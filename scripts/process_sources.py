#!/usr/bin/env python3
"""Process latest eligible items from configured sources (YouTube channels and Xiaoyuzhou podcasts)."""
import argparse
import json
import os
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
            writer = GeminiWriter(
                model=gemini_model,
                profile_name=profile.name,
                profile_prompt=profile.prompt,
            )
            article = writer.write(transcript, meta)
            write_article(output_base, video_id, article.markdown)
            run["article_status"] = "ok"
            run["article_title"] = article.title
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


def _build_cover_text(source_name: str, title: str, issue: str = "") -> tuple[str, str]:
    """Return (ticker, hook) suitable for the cover image.

    ticker — short source / channel label (e.g. "Sven Carlin")
    hook   — a compact teaser from the article title, at most 24 chars.
             Long titles are truncated with '…' so the cover stays clean.
    """
    cleaned = title.strip()
    for prefix in ("炼金投研｜", "炼金投研 |", "炼金投研：", "炼金投研:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    if issue:
        for prefix in (f"{issue} |", f"{issue}｜", f"{issue}:"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
    hook = cleaned or title.strip() or "最新研报"
    # Keep the hook concise so it fits on the cover (≤24 chars)
    MAX_HOOK_LEN = 24
    if len(hook) > MAX_HOOK_LEN:
        hook = hook[:MAX_HOOK_LEN].rstrip() + "…"
    return source_name.strip(), hook


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

    tentative_issue = store.preview_next_issue(source.series) if source.series else ""

    if candidate.video is not None:
        output_dir, run = process_video_url(
            candidate.video.url,
            output_base=output_base / candidate.source_slug,
            no_check_certificates=no_check_certificates,
            skip_audio=source.compare_evaluation == "none",
            generate_article=generate_article,
            writer_profile=source.writer_profile,
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
            writer_profile=source.writer_profile,
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
            writer_profile=source.writer_profile,
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
            writer = GeminiWriter(
                model=gemini_model,
                profile_name=profile.name,
                profile_prompt=profile.prompt,
            )
            article = writer.write(full_transcript, meta)
            write_article(output_base, ep.episode_id, article.markdown)
            run["article_status"] = "ok"
            run["article_title"] = article.title
        except (OSError, WriterError) as exc:
            run["article_status"] = "error"
            run["article_error"] = str(exc)
            if run["status"] == "ok":
                run["status"] = "partial"

    (output_dir / "run.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output_dir, run


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
                    ticker_text, hook_text = _build_cover_text(
                        source_name=candidate.source.name,
                        title=title,
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
        return 1

    print(f"Processed {processed_count} source item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
