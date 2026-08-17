import tempfile
import unittest
from pathlib import Path

from youtube_to_wechat.writer import GeminiWriter, _parse_article_response
from youtube_to_wechat.writer_profiles import load_writer_profile


class WriterProfileTests(unittest.TestCase):
    def test_loads_writer_profile_markdown_by_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles_dir = Path(temp_dir)
            (profiles_dir / "deep-stock-analysis.md").write_text(
                "# Deep Stock Analysis\n\nUse detailed company analysis.",
                encoding="utf-8",
            )

            profile = load_writer_profile("deep-stock-analysis", profiles_dir)

        self.assertEqual(profile.name, "deep-stock-analysis")
        self.assertIn("detailed company analysis", profile.prompt)

    def test_rejects_unknown_writer_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(FileNotFoundError):
                load_writer_profile("missing-profile", Path(temp_dir))

    def test_gemini_writer_uses_loaded_profile_prompt(self):
        writer = GeminiWriter(
            api_key="test-key",
            profile_name="market-commentary",
            profile_prompt="Use a market commentary structure.",
        )

        prompt = writer.build_prompt(
            transcript="Transcript body",
            meta={
                "title": "Market Update",
                "channel": "Demo Channel",
                "url": "https://example.com/video",
            },
        )

        self.assertIn("Use a market commentary structure.", prompt)
        self.assertIn("writer_profile：market-commentary", prompt)

    def test_interview_profile_keeps_long_transcript_tail(self):
        writer = GeminiWriter(
            api_key="test-key",
            profile_name="interview",
            profile_prompt="Cover every company in the title.",
        )
        transcript = "A" * 12_000 + "REDDIT AMAZON TSMC"

        prompt = writer.build_prompt(transcript=transcript, meta={"title": "Four companies"})

        self.assertIn("REDDIT AMAZON TSMC", prompt)

    def test_deep_stock_profile_keeps_existing_transcript_limit(self):
        writer = GeminiWriter(
            api_key="test-key",
            profile_name="deep-stock-analysis",
            profile_prompt="Analyze one company.",
        )
        transcript = "A" * 12_000 + "TAIL SHOULD BE TRUNCATED"

        prompt = writer.build_prompt(transcript=transcript, meta={"title": "One company"})

        self.assertNotIn("TAIL SHOULD BE TRUNCATED", prompt)

    def test_parse_article_response_removes_opening_disclaimer_but_keeps_ending_one(self):
        raw = """**【免责声明】本内容仅为 AI 对海外公开资讯的辅助整理，不代表本公众号立场，不构成任何投资建议。**

## 引言与核心金句
正文内容。

## 九、来源说明
来源链接。
**【免责声明】本内容仅为 AI 对海外公开资讯的辅助整理，不代表本公众号立场，不构成任何投资建议。**

> 摘要：样例摘要
> 标题建议：样例标题
"""

        article = _parse_article_response(raw, fallback_title="Fallback", url="https://example.com")

        self.assertFalse(article.markdown.lstrip().startswith("**【免责声明】"))
        self.assertEqual(article.markdown.count("**【免责声明】"), 1)
        self.assertIn("## 九、来源说明", article.markdown)


if __name__ == "__main__":
    unittest.main()
