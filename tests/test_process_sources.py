import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.process_sources import (
    SourceCandidate,
    _build_cover_text,
    _build_publish_context,
    _dispatch_article,
    _record_publish_result,
    _resolve_writer_profile,
    _should_push_output,
    collect_source_candidates,
    process_candidate,
    process_episode,
    process_source,
    process_video_url,
)
from youtube_to_wechat.publish import PublishResult
from youtube_to_wechat.processed_store import ProcessedStore
from youtube_to_wechat.source_config import SourceConfig
from youtube_to_wechat.xiaoyuzhou import PodcastEpisode
from youtube_to_wechat.youtube_channel import ChannelVideo


class ProcessSourcesTests(unittest.TestCase):
    def test_record_publish_result_preserves_run_fields_and_persists_exact_result(self):
        result = PublishResult(
            action="mass_send",
            media_id="media-123",
            task_id="task-456",
            retried=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            run_json_path = Path(temp_dir) / "run.json"
            run_json_path.write_text(
                '{\n  "status": "ok",\n  "issue": "No.001"\n}\n',
                encoding="utf-8",
            )

            _record_publish_result(run_json_path, result)

            self.assertEqual(
                json.loads(run_json_path.read_text(encoding="utf-8")),
                {
                    "status": "ok",
                    "issue": "No.001",
                    "wechat_publish": result.to_dict(),
                },
            )
            self.assertTrue(run_json_path.read_text(encoding="utf-8").endswith("\n"))

    def test_record_publish_result_noops_for_none_missing_or_invalid_run_json(self):
        result = PublishResult(action="draft", media_id="media-123")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            missing_path = temp_path / "missing.json"
            _record_publish_result(missing_path, result)
            self.assertFalse(missing_path.exists())

            invalid_path = temp_path / "invalid.json"
            invalid_path.write_text("not json", encoding="utf-8")
            _record_publish_result(invalid_path, result)
            self.assertEqual(invalid_path.read_text(encoding="utf-8"), "not json")

            valid_path = temp_path / "valid.json"
            valid_path.write_text('{"status": "ok"}\n', encoding="utf-8")
            _record_publish_result(valid_path, None)
            self.assertEqual(valid_path.read_text(encoding="utf-8"), '{"status": "ok"}\n')

    def test_record_publish_result_none_does_not_overwrite_existing_wechat_outcome(self):
        existing = {"action": "mass_send", "media_id": "already-sent"}
        with tempfile.TemporaryDirectory() as temp_dir:
            run_json_path = Path(temp_dir) / "run.json"
            run_json_path.write_text(
                json.dumps({"status": "ok", "wechat_publish": existing}) + "\n",
                encoding="utf-8",
            )

            _record_publish_result(run_json_path, None)

            self.assertEqual(json.loads(run_json_path.read_text(encoding="utf-8"))["wechat_publish"], existing)

    def test_build_publish_context_defaults_to_mass_send_enabled_and_honors_false(self):
        self.assertTrue(_build_publish_context({}).wechat_batch.mass_send_enabled)
        self.assertFalse(_build_publish_context({"WECHAT_MASS_SEND": "false"}).wechat_batch.mass_send_enabled)

    @patch("scripts.process_sources.publish_article")
    def test_dispatch_article_reuses_the_supplied_context_across_candidates(self, publish_article):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            first_run = temp_path / "first.json"
            second_run = temp_path / "second.json"
            first_run.write_text('{"status": "ok"}\n', encoding="utf-8")
            second_run.write_text('{"status": "ok"}\n', encoding="utf-8")
            context = _build_publish_context({})
            publish_article.return_value = None

            _dispatch_article(
                destinations=["email"],
                source_name="First",
                issue="No.001",
                article_path=temp_path / "first.md",
                cover_path=temp_path / "first.png",
                env={},
                context=context,
                run_json_path=first_run,
            )
            _dispatch_article(
                destinations=["pushplus"],
                source_name="Second",
                issue="No.002",
                article_path=temp_path / "second.md",
                cover_path=temp_path / "second.png",
                env={},
                context=context,
                run_json_path=second_run,
            )

        self.assertEqual(publish_article.call_count, 2)
        self.assertIs(publish_article.call_args_list[0].kwargs["context"], context)
        self.assertIs(publish_article.call_args_list[1].kwargs["context"], context)
    def test_auto_profile_routes_explicit_company_list_to_interview(self):
        self.assertEqual(
            _resolve_writer_profile(
                "intrinsic-value-auto",
                "Google, Reddit, Amazon, TSMC – Are our Biggest Winners Still a Buy Now?",
            ),
            "interview",
        )

    def test_auto_profile_keeps_one_main_company_with_competitor_as_deep_stock(self):
        self.assertEqual(
            _resolve_writer_profile(
                "intrinsic-value-auto",
                "How Grab Beat Uber: Inside the Rise of Southeast Asia's Super App",
            ),
            "deep-stock-analysis",
        )

    def test_auto_profile_routes_plural_stock_scan_to_interview(self):
        self.assertEqual(
            _resolve_writer_profile(
                "intrinsic-value-auto",
                "5 Stocks: Compounders Built to Beat the Market",
            ),
            "interview",
        )

    def test_explicit_profile_is_unchanged(self):
        self.assertEqual(
            _resolve_writer_profile("dehydrate", "Adobe, PayPal and Intuit"),
            "dehydrate",
        )

    @patch("scripts.process_sources.write_article")
    @patch("scripts.process_sources.GeminiWriter")
    @patch("scripts.process_sources.load_writer_profile")
    @patch("scripts.process_sources.fetch_transcript")
    @patch("scripts.process_sources.fetch_info")
    def test_process_video_url_can_generate_article_with_source_writer_profile(
        self,
        fetch_info,
        fetch_transcript,
        load_profile,
        writer_cls,
        write_article,
    ):
        fetch_info.return_value = {"title": "Demo Video", "url": "https://www.youtube.com/watch?v=abc123"}
        fetch_transcript.return_value = "Transcript body"
        load_profile.return_value.name = "market-commentary"
        load_profile.return_value.prompt = "Market prompt"
        writer = writer_cls.return_value
        writer.write.return_value.markdown = "# Generated Article"
        writer.write.return_value.title = "Generated Article"

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir, run = process_video_url(
                url="https://www.youtube.com/watch?v=abc123",
                output_base=Path(temp_dir),
                skip_audio=True,
                generate_article=True,
                writer_profile="market-commentary",
                writer_profile_dir=Path("config/writer_profiles"),
            )

        self.assertEqual(run["article_status"], "ok")
        self.assertEqual(output_dir.name, "abc123")
        load_profile.assert_called_once_with("market-commentary", Path("config/writer_profiles"))
        writer_cls.assert_called_once()
        writer.write.assert_called_once_with("Transcript body", fetch_info.return_value)
        write_article.assert_called_once()

    @patch("scripts.process_sources.fetch_channel_videos")
    def test_collect_candidates_sorts_by_source_priority_then_published_at(self, fetch_videos):
        high_priority = SourceConfig(
            type="youtube_channel",
            name="High Priority",
            url="https://www.youtube.com/@high",
            priority=10,
        )
        low_priority = SourceConfig(
            type="youtube_channel",
            name="Low Priority",
            url="https://www.youtube.com/@low",
            priority=50,
        )

        def videos_for(url, limit, no_check_certificates):
            if "high" in url:
                return [
                    ChannelVideo(
                        video_id="high-new",
                        title="High New",
                        url="https://www.youtube.com/watch?v=high-new",
                        duration_seconds=900,
                        published_at="2026-05-17",
                    ),
                    ChannelVideo(
                        video_id="high-old",
                        title="High Old",
                        url="https://www.youtube.com/watch?v=high-old",
                        duration_seconds=900,
                        published_at="2026-05-10",
                    ),
                ]
            return [
                ChannelVideo(
                    video_id="low-old",
                    title="Low Old",
                    url="https://www.youtube.com/watch?v=low-old",
                    duration_seconds=900,
                    published_at="2026-05-01",
                )
            ]

        fetch_videos.side_effect = videos_for
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProcessedStore(Path(temp_dir) / "processed.json")
            store.record_source_scan("High Priority", "high-priority", [])
            store.record_source_scan("Low Priority", "low-priority", [])

            candidates = collect_source_candidates(
                sources=[low_priority, high_priority],
                store=store,
                channel_limit=5,
                no_check_certificates=False,
            )

        self.assertEqual(
            [candidate.video.video_id for candidate in candidates],
            ["high-old", "high-new", "low-old"],
        )

    @patch("scripts.process_sources.process_video_url")
    @patch("scripts.process_sources.fetch_channel_videos")
    def test_process_source_uses_source_slug_output_dir_and_records_scan(self, fetch_videos, process_video):
        source = SourceConfig(
            type="youtube_channel",
            name="Demo Channel",
            url="https://www.youtube.com/@demo",
        )
        video = ChannelVideo(
            video_id="abc123",
            title="Demo Long Video",
            url="https://www.youtube.com/watch?v=abc123",
            duration_seconds=900,
            published_at="2026-05-17",
        )
        fetch_videos.return_value = [video]
        process_video.return_value = (
            Path("outputs/youtube/demo-channel/abc123"),
            {"status": "ok"},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ProcessedStore(Path(temp_dir) / "processed.json")

            processed = process_source(
                source=source,
                store=store,
                output_base=Path("outputs/youtube"),
                channel_limit=5,
                no_check_certificates=False,
                dry_run=False,
            )

        self.assertEqual(processed, 1)
        process_video.assert_called_once()
        self.assertEqual(process_video.call_args.kwargs["output_base"], Path("outputs/youtube/demo-channel"))
        self.assertTrue(store.is_processed("abc123"))
        self.assertEqual(store.source_record("demo-channel")["latest_videos"][0]["video_id"], "abc123")
        self.assertEqual(store.processing_record("abc123")["writer_profile"], "alchemy-research")

    def test_should_push_only_when_generated_article_succeeded(self):
        self.assertFalse(_should_push_output(Path("missing")))

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "article.md").write_text("# Placeholder\n", encoding="utf-8")
            (output_dir / "run.json").write_text('{"article_status": "skipped"}', encoding="utf-8")

            self.assertFalse(_should_push_output(output_dir))

            (output_dir / "run.json").write_text('{"article_status": "ok"}', encoding="utf-8")
            self.assertTrue(_should_push_output(output_dir))

    @patch("scripts.process_sources.write_article")
    @patch("scripts.process_sources.GeminiWriter")
    @patch("scripts.process_sources.load_writer_profile")
    @patch("scripts.process_sources.FasterWhisperTranscriber")
    @patch("scripts.process_sources.download_episode_audio")
    def test_process_episode_writes_article_in_episode_output_dir(
        self,
        download_audio,
        transcriber_cls,
        load_profile,
        writer_cls,
        write_article,
    ):
        ep = PodcastEpisode(
            episode_id="ep123",
            title="Demo Episode",
            description="Show notes",
            audio_url="https://example.com/audio.mp3",
            duration_seconds=900,
            published_at="2026-05-20",
            episode_url="https://example.com/episode",
            podcast_name="Demo Podcast",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "audio.mp3"
            audio_path.write_bytes(b"demo")
            download_audio.return_value = audio_path
            transcriber_cls.return_value.transcribe.return_value = "Transcript body"
            load_profile.return_value.name = "market-commentary"
            load_profile.return_value.prompt = "Market prompt"
            writer_cls.return_value.write.return_value.markdown = "# Generated"
            writer_cls.return_value.write.return_value.title = "Generated"

            output_dir, run = process_episode(
                ep,
                source_slug="demo-podcast",
                output_base=Path(temp_dir) / "demo-podcast",
                generate_article=True,
            )

        self.assertEqual(run["article_status"], "ok")
        self.assertEqual(output_dir, Path(temp_dir) / "demo-podcast" / "ep123")
        write_article.assert_called_once()
        self.assertEqual(write_article.call_args.args[0], Path(temp_dir) / "demo-podcast")
        self.assertEqual(write_article.call_args.args[1], "ep123")

    @patch("scripts.process_sources.process_video_url")
    def test_process_candidate_does_not_require_private_store_data(self, process_video):
        class MinimalStore:
            def is_processed(self, video_id): return False
            def processed_video_ids(self): return set()
            def processed_video_ids_for_source(self, source_slug): return set()
            def has_source(self, source_slug): return False
            def allocate_issue(self, series): return "No.001"
            def preview_next_issue(self, series): return "No.001"
            def get_current_issue(self, series): return "No.001"
            def processing_record(self, video_id): return {}
            def source_record(self, source_slug): return {}
            def record_source_scan(self, source_name, source_slug, videos): pass
            def mark_processed(self, *args, **kwargs): pass

        source = SourceConfig(
            type="youtube_channel",
            name="Demo Channel",
            url="https://www.youtube.com/@demo",
        )
        video = ChannelVideo(
            video_id="abc123",
            title="Demo Long Video",
            url="https://www.youtube.com/watch?v=abc123",
            duration_seconds=900,
        )
        process_video.return_value = (Path("outputs/youtube/demo-channel/abc123"), {"status": "ok"})

        processed = process_candidate(
            candidate=SourceCandidate(source=source, source_slug="demo-channel", video=video),
            store=MinimalStore(),
            output_base=Path("outputs/youtube"),
            no_check_certificates=False,
            dry_run=False,
        )

        self.assertEqual(processed, 1)
        self.assertEqual(process_video.call_args.kwargs["issue"], "No.001")

    @patch("scripts.process_sources.process_video_url")
    def test_process_candidate_resolves_auto_profile_before_writing(self, process_video):
        class MinimalStore:
            def allocate_issue(self, series): return "No.001"
            def preview_next_issue(self, series): return "No.001"
            def mark_processed(self, *args, **kwargs): self.recorded = kwargs

        store = MinimalStore()
        source = SourceConfig(
            type="youtube_channel",
            name="The Intrinsic Value Podcast",
            url="https://www.youtube.com/@TheIntrinsicValuePodcast/videos",
            writer_profile="intrinsic-value-auto",
        )
        video = ChannelVideo(
            video_id="multi123",
            title="Adobe, Lululemon, PayPal – Are our Biggest Losers a Buy Now?",
            url="https://www.youtube.com/watch?v=multi123",
            duration_seconds=3600,
        )
        process_video.return_value = (
            Path("outputs/youtube/the-intrinsic-value-podcast/multi123"),
            {"status": "ok"},
        )

        process_candidate(
            candidate=SourceCandidate(
                source=source,
                source_slug="the-intrinsic-value-podcast",
                video=video,
            ),
            store=store,
            output_base=Path("outputs/youtube"),
            no_check_certificates=False,
            dry_run=False,
        )

        self.assertEqual(process_video.call_args.kwargs["writer_profile"], "interview")
        self.assertEqual(store.recorded["writer_profile"], "interview")

    def test_cover_text_uses_article_title_for_readable_thumbnail(self):
        title = "炼金投研｜No.008 | ZS Zscaler 财报暴雷深度拆解：警惕高增长背后的内投出走"

        ticker, hook = _build_cover_text(
            source_name="Unrivaled Investing",
            title=title,
            issue="No.008",
        )

        self.assertEqual(ticker, "ZS Zscaler 财报暴雷深度拆解")
        self.assertIn("警惕高增长背后的", hook)
        self.assertNotIn("...", hook)


if __name__ == "__main__":
    unittest.main()
