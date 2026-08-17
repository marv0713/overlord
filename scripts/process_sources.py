#!/usr/bin/env python3
"""Process latest eligible items from configured sources (YouTube channels and Xiaoyuzhou podcasts)."""
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from youtube_to_wechat.output import write_article, write_outputs
from youtube_to_wechat.processed_store import BaseProcessedStore, create_store, slugify_source_name
from youtube_to_wechat.source_config import SourceConfig, load_source_config
from youtube_to_wechat.transcriber import FasterWhisperTranscriber, TranscriberError
from youtube_to_wechat.writer import GeminiWriter, WriterError
from youtube_to_wechat.writer_profiles import load_writer_profile
from youtube_to_wechat.xiaoyuzhou import (
    PodcastEpisode,
    XiaoyuzhouError,
    download_episode_audio,
    fetch_podcast_episodes,
    select_eligible_episodes,
)
from youtube_to_wechat.youtube_channel import (
    ChannelVideo,
    fetch_channel_videos,
    select_eligible_videos_for_source,
)
from youtube_to_wechat.youtube_meta import extract_video_id
from youtube_to_wechat.ytdlp import YtDlpError, download_audio, fetch_info, fetch_transcript


@dataclass
class SourceCandidate:
    source: SourceConfig
    source_slug: str
    # One of the two will be set depending on source type
    video: ChannelVideo | None = None
    episode: PodcastEpisode | None = None

    @property
    def item_id(self) -> str:
        return self.video.video_id if self.video else (self.episode.episode_id if self.episode else "")

    @property
    def item_title(self) -> str:
        return self.video.title if self.video else (self.episode.title if self.episode else "")

    @property
    def item_published_at(self) -> str:
        return self.video.published_at if self.video else (self.episode.published_at if self.episode else "")


def process_video_url(
    url: str,
    output_base: Path,
    no_check_certificates: bool = False,
    skip_audio: bool = False,
    generate_article: bool = False,
    writer_profile: str = "deep-stock-analysis",
    writer_profile_dir: Path = Path("config/writer_profiles"),
    gemini_model: str = "gemini-2.5-flash",
    model_size: str = "base",
    language: str = "zh",
    issue: str = "",
) -> tuple[Path, dict]:
    video_id = extract_video_id(url)
    meta = fetch_info(url, no_check_certificates=no_check_certificates)
    meta.setdefault("video_id", video_id)
    meta.setdefault("url", url)
    if issue:
        meta["issue"] = issue

    run: dict = {
        "status": "ok",
        "transcript_status": "pending",
        "audio_status": "skipped",
        "article_status": "skipped",
        "writer_profile": writer_profile,
    }
    if issue:
        run["issue"] = issue
    try:
        transcript = fetch_transcript(
            url,
            output_base / video_id / "_subs",
            no_check_certificates=no_check_certificates,
        )
        run["transcript_status"] = "ok"
    except YtDlpError as exc:
        transcript = ""
        run["transcript_status"] = "error"
        run["transcript_error"] = str(exc)

    if not skip_audio:
        try:
            audio_path = download_audio(
                url,
                output_base / video_id / "audio",
                no_check_certificates=no_check_certificates,
            )
            run["audio_status"] = "ok"
            run["audio_path"] = str(audio_path) if audio_path else ""
        except YtDlpError as exc:
            run["audio_status"] = "error"
            run["audio_error"] = str(exc)

    if run["transcript_status"] != "ok" and run["audio_status"] != "ok":
        run["status"] = "error"
    elif run["transcript_status"] != "ok" or run["audio_status"] != "ok":
        run["status"] = "partial"

    # --- Whisper Fallback ---
    if not transcript and run.get("audio_path"):
        print(f"[{video_id}] No subtitle found, falling back to Whisper transcription...")
        from youtube_to_wechat.transcriber import FasterWhisperTranscriber, TranscriberError
        try:
            transcriber = FasterWhisperTranscriber(model_size=model_size, language=language if language else None)
            transcript = transcriber.transcribe(Path(run["audio_path"]))
            run["transcript_status"] = "ok (whisper fallback)"
            # Update status since we now have transcript
            run["status"] = "ok" if run["audio_status"] == "ok" else "partial"
        except TranscriberError as exc:
            run["transcript_status"] = "error"
            run["transcript_error"] = f"Whisper fallback failed: {exc}"

    output_dir = write_outputs(output_base, video_id, meta, transcript, run=run)
    if generate_article and transcript:
        try:
            profile = load_writer_profile(writer_profile, writer_profile_dir)
            article, used_model = _write_article_with_model_fallback(
                transcript=transcript,
                meta=meta,
                profile_name=profile.name,
                profile_prompt=profile.prompt,
                gemini_model=gemini_model,
            )
            write_article(output_base, video_id, article.markdown)
            run["article_status"] = "ok"
            run["article_title"] = article.title
            run["gemini_model"] = used_model
        except (OSError, WriterError) as exc:
            run["article_status"] = "error"
            run["article_error"] = str(exc)
            if run["status"] == "ok":
                run["status"] = "partial"
        (output_dir / "run.json").write_text(
            json.dumps(run, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return output_dir, run


def collect_source_candidates(
    sources: list[SourceConfig],
    store: BaseProcessedStore,
    channel_limit: int,
    no_check_certificates: bool,
) -> list[SourceCandidate]:
    candidates: list[SourceCandidate] = []
    for source in sources:
        if not source.enabled:
            print(f"[skip] {source.name}: disabled")
            continue

        source_slug = slugify_source_name(source.name)
        source_seen_before = store.has_source(source_slug)

        if source.type == "youtube_channel":
            videos = fetch_channel_videos(
                source.url,
                limit=channel_limit,
                no_check_certificates=no_check_certificates,
            )
            store.record_source_scan(source_name=source.name, source_slug=source_slug, videos=videos)
            selected_videos = select_eligible_videos_for_source(
                videos,
                min_duration_seconds=source.min_duration_seconds,
                processed_video_ids=store.processed_video_ids(),
                source_seen_before=source_seen_before,
                stop_at_video_ids=store.processed_video_ids_for_source(source_slug),
            )
            if not selected_videos:
                print(f"[skip] {source.name}: no new long video found")
                continue
            for video in selected_videos:
                candidates.append(SourceCandidate(source=source, source_slug=source_slug, video=video))

        elif source.type == "podcast_rss":
            rss_url = source.rss_url or source.url
            if not rss_url:
                print(f"[skip] {source.name}: no rss_url configured")
                continue
            try:
                episodes = fetch_podcast_episodes(
                    rss_url,
                    limit=channel_limit,
                    no_check_certificates=no_check_certificates,
                )
            except XiaoyuzhouError as exc:
                print(f"[skip] {source.name}: RSS fetch failed: {exc}")
                continue
            selected_episodes = select_eligible_episodes(
                episodes,
                min_duration_seconds=source.min_duration_seconds,
                processed_episode_ids=store.processed_video_ids(),
                source_seen_before=source_seen_before,
            )
            if not selected_episodes:
                print(f"[skip] {source.name}: no new long episode found")
                continue
            for ep in selected_episodes:
                candidates.append(SourceCandidate(source=source, source_slug=source_slug, episode=ep))

        else:
            print(f"[skip] {source.name}: unsupported source type {source.type}")
            continue

    return sorted(
        candidates,
        key=lambda c: (c.source.priority, c.item_published_at or "9999-12-31"),
    )


def _should_push_output(output_dir: Path) -> bool:
    article_path = output_dir / "article.md"
    run_json_path = output_dir / "run.json"
    if not article_path.exists() or not run_json_path.exists():
        return False
    try:
        run = json.loads(run_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return run.get("article_status") == "ok"


def _resolve_writer_profile(configured_profile: str, title: str) -> str:
    """Resolve the Intrinsic Value channel's per-video writing structure."""
    if configured_profile != "intrinsic-value-auto":
        return configured_profile

    normalized = " ".join(title.split())
    topic = re.split(r"\s+[–—-]\s+|[?:：]", normalized, maxsplit=1)[0]
    if topic.count(",") >= 1:
        return "interview"
    if re.search(r"\b(?:[2-9]|\d{2,})\s+stocks?\b", normalized, re.IGNORECASE):
        return "interview"
    if re.search(r"\b(?:stocks|companies)\s+(?:to|that|for|built)\b", normalized, re.IGNORECASE):
        return "interview"
    return "deep-stock-analysis"


def _build_cover_text(
    raw_title: str = "",
    issue: str = "",
    source_name: str = "",
    title: str | None = None,
) -> tuple[str, str]:
    """Return a short, mobile-readable (headline, hook) pair for the cover."""
    if title is not None:
        raw_title = title
    title = raw_title.replace("**", "").strip()
    if "|" in title:
        title = title.split("|", 1)[1].strip()
    for sep in ("：", ":", "——", " - ", "？", "?"):
        if sep in title:
            head, tail = title.split(sep, 1)
            if 4 <= len(head.strip()) <= 24:
                return _trim_cover_text(head.strip(), 20), _trim_cover_text(tail.strip(), 22)

    words = title.split()
    if words and words[0].isupper() and len(words[0]) <= 6:
        head = " ".join(words[:2])
        tail = " ".join(words[2:])
        return _trim_cover_text(head, 20), _trim_cover_text(tail, 22)

    return _trim_cover_text(title, 20), ""


def _trim_cover_text(text: str, max_chars: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def process_candidate(
    candidate: SourceCandidate,
    store: BaseProcessedStore,
    output_base: Path,
    no_check_certificates: bool,
    dry_run: bool,
    generate_article: bool = False,
    writer_profile_dir: Path = Path("config/writer_profiles"),
    gemini_model: str = "gemini-2.5-flash",
    model_size: str = "base",
    language: str = "zh",
) -> int:
    source = candidate.source
    print(f"[pick] {source.name}: {candidate.item_title}")
    if dry_run:
        return 0

    writer_profile = _resolve_writer_profile(source.writer_profile, candidate.item_title)
    if writer_profile != source.writer_profile:
        print(f"[profile] {source.name}: {writer_profile}")

    tentative_issue = store.preview_next_issue(source.series) if source.series else ""

    if candidate.video is not None:
        output_dir, run = process_video_url(
            candidate.video.url,
            output_base=output_base / candidate.source_slug,
            no_check_certificates=no_check_certificates,
            skip_audio=source.compare_evaluation == "none",
            generate_article=generate_article,
            writer_profile=writer_profile,
            writer_profile_dir=writer_profile_dir,
            gemini_model=gemini_model,
            model_size=model_size,
            language=language,
            issue=tentative_issue,
        )
        item_id = candidate.video.video_id
        item_url = candidate.video.url
        item_title = candidate.video.title
    else:
        ep = candidate.episode
        output_dir, run = process_episode(
            ep,
            source_slug=candidate.source_slug,
            output_base=output_base / candidate.source_slug,
            no_check_certificates=no_check_certificates,
            generate_article=generate_article,
            writer_profile=writer_profile,
            writer_profile_dir=writer_profile_dir,
            gemini_model=gemini_model,
            model_size=model_size,
            language=language,
            issue=tentative_issue,
        )
        item_id = ep.episode_id
        item_url = ep.episode_url
        item_title = ep.title

    if run["status"] != "error":
        issue = store.allocate_issue(source.series)
        store.mark_processed(
            item_id,
            source_name=source.name,
            source_slug=candidate.source_slug,
            title=item_title,
            url=item_url,
            output_dir=str(output_dir),
            status=run["status"],
            series=source.series,
            issue=issue,
            writer_profile=writer_profile,
        )
        if issue:
            print(f"[issue] {source.series}: {issue}")
    print(f"[done] {source.name}: {run['status']} -> {output_dir}")
    return 1


def process_episode(
    ep: PodcastEpisode,
    source_slug: str,
    output_base: Path,
    no_check_certificates: bool = False,
    generate_article: bool = False,
    writer_profile: str = "alchemy-research",
    writer_profile_dir: Path = Path("config/writer_profiles"),
    gemini_model: str = "gemini-2.5-flash",
    model_size: str = "base",
    language: str = "zh",
    issue: str = "",
) -> tuple[Path, dict]:
    """Download, transcribe, and optionally write an article for one podcast episode."""
    output_dir = output_base / ep.episode_id
    output_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "episode_id": ep.episode_id,
        "title": ep.title,
        "podcast_name": ep.podcast_name,
        "episode_url": ep.episode_url,
        "audio_url": ep.audio_url,
        "duration_seconds": ep.duration_seconds,
        "published_at": ep.published_at,
        "description": ep.description,
        "source": "podcast_rss",
    }
    (output_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    run: dict = {
        "status": "ok",
        "transcript_status": "pending",
        "article_status": "skipped",
        "writer_profile": writer_profile,
    }
    if issue:
        run["issue"] = issue

    # Download audio
    try:
        audio_path = download_episode_audio(
            ep.audio_url,
            output_dir / "audio",
            no_check_certificates=no_check_certificates,
        )
        audio_size = audio_path.stat().st_size
        run["audio_status"] = "ok"
        run["audio_path"] = str(audio_path)
        run["audio_size_bytes"] = audio_size
        run["audio_size_mb"] = round(audio_size / 1024 / 1024, 1)
    except XiaoyuzhouError as exc:
        run["transcript_status"] = "error"
        run["transcript_error"] = str(exc)
        run["status"] = "error"
        (output_dir / "run.json").write_text(
            json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return output_dir, run

    # Transcribe
    transcriber = FasterWhisperTranscriber(model_size=model_size, language=language or None)
    try:
        transcript = transcriber.transcribe(audio_path)
        run["transcript_status"] = "ok"
    except TranscriberError as exc:
        run["transcript_status"] = "error"
        run["transcript_error"] = str(exc)
        run["status"] = "partial"
        transcript = ep.description  # fall back to RSS description

    (output_dir / "transcript.txt").write_text(
        f"【节目简介】\n{ep.description}\n\n【音频转写】\n{transcript}\n",
        encoding="utf-8",
    )
    # Full transcript for Gemini = show notes + audio transcription
    full_transcript = f"节目简介：\n{ep.description}\n\n音频内容转写：\n{transcript}"

    # Generate article
    if generate_article and transcript:
        try:
            profile = load_writer_profile(writer_profile, writer_profile_dir)
            article, used_model = _write_article_with_model_fallback(
                transcript=full_transcript,
                meta=meta,
                profile_name=profile.name,
                profile_prompt=profile.prompt,
                gemini_model=gemini_model,
            )
            write_article(output_base, ep.episode_id, article.markdown)
            run["article_status"] = "ok"
            run["article_title"] = article.title
            run["gemini_model"] = used_model
        except (OSError, WriterError) as exc:
            run["article_status"] = "error"
            run["article_error"] = str(exc)
            if run["status"] == "ok":
                run["status"] = "partial"

    (output_dir / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output_dir, run


def _write_article_with_model_fallback(
    transcript: str,
    meta: dict,
    profile_name: str,
    profile_prompt: str,
    gemini_model: str,
):
    """Write with the first available model.

    ``gemini_model`` may be a comma-separated list, e.g.
    ``gemini-flash-latest,gemini-2.5-pro``. This keeps the cron on the latest
    model first, while allowing recovery from transient Gemini capacity errors.
    """
    models = [item.strip() for item in gemini_model.split(",") if item.strip()]
    if not models:
        models = ["gemini-2.5-flash"]

    last_error: WriterError | None = None
    for model in models:
        writer = GeminiWriter(
            model=model,
            profile_name=profile_name,
            profile_prompt=profile_prompt,
        )
        try:
            return writer.write(transcript, meta), model
        except WriterError as exc:
            last_error = exc
            if not _is_retryable_model_error(exc):
                raise
            print(f"[writer] {model} failed with retryable error; trying next model: {exc}")

    if last_error:
        raise last_error
    raise WriterError("No Gemini model configured")


def _is_retryable_model_error(exc: WriterError) -> bool:
    message = str(exc)
    return any(token in message for token in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED"))


def process_source(
    source: SourceConfig,
    store: BaseProcessedStore,
    output_base: Path,
    channel_limit: int,
    no_check_certificates: bool,
    dry_run: bool,
    generate_article: bool = False,
    writer_profile_dir: Path = Path("config/writer_profiles"),
    gemini_model: str = "gemini-2.5-flash",
) -> int:
    if not source.enabled:
        print(f"[skip] {source.name}: disabled")
        return 0
    if source.type != "youtube_channel":
        print(f"[skip] {source.name}: unsupported source type {source.type}")
        return 0

    source_slug = slugify_source_name(source.name)
    source_seen_before = store.has_source(source_slug)
    videos = fetch_channel_videos(
        source.url,
        limit=channel_limit,
        no_check_certificates=no_check_certificates,
    )
    store.record_source_scan(source_name=source.name, source_slug=source_slug, videos=videos)
    selected_videos = select_eligible_videos_for_source(
        videos,
        min_duration_seconds=source.min_duration_seconds,
        processed_video_ids=store.processed_video_ids(),
        source_seen_before=source_seen_before,
        stop_at_video_ids=store.processed_video_ids_for_source(source_slug),
    )
    selected = selected_videos[0] if selected_videos else None
    if selected is None:
        print(f"[skip] {source.name}: no new long video found")
        return 0

    print(f"[pick] {source.name}: {selected.title} ({selected.duration_seconds}s)")
    print(f"       {selected.url}")
    if dry_run:
        return 0

    output_dir, run = process_video_url(
        selected.url,
        output_base=output_base / source_slug,
        no_check_certificates=no_check_certificates,
        skip_audio=source.compare_evaluation == "none",
        generate_article=generate_article,
        writer_profile=source.writer_profile,
        writer_profile_dir=writer_profile_dir,
        gemini_model=gemini_model,
    )
    if run["status"] != "error":
        issue = store.allocate_issue(source.series)
        store.mark_processed(
            selected.video_id,
            source_name=source.name,
            source_slug=source_slug,
            title=selected.title,
            url=selected.url,
            output_dir=str(output_dir),
            status=run["status"],
            series=source.series,
            issue=issue,
            writer_profile=source.writer_profile,
        )
        if issue:
            print(f"[issue] {source.series}: {issue}")
    print(f"[done] {source.name}: {run['status']} -> {output_dir}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Process latest long videos from configured sources.")
    parser.add_argument("--config", default="config/sources.json", help="Source config JSON path")
    parser.add_argument("--store", default="data/processed.json", help="Processed video store path")
    parser.add_argument("--output-dir", default="outputs/youtube", help="Base output directory")
    parser.add_argument("--channel-limit", type=int, default=12, help="Videos to inspect per channel")
    parser.add_argument("--max-items", type=int, default=1, help="Maximum queued videos to process this run")
    parser.add_argument("--generate-article", action="store_true", help="Generate article.md using each source writer_profile")
    parser.add_argument("--writer-profile-dir", default="config/writer_profiles", help="Directory containing writer profile Markdown files")
    parser.add_argument("--gemini-model", default="gemini-2.5-flash", help="Gemini model used when --generate-article is set")
    parser.add_argument("--push", action="store_true", help="Push generated article to configured destinations")
    parser.add_argument("--cover", default="", help="Path to default cover image for destinations requiring it")
    parser.add_argument("--env", default=".env", help="Path to .env file for WeChat credentials")
    parser.add_argument("--dry-run", action="store_true", help="Print selected videos without processing")
    parser.add_argument(
        "--no-check-certificates",
        action="store_true",
        help="Pass --no-check-certificates to yt-dlp.",
    )
    args = parser.parse_args()

    # Load environment variables into os.environ globally (for GeminiWriter etc)
    from youtube_to_wechat.wechat import load_env
    import os
    env_vars = load_env(Path(args.env))
    if env_vars:
        os.environ.update(env_vars)

    try:
        config = load_source_config(Path(args.config))
        db_url = os.environ.get("SUPABASE_DB_URL")
        store = create_store(args.store, db_url)
        processed_count = 0
        candidates = collect_source_candidates(
            sources=config.sources,
            store=store,
            channel_limit=args.channel_limit,
            no_check_certificates=args.no_check_certificates,
        )
        for candidate in candidates[: args.max_items]:
            processed_count += 1
            output_dir = None
            if candidate.video is not None:
                output_dir = Path(args.output_dir) / candidate.source_slug / candidate.video.video_id
            else:
                output_dir = Path(args.output_dir) / candidate.source_slug / candidate.episode.episode_id
                
            process_candidate(
                candidate=candidate,
                store=store,
                output_base=Path(args.output_dir),
                no_check_certificates=args.no_check_certificates,
                dry_run=args.dry_run,
                generate_article=args.generate_article,
                writer_profile_dir=Path(args.writer_profile_dir),
                gemini_model=args.gemini_model,
            )

            # Check for task failure and send alert via WeChat
            if not args.dry_run and output_dir is not None:
                _run_json_path = output_dir / "run.json"
                if _run_json_path.exists():
                    import json as _json2
                    try:
                        _run = _json2.loads(_run_json_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        _run = {}
                    _status = _run.get("status", "")
                    # Collect concrete error fields (not just skipped steps)
                    _errors = []
                    for _key in ("transcript_error", "audio_error", "article_error"):
                        if _run.get(_key):
                            _errors.append(f"{_key}: {_run[_key]}")
                    # Only alert on real failures: status=error, or partial with actual errors
                    if _status == "error" or (_errors and _status == "partial"):
                        _error_msg = "; ".join(_errors)
                        from youtube_to_wechat.publish import send_failure_alert
                        send_failure_alert(
                            source_name=candidate.source.name,
                            error_message=_error_msg,
                            item_title=candidate.item_title,
                            issue=_run.get("issue", ""),
                            env=load_env(Path(args.env)),
                        )
            
            # If successfully generated an article and push is requested
            if args.push and _should_push_output(output_dir):
                cover_path = Path(args.cover) if args.cover else output_dir / "cover.png"
                run_json_path = output_dir / "run.json"
                
                # Auto-generate cover if missing (some publishers require it, e.g. WechatDraft)
                if not cover_path.exists():
                    print(f"[{candidate.source.name}] Auto-generating default cover image...")
                    import sys as _sys
                    if str(Path("scripts").absolute()) not in _sys.path and "scripts" not in _sys.path:
                        _sys.path.append("scripts")
                    from generate_cover import generate_cover
                    import re as _re
                    import json as _json

                    article_path = output_dir / "article.md"
                    text = article_path.read_text(encoding="utf-8")
                    title_match = _re.search(r"^#\s+(.+)$", text, _re.MULTILINE)
                    title = title_match.group(1).strip() if title_match else candidate.item_title
                    if not title:
                        title = "最新研报"

                    _run_data = _json.loads(run_json_path.read_text(encoding="utf-8")) if run_json_path.exists() else {}
                    # Use the generated article title as the cover headline; source
                    # titles are often long English strings that become unreadable
                    # in WeChat's small list thumbnails.
                    ticker_text, hook_text = _build_cover_text(
                        raw_title=title,
                        issue=_run_data.get("issue", ""),
                    )
                    generate_cover(
                        output=cover_path,
                        column="炼金投研",
                        ticker=ticker_text,
                        hook=hook_text,
                        issue=_run_data.get("issue", "")
                    )
                
                # Dispatch to all configured destinations
                import json as _json
                from youtube_to_wechat.publish import publish_article
                
                env = load_env(Path(args.env))
                _run_data = _json.loads(run_json_path.read_text(encoding="utf-8")) if run_json_path.exists() else {}
                issue_val = _run_data.get("issue", "")
                
                for destination in candidate.source.destinations:
                    print(f"[{candidate.source.name}] Dispatching to destination: {destination}...")
                    publish_article(
                        destination=destination,
                        source_name=candidate.source.name,
                        issue=issue_val,
                        article_path=output_dir / "article.md",
                        cover_path=cover_path,
                        env=env,
                    )

    except (OSError, ValueError, YtDlpError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        try:
            from youtube_to_wechat.publish import send_failure_alert
            send_failure_alert(
                source_name="process_sources",
                error_message=f"Fatal error: {exc}",
                env=load_env(Path(args.env)) if Path(args.env).exists() else {},
            )
        except Exception:
            pass
        return 1

    print(f"Processed {processed_count} source item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
