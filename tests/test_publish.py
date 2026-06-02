import tempfile
import unittest
from pathlib import Path

from youtube_to_wechat.publish import WechatDraftPublisher


class PublishTests(unittest.TestCase):
    def test_wechat_publisher_missing_env_does_not_raise(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            article_path = temp_path / "article.md"
            cover_path = temp_path / "cover.png"
            article_path.write_text("# Demo\n\n正文", encoding="utf-8")
            cover_path.write_bytes(b"not-a-real-image")

            WechatDraftPublisher().publish(
                source_name="Demo Source",
                issue="No.001",
                article_path=article_path,
                cover_path=cover_path,
                env={},
            )


if __name__ == "__main__":
    unittest.main()
