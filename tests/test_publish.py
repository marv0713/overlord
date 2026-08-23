import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import youtube_to_wechat.publish as publish_module
from youtube_to_wechat.wechat import WechatError


class PublishTests(unittest.TestCase):
    def _article_files(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp_path = Path(temp_dir.name)
        article_path = temp_path / "article.md"
        cover_path = temp_path / "cover.png"
        article_path.write_text("# Demo Title\n\n正文", encoding="utf-8")
        cover_path.write_bytes(b"image")
        return article_path, cover_path

    def _wechat_env(self, **extra):
        return {
            "WECHAT_APPID": "app-id",
            "WECHAT_APPSECRET": "app-secret",
            "WECHAT_AUTHOR": "author",
            **extra,
        }

    def _wechat_mocks(self, *, draft="draft-1", mass="msg-1", publish="publish-1"):
        token_mock = MagicMock(return_value="token")
        thumb_mock = MagicMock(return_value="thumb")
        draft_mock = MagicMock(return_value=draft)
        mass_mock = MagicMock(return_value=mass)
        publish_mock = MagicMock(return_value=publish)
        token_patch = patch("youtube_to_wechat.publish.get_access_token", new=token_mock)
        thumb_patch = patch("youtube_to_wechat.publish.upload_permanent_thumb", new=thumb_mock)
        draft_patch = patch("youtube_to_wechat.publish.add_draft", new=draft_mock)
        mass_patch = patch("youtube_to_wechat.publish.submit_mass_send", new=mass_mock)
        publish_patch = patch("youtube_to_wechat.publish.submit_publish", new=publish_mock)
        token_patch.mock = token_mock
        thumb_patch.mock = thumb_mock
        draft_patch.mock = draft_mock
        mass_patch.mock = mass_mock
        publish_patch.mock = publish_mock
        return (
            token_patch, thumb_patch, draft_patch, mass_patch, publish_patch,
        )

    def test_publish_result_contract_and_default_auto_publish_off(self):
        self.assertTrue(hasattr(publish_module, "PublishContext"))
        self.assertTrue(hasattr(publish_module, "PublishResult"))
        self.assertTrue(hasattr(publish_module, "WechatBatchState"))
        self.assertEqual(publish_module.PublishResult(action="draft", media_id="draft").to_dict(), {
            "action": "draft", "media_id": "draft", "task_id": "", "retried": False,
            "error_code": None, "error_message": "",
        })
        self.assertFalse(publish_module.WechatBatchState(False).mass_send_enabled)
        self.assertIsNone(publish_module.PublishContext().wechat_batch)
        article_path, cover_path = self._article_files()
        token, thumb, draft, mass, publish = self._wechat_mocks()
        with token, thumb, draft, mass, publish:
            result = publish_module.WechatDraftPublisher().publish("source", "1", article_path, cover_path, self._wechat_env())
        self.assertEqual(result.action, "draft")
        self.assertEqual(result.media_id, "draft-1")
        mass.mock.assert_not_called()
        publish.mock.assert_not_called()

    def test_auto_publish_defaults_to_a_single_mass_send(self):
        article_path, cover_path = self._article_files()
        token, thumb, draft, mass, publish = self._wechat_mocks()
        with token, thumb, draft, mass, publish:
            result = publish_module.WechatDraftPublisher().publish(
                "source", "1", article_path, cover_path, self._wechat_env(WECHAT_AUTO_PUBLISH="true")
            )
        self.assertEqual(result.action, "mass_send")
        self.assertEqual(result.task_id, "msg-1")
        self.assertEqual(mass.mock.call_count, 1)
        publish.mock.assert_not_called()

    def test_auto_publish_with_mass_disabled_publishes_without_mass_attempt(self):
        article_path, cover_path = self._article_files()
        batch = publish_module.WechatBatchState(mass_send_enabled=False)
        token, thumb, draft, mass, publish = self._wechat_mocks()
        with token, thumb, draft, mass, publish:
            result = publish_module.WechatDraftPublisher().publish(
                "source", "1", article_path, cover_path,
                self._wechat_env(WECHAT_AUTO_PUBLISH="true"), publish_module.PublishContext(batch),
            )
        self.assertEqual(result.action, "publish")
        self.assertEqual(result.task_id, "publish-1")
        self.assertEqual(batch.mass_send_attempts, 0)
        mass.mock.assert_not_called()
        self.assertEqual(publish.mock.call_count, 1)

    def test_shared_batch_allows_only_first_article_to_mass_send(self):
        first_article, first_cover = self._article_files()
        second_article, second_cover = self._article_files()
        batch = publish_module.WechatBatchState(mass_send_enabled=True)
        context = publish_module.PublishContext(batch)
        token, thumb, draft, mass, publish = self._wechat_mocks(draft="draft-1")
        with token, thumb, draft, mass, publish:
            first = publish_module.WechatDraftPublisher().publish("source", "1", first_article, first_cover, self._wechat_env(WECHAT_AUTO_PUBLISH="1"), context)
            draft.mock.return_value = "draft-2"
            second = publish_module.WechatDraftPublisher().publish("source", "2", second_article, second_cover, self._wechat_env(WECHAT_AUTO_PUBLISH="1"), context)
        self.assertEqual(first.action, "mass_send")
        self.assertEqual(second.action, "publish")
        self.assertEqual(batch.mass_send_attempts, 1)
        self.assertEqual(mass.mock.call_count, 1)
        self.assertEqual(publish.mock.call_count, 1)

    def test_safe_mass_failure_retries_once_then_publishes_current_draft(self):
        article_path, cover_path = self._article_files()
        error = WechatError("network refused", retryable=True)
        batch = publish_module.WechatBatchState(mass_send_enabled=True)
        token, thumb, draft, mass, publish = self._wechat_mocks()
        mass.mock.side_effect = [error, error]
        with token, thumb, draft, mass, publish:
            result = publish_module.WechatDraftPublisher().publish(
                "source", "1", article_path, cover_path, self._wechat_env(WECHAT_AUTO_PUBLISH="true"),
                publish_module.PublishContext(batch),
            )
        self.assertEqual(result.action, "publish")
        self.assertTrue(result.retried)
        self.assertEqual(result.error_message, "network refused")
        self.assertEqual(batch.mass_send_attempts, 2)
        self.assertEqual(mass.mock.call_count, 2)
        self.assertEqual(publish.mock.call_count, 1)

    def test_safe_mass_failure_then_retry_success_reports_mass_send(self):
        article_path, cover_path = self._article_files()
        batch = publish_module.WechatBatchState(mass_send_enabled=True)
        token, thumb, draft, mass, publish = self._wechat_mocks()
        mass.mock.side_effect = [WechatError("network refused", retryable=True), "msg-2"]
        with token, thumb, draft, mass, publish:
            result = publish_module.WechatDraftPublisher().publish(
                "source", "1", article_path, cover_path, self._wechat_env(WECHAT_AUTO_PUBLISH="true"),
                publish_module.PublishContext(batch),
            )
        self.assertEqual(result.action, "mass_send")
        self.assertEqual(result.task_id, "msg-2")
        self.assertTrue(result.retried)
        self.assertEqual(batch.mass_send_attempts, 2)
        self.assertEqual(mass.mock.call_count, 2)
        publish.mock.assert_not_called()

    def test_safe_mass_failure_then_ambiguous_retry_reconciles_without_publish(self):
        article_path, cover_path = self._article_files()
        batch = publish_module.WechatBatchState(mass_send_enabled=True)
        token, thumb, draft, mass, publish = self._wechat_mocks()
        mass.mock.side_effect = [
            WechatError("network refused", retryable=True),
            WechatError("timeout", outcome_unknown=True),
        ]
        with token, thumb, draft, mass, publish, patch("youtube_to_wechat.publish.get_draft", return_value={"news_item": []}) as get_draft:
            result = publish_module.WechatDraftPublisher().publish(
                "source", "1", article_path, cover_path, self._wechat_env(WECHAT_AUTO_PUBLISH="true"),
                publish_module.PublishContext(batch),
            )
        self.assertEqual(result.action, "mass_send_unknown")
        self.assertTrue(result.retried)
        self.assertEqual(batch.mass_send_attempts, 2)
        self.assertEqual(mass.mock.call_count, 2)
        get_draft.assert_called_once_with("token", "draft-1")
        publish.mock.assert_not_called()

    def test_known_api_error_including_minus_one_downgrades_to_publish(self):
        article_path, cover_path = self._article_files()
        error = WechatError("system busy", errcode=-1, retryable=True)
        token, thumb, draft, mass, publish = self._wechat_mocks()
        mass.mock.side_effect = error
        with token, thumb, draft, mass, publish:
            result = publish_module.WechatDraftPublisher().publish(
                "source", "1", article_path, cover_path, self._wechat_env(WECHAT_AUTO_PUBLISH="true")
            )
        self.assertEqual(result.action, "publish")
        self.assertEqual(result.error_code, -1)
        self.assertFalse(result.retried)
        self.assertEqual(mass.mock.call_count, 1)
        self.assertEqual(publish.mock.call_count, 1)

    def test_ambiguous_mass_timeout_reconciles_without_retry_or_publish(self):
        article_path, cover_path = self._article_files()
        ambiguous = WechatError("timeout", outcome_unknown=True)
        token, thumb, draft, mass, publish = self._wechat_mocks()
        mass.mock.side_effect = ambiguous
        with token, thumb, draft, mass, publish, patch("youtube_to_wechat.publish.get_draft", return_value=None) as get_draft:
            result = publish_module.WechatDraftPublisher().publish(
                "source", "1", article_path, cover_path, self._wechat_env(WECHAT_AUTO_PUBLISH="true")
            )
        self.assertEqual(result.action, "mass_send_unknown")
        self.assertIn("很可能已群发", result.error_message)
        self.assertEqual(result.task_id, "")
        self.assertEqual(mass.mock.call_count, 1)
        get_draft.assert_called_once_with("token", "draft-1")
        publish.mock.assert_not_called()

    def test_ambiguous_mass_result_with_existing_draft_never_publishes(self):
        article_path, cover_path = self._article_files()
        token, thumb, draft, mass, publish = self._wechat_mocks()
        mass.mock.side_effect = WechatError("bad gateway", outcome_unknown=True)
        with token, thumb, draft, mass, publish, patch("youtube_to_wechat.publish.get_draft", return_value={"news_item": []}):
            result = publish_module.WechatDraftPublisher().publish("source", "1", article_path, cover_path, self._wechat_env(WECHAT_AUTO_PUBLISH="true"))
        self.assertEqual(result.action, "mass_send_unknown")
        self.assertIn("结果不确定", result.error_message)
        self.assertEqual(mass.mock.call_count, 1)
        publish.mock.assert_not_called()

    def test_ambiguous_mass_result_with_draft_query_failure_never_publishes(self):
        article_path, cover_path = self._article_files()
        token, thumb, draft, mass, publish = self._wechat_mocks()
        mass.mock.side_effect = WechatError("bad gateway", outcome_unknown=True)
        with token, thumb, draft, mass, publish, patch("youtube_to_wechat.publish.get_draft", side_effect=WechatError("query failed")):
            result = publish_module.WechatDraftPublisher().publish("source", "1", article_path, cover_path, self._wechat_env(WECHAT_AUTO_PUBLISH="true"))
        self.assertEqual(result.action, "mass_send_unknown")
        self.assertIn("结果不确定", result.error_message)
        self.assertIn("query failed", result.error_message)
        self.assertEqual(mass.mock.call_count, 1)
        publish.mock.assert_not_called()

    def test_publish_failure_retains_created_draft(self):
        article_path, cover_path = self._article_files()
        batch = publish_module.WechatBatchState(mass_send_enabled=False)
        token, thumb, draft, mass, publish = self._wechat_mocks()
        publish.mock.side_effect = WechatError("publish denied", errcode=40001)
        with token, thumb, draft, mass, publish:
            result = publish_module.WechatDraftPublisher().publish(
                "source", "1", article_path, cover_path, self._wechat_env(WECHAT_AUTO_PUBLISH="true"), publish_module.PublishContext(batch)
            )
        self.assertEqual(result.action, "draft")
        self.assertEqual(result.media_id, "draft-1")
        self.assertEqual(result.error_code, 40001)
        draft.mock.assert_called_once()
        mass.mock.assert_not_called()
        self.assertEqual(publish.mock.call_count, 1)

    def test_failed_draft_creation_does_not_consume_batch_mass_candidate(self):
        article_path, cover_path = self._article_files()
        next_article, next_cover = self._article_files()
        batch = publish_module.WechatBatchState(mass_send_enabled=True)
        token, thumb, draft, mass, publish = self._wechat_mocks()
        draft.mock.side_effect = [WechatError("add failed"), "draft-2"]
        with token, thumb, draft, mass, publish:
            failed = publish_module.WechatDraftPublisher().publish("source", "1", article_path, cover_path, self._wechat_env(WECHAT_AUTO_PUBLISH="true"), publish_module.PublishContext(batch))
            successful = publish_module.WechatDraftPublisher().publish("source", "2", next_article, next_cover, self._wechat_env(WECHAT_AUTO_PUBLISH="true"), publish_module.PublishContext(batch))
        self.assertIsNone(failed)
        self.assertEqual(successful.action, "mass_send")
        self.assertEqual(batch.mass_send_attempts, 1)
        self.assertEqual(mass.mock.call_count, 1)

    def test_non_wechat_publishers_accept_context_and_unknown_destination_returns_none(self):
        article_path, cover_path = self._article_files()
        self.assertIsNone(publish_module.PushPlusPublisher().publish("source", "1", article_path, cover_path, {}, publish_module.PublishContext()))
        self.assertIsNone(publish_module.EmailPublisher().publish("source", "1", article_path, cover_path, {}, publish_module.PublishContext()))
        self.assertIsNone(publish_module.publish_article("missing", "source", "1", article_path, cover_path, {}, publish_module.PublishContext()))

    def test_alert_text_covers_manual_failure_mass_publish_and_unknown_outcomes(self):
        publisher = publish_module.WechatDraftPublisher()
        webhook = "https://wecom.example.test/hook"
        with patch("youtube_to_wechat.publish.urllib.request.urlopen") as urlopen:
            publisher._send_alerts("source", "title", {"WECOM_WEBHOOK": webhook}, publish_module.PublishResult(action="draft", media_id="draft"))
            publisher._send_alerts("source", "title", {"WECOM_WEBHOOK": webhook}, publish_module.PublishResult(action="draft", media_id="draft", error_message="publish failed"))
            publisher._send_alerts("source", "title", {"WECOM_WEBHOOK": webhook}, publish_module.PublishResult(action="mass_send", media_id="draft", task_id="msg-1"))
            publisher._send_alerts("source", "title", {"WECOM_WEBHOOK": webhook}, publish_module.PublishResult(action="publish", media_id="draft", task_id="pub-1"))
            publisher._send_alerts("source", "title", {"WECOM_WEBHOOK": webhook}, publish_module.PublishResult(action="mass_send_unknown", media_id="draft", error_message="结果不确定"))
            publisher._send_alerts("source", "title", {"WECOM_WEBHOOK": webhook}, publish_module.PublishResult(action="publish", media_id="draft", task_id="pub-1", error_code=-1, error_message="system busy"))
        contents = [json.loads(call.args[0].data.decode("utf-8"))["markdown"]["content"] for call in urlopen.call_args_list]
        self.assertIn("请前往微信公众号后台草稿箱预览并群发", contents[0])
        self.assertIn("自动发布失败", contents[1])
        self.assertIn("草稿已保留", contents[1])
        self.assertIn("msg_id: msg-1", contents[2])
        self.assertIn("自动发布", contents[3])
        self.assertIn("未群发", contents[3])
        self.assertIn("publish_id: pub-1", contents[3])
        self.assertIn("结果不确定", contents[4])
        self.assertIn("publish_id: pub-1", contents[5])
        self.assertIn("system busy", contents[5])

    def test_malformed_wecom_webhook_is_nonfatal(self):
        publisher = publish_module.WechatDraftPublisher()
        publisher._send_alerts(
            "source",
            "title",
            {"WECOM_WEBHOOK": "\n"},
            publish_module.PublishResult(action="draft", media_id="draft"),
        )
    def test_wechat_publisher_missing_env_does_not_raise(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            article_path = temp_path / "article.md"
            cover_path = temp_path / "cover.png"
            article_path.write_text("# Demo\n\n正文", encoding="utf-8")
            cover_path.write_bytes(b"not-a-real-image")

            publish_module.WechatDraftPublisher().publish(
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

            publish_module.EmailPublisher().publish(
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

            publish_module.EmailPublisher().publish(
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
