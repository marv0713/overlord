import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_to_wechat.publish import EmailPublisher, WechatDraftPublisher


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

    @patch("smtplib.SMTP_SSL")
    def test_email_publisher_sends_markdown_article(self, smtp_ssl):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            article_path = temp_path / "article.md"
            cover_path = temp_path / "cover.png"
            article_path.write_text("# Demo Title\n\n正文", encoding="utf-8")

            EmailPublisher().publish(
                source_name="Demo Source",
                issue="",
                article_path=article_path,
                cover_path=cover_path,
                env={
                    "SMTP_HOST": "smtp.example.com",
                    "SMTP_PORT": "465",
                    "SMTP_USER": "bot@example.com",
                    "SMTP_PASSWORD": "secret",
                    "EMAIL_FROM": "bot@example.com",
                    "EMAIL_TO": "reader@example.com",
                },
            )

        smtp_ssl.assert_called_once_with("smtp.example.com", 465, timeout=20)
        server = smtp_ssl.return_value.__enter__.return_value
        server.login.assert_called_once_with("bot@example.com", "secret")
        sent = server.send_message.call_args.args[0]
        self.assertEqual(sent["To"], "reader@example.com")
        self.assertIn("Demo Source", sent["Subject"])
        self.assertIn("Demo Title", sent["Subject"])
        self.assertIn("正文", sent.get_content())

    @patch("smtplib.SMTP")
    def test_email_publisher_supports_lurker_smtp_env_names(self, smtp):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            article_path = temp_path / "article.md"
            cover_path = temp_path / "cover.png"
            article_path.write_text("# Lurker Style\n\n正文", encoding="utf-8")

            EmailPublisher().publish(
                source_name="Demo Source",
                issue="",
                article_path=article_path,
                cover_path=cover_path,
                env={
                    "SMTP_HOST": "smtp.example.com",
                    "SMTP_PORT": "587",
                    "SMTP_USER": "bot@example.com",
                    "SMTP_PASSWORD": "secret",
                    "SMTP_FROM": "from@example.com",
                    "SMTP_USE_SSL": "false",
                    "SMTP_USE_TLS": "true",
                    "EMAIL_TO": "reader@example.com",
                },
            )

        smtp.assert_called_once_with("smtp.example.com", 587, timeout=20)
        server = smtp.return_value.__enter__.return_value
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("bot@example.com", "secret")
        sent = server.send_message.call_args.args[0]
        self.assertEqual(sent["From"], "from@example.com")


if __name__ == "__main__":
    unittest.main()
