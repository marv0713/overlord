import tempfile
import unittest
from pathlib import Path

from scripts.push_wechat_draft import _extract_title, _read_article


class PushWechatDraftTests(unittest.TestCase):
    def test_extract_title_prefers_title_suggestion(self):
        text = """# No.012 | AMZN、HPQ、CHTR、FISV... Sven Carlin 博士拆解：高风险、高回报与“公平公司”的价值博弈

No.012 | Sven Carlin 价值投资象限：拆解高风险与被低估的“公平公司”

正文。

> 标题建议：No.012 | Sven Carlin 价值投资象限
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            article_path = Path(temp_dir) / "article.md"
            article_path.write_text(text, encoding="utf-8")

            title = _extract_title(text, article_path)

        self.assertEqual(title, "No.012 | Sven 价值投资象限")

    def test_read_article_removes_leading_title_block_from_content(self):
        text = """# No.012 | AMZN、HPQ、CHTR、FISV... Sven Carlin 博士拆解：高风险、高回报与“公平公司”的价值博弈

No.012 | Sven Carlin 价值投资象限：拆解高风险与被低估的“公平公司”

> **管理层正在疯狂抛售。**

正文。

> 标题建议：No.012 | Sven Carlin 价值投资象限
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            article_path = Path(temp_dir) / "article.md"
            article_path.write_text(text, encoding="utf-8")

            title, _digest, content = _read_article(article_path)

        self.assertEqual(title, "No.012 | Sven 价值投资象限")
        self.assertNotIn("<h1", content)
        self.assertNotIn("AMZN、HPQ、CHTR", content)
        self.assertNotIn("拆解高风险与被低估", content)
        self.assertIn("管理层正在疯狂抛售", content)


if __name__ == "__main__":
    unittest.main()
