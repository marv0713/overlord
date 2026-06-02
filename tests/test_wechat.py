import unittest

from youtube_to_wechat.wechat import _markdown_to_html, build_draft_article, parse_env_text


class WechatTests(unittest.TestCase):
    def test_parse_env_text_ignores_comments_and_blank_lines(self):
        env = parse_env_text("""
GEMINI_API_KEY=abc

# comment
WECHAT_AUTHOR=marv 的炼金术
""")

        self.assertEqual(env["GEMINI_API_KEY"], "abc")
        self.assertEqual(env["WECHAT_AUTHOR"], "marv 的炼金术")

    def test_build_draft_article_adds_column_to_title(self):
        article = build_draft_article(
            title="AAPL：样例文章",
            author="marv 的炼金术",
            digest="摘要",
            content="<h1>AAPL</h1>",
            thumb_media_id="thumb123",
            column="炼金投研",
            source_url="https://www.youtube.com/watch?v=abc",
        )

        self.assertEqual(article["title"], "炼金投研｜AAPL：样例文章")
        self.assertEqual(article["thumb_media_id"], "thumb123")
        self.assertEqual(article["content_source_url"], "https://www.youtube.com/watch?v=abc")

    def test_markdown_html_uses_mobile_friendly_spacing(self):
        html = _markdown_to_html(
            "- **估值吸引力**：当前估值（P/FE 21-22倍）接近十年低位。\n\n"
            "Microsoft 365 和 Azure 的高速增长为近期业绩提供动力。"
        )

        self.assertNotIn("text-align: justify", html)
        self.assertIn("text-align: left", html)
        self.assertIn("word-break: break-word", html)
        self.assertIn("line-height: 1.72", html)
        self.assertIn("margin: 0 0 0.46em", html)
        self.assertIn("margin: 0 0 0.58em", html)

    def test_markdown_html_escapes_untrusted_text(self):
        html = _markdown_to_html("# <script>alert(1)</script>\n\n正文 <img src=x onerror=alert(1)>")

        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)


if __name__ == "__main__":
    unittest.main()
