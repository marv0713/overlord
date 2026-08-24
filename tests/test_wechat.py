import http.client
import io
import ssl
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from youtube_to_wechat import wechat
from youtube_to_wechat.wechat import (
    WechatError,
    _get_json,
    _markdown_to_html,
    _multipart_upload,
    _post_json,
    build_draft_article,
    parse_env_text,
)


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _ReadFailureResponse(_Response):
    def __init__(self, error: BaseException):
        self.error = error

    def read(self):
        raise self.error


class WechatTests(unittest.TestCase):
    def test_wechat_error_exposes_structured_fields_and_message(self):
        error = WechatError("request failed", errcode=123, retryable=True, outcome_unknown=True)

        self.assertEqual(str(error), "request failed")
        self.assertEqual(error.errcode, 123)
        self.assertTrue(error.retryable)
        self.assertTrue(error.outcome_unknown)

    def test_raise_api_error_marks_every_nonzero_code_non_retryable_and_known(self):
        with self.assertRaises(WechatError) as raised:
            wechat._raise_api_error({"errcode": -1, "errmsg": "system busy"}, "mass send")

        self.assertEqual(raised.exception.errcode, -1)
        self.assertFalse(raised.exception.retryable)
        self.assertFalse(raised.exception.outcome_unknown)
        self.assertIn("mass send", str(raised.exception))

    def test_raise_api_error_accepts_missing_none_and_string_zero(self):
        for data in ({}, {"errcode": None}, {"errcode": 0}, {"errcode": "0"}):
            with self.subTest(data=data):
                wechat._raise_api_error(data, "test")

    def test_raise_api_error_normalizes_string_negative_code(self):
        with self.assertRaises(WechatError) as raised:
            wechat._raise_api_error({"errcode": "-1", "errmsg": "system busy"}, "mass send")

        self.assertEqual(raised.exception.errcode, -1)
        self.assertFalse(raised.exception.retryable)
        self.assertFalse(raised.exception.outcome_unknown)

    def test_raise_api_error_normalizes_integer_formatted_string_code(self):
        with self.assertRaises(WechatError) as raised:
            wechat._raise_api_error({"errcode": " 40007 ", "errmsg": "invalid media"}, "test")

        self.assertEqual(raised.exception.errcode, 40007)

    def test_raise_api_error_rejects_non_integer_errcode_values(self):
        for raw_errcode in (False, True, 0.0, 0.5, 1.5, [], {}):
            with self.subTest(raw_errcode=raw_errcode):
                with self.assertRaises(WechatError) as raised:
                    wechat._raise_api_error({"errcode": raw_errcode}, "test")

                self.assertIsNone(raised.exception.errcode)
                self.assertFalse(raised.exception.retryable)
                self.assertTrue(raised.exception.outcome_unknown)

    def test_raise_api_error_rejects_nonnumeric_code_as_structured_error(self):
        with self.assertRaises(WechatError) as raised:
            wechat._raise_api_error({"errcode": "not-a-code"}, "test")

        self.assertIsNone(raised.exception.errcode)
        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat._post_json", return_value={"msg_id": 987})
    def test_submit_mass_send_posts_exact_payload_and_returns_string_msg_id(self, post):
        result = wechat.submit_mass_send("token", "draft-1")

        self.assertEqual(result, "987")
        post.assert_called_once_with(
            "https://api.weixin.qq.com/cgi-bin/message/mass/sendall?access_token=token",
            {
                "filter": {"is_to_all": True},
                "mpnews": {"media_id": "draft-1"},
                "msgtype": "mpnews",
                "send_ignore_reprint": 1,
            },
        )

    @patch("youtube_to_wechat.wechat._post_json", return_value={"publish_id": 456})
    def test_submit_publish_posts_payload_and_returns_string_publish_id(self, post):
        result = wechat.submit_publish("token", "draft-1")

        self.assertEqual(result, "456")
        post.assert_called_once_with(
            "https://api.weixin.qq.com/cgi-bin/freepublish/submit?access_token=token",
            {"media_id": "draft-1"},
        )

    @patch("youtube_to_wechat.wechat._post_json", return_value={})
    def test_submit_mass_send_missing_msg_id_raises_structured_error(self, post):
        with self.assertRaises(WechatError) as raised:
            wechat.submit_mass_send("token", "draft-1")

        self.assertIsNone(raised.exception.errcode)
        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat._post_json", return_value={})
    def test_submit_publish_missing_publish_id_raises_structured_error(self, post):
        with self.assertRaises(WechatError) as raised:
            wechat.submit_publish("token", "draft-1")

        self.assertIsNone(raised.exception.errcode)
        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat._post_json", return_value={"msg_id": 0})
    def test_submit_mass_send_returns_zero_identifier_as_string(self, post):
        self.assertEqual(wechat.submit_mass_send("token", "draft-1"), "0")

    @patch("youtube_to_wechat.wechat._post_json", return_value={"publish_id": 0})
    def test_submit_publish_returns_zero_identifier_as_string(self, post):
        self.assertEqual(wechat.submit_publish("token", "draft-1"), "0")

    @patch("youtube_to_wechat.wechat._post_json", return_value={"msg_id": None})
    def test_submit_mass_send_none_identifier_is_missing(self, post):
        with self.assertRaises(WechatError) as raised:
            wechat.submit_mass_send("token", "draft-1")

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat._post_json", return_value={"publish_id": None})
    def test_submit_publish_none_identifier_is_missing(self, post):
        with self.assertRaises(WechatError) as raised:
            wechat.submit_publish("token", "draft-1")

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat._post_json")
    def test_submit_mass_send_rejects_invalid_identifiers_as_unknown(self, post):
        for identifier in ("", "   ", [], {}):
            with self.subTest(identifier=identifier):
                post.return_value = {"msg_id": identifier}
                with self.assertRaises(WechatError) as raised:
                    wechat.submit_mass_send("token", "draft-1")

                self.assertFalse(raised.exception.retryable)
                self.assertTrue(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat._post_json")
    def test_submit_publish_rejects_invalid_identifiers_as_unknown(self, post):
        for identifier in ("", "   ", [], {}):
            with self.subTest(identifier=identifier):
                post.return_value = {"publish_id": identifier}
                with self.assertRaises(WechatError) as raised:
                    wechat.submit_publish("token", "draft-1")

                self.assertFalse(raised.exception.retryable)
                self.assertTrue(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat._post_json", return_value={"msg_id": False})
    def test_submit_mass_send_rejects_boolean_identifier(self, post):
        with self.assertRaises(WechatError) as raised:
            wechat.submit_mass_send("token", "draft-1")

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat._post_json", return_value={"publish_id": True})
    def test_submit_publish_rejects_boolean_identifier(self, post):
        with self.assertRaises(WechatError) as raised:
            wechat.submit_publish("token", "draft-1")

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat._post_json", return_value={"errcode": 40007, "errmsg": "Invalid MEDIA_ID"})
    def test_get_draft_maps_invalid_media_id_to_none(self, post):
        self.assertIsNone(wechat.get_draft("token", "draft-1"))
        post.assert_called_once_with(
            "https://api.weixin.qq.com/cgi-bin/draft/get?access_token=token",
            {"media_id": "draft-1"},
        )

    @patch("youtube_to_wechat.wechat._post_json", return_value={"errcode": "40007", "errmsg": "Invalid MEDIA_ID"})
    def test_get_draft_maps_string_invalid_media_id_to_none(self, post):
        self.assertIsNone(wechat.get_draft("token", "draft-1"))

    @patch("youtube_to_wechat.wechat._post_json", return_value={"errcode": 40007, "errmsg": "media missing"})
    def test_get_draft_raises_for_other_40007_message(self, post):
        with self.assertRaises(WechatError) as raised:
            wechat.get_draft("token", "draft-1")

        self.assertEqual(raised.exception.errcode, 40007)
        self.assertFalse(raised.exception.retryable)
        self.assertFalse(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat._post_json", return_value={"news_item": []})
    def test_get_draft_returns_successful_response(self, post):
        self.assertEqual(wechat.get_draft("token", "draft-1"), {"news_item": []})

    def test_post_json_maps_http_400_to_known_non_retryable_error(self):
        error = urllib.error.HTTPError("url", 400, "bad request", {}, io.BytesIO(b"{}"))
        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(WechatError) as raised:
                _post_json("https://example.com", {"x": 1})

        self.assertFalse(raised.exception.retryable)
        self.assertFalse(raised.exception.outcome_unknown)

    def test_post_json_maps_http_500_to_unknown_non_retryable_error(self):
        error = urllib.error.HTTPError("url", 500, "server error", {}, io.BytesIO(b"{}"))
        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(WechatError) as raised:
                _post_json("https://example.com", {"x": 1})

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    def test_post_json_maps_dns_failure_to_safe_retryable_error(self):
        error = urllib.error.URLError(socket.gaierror("dns failure"))
        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(WechatError) as raised:
                _post_json("https://example.com", {"x": 1})

        self.assertTrue(raised.exception.retryable)
        self.assertFalse(raised.exception.outcome_unknown)

    def test_post_json_maps_connection_refused_to_safe_retryable_error(self):
        error = urllib.error.URLError(ConnectionRefusedError("refused"))
        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(WechatError) as raised:
                _post_json("https://example.com", {"x": 1})

        self.assertTrue(raised.exception.retryable)
        self.assertFalse(raised.exception.outcome_unknown)

    def test_post_json_maps_unsafe_url_error_to_unknown_non_retryable_error(self):
        error = urllib.error.URLError(OSError("unsafe connection failure"))
        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(WechatError) as raised:
                _post_json("https://example.com", {"x": 1})

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    def test_post_json_maps_socket_timeout_to_unknown_non_retryable_error(self):
        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", side_effect=socket.timeout()):
            with self.assertRaises(WechatError) as raised:
                _post_json("https://example.com", {"x": 1})

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    def test_post_json_maps_timeout_error_to_unknown_non_retryable_error(self):
        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", side_effect=TimeoutError()):
            with self.assertRaises(WechatError) as raised:
                _post_json("https://example.com", {"x": 1})

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    def test_post_json_maps_remote_disconnect_to_unknown_non_retryable_error(self):
        with patch(
            "youtube_to_wechat.wechat.urllib.request.urlopen",
            side_effect=http.client.RemoteDisconnected("remote disconnected"),
        ):
            with self.assertRaises(WechatError) as raised:
                _post_json("https://example.com", {"x": 1})

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    def test_post_json_maps_connection_reset_to_unknown_non_retryable_error(self):
        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", side_effect=ConnectionResetError()):
            with self.assertRaises(WechatError) as raised:
                _post_json("https://example.com", {"x": 1})

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    def test_post_json_maps_incomplete_read_to_redacted_unknown_error_with_cause(self):
        url = "https://api.weixin.qq.com/cgi-bin/message/mass/sendall?access_token=secret-token"
        failure = http.client.IncompleteRead(b'{"errcode":0', 5)
        response = _ReadFailureResponse(failure)

        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", return_value=response):
            with self.assertRaises(WechatError) as raised:
                _post_json(url, {"x": 1})

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)
        self.assertNotIn("secret-token", str(raised.exception))
        self.assertIn("/cgi-bin/message/mass/sendall", str(raised.exception))
        self.assertIs(raised.exception.__cause__, failure)

    def test_post_json_does_not_swallow_unrelated_read_errors(self):
        failure = RuntimeError("programmer error")
        response = _ReadFailureResponse(failure)

        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "programmer error") as raised:
                _post_json("https://example.com/post", {"x": 1})

        self.assertIs(raised.exception, failure)

    def test_post_json_maps_invalid_json_to_unknown_non_retryable_error(self):
        response = _Response(b"not json")
        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", return_value=response):
            with self.assertRaises(WechatError) as raised:
                _post_json("https://example.com", {"x": 1})

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    def test_post_json_maps_invalid_utf8_to_unknown_non_retryable_error(self):
        response = _Response(b"\xff")
        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", return_value=response):
            with self.assertRaises(WechatError) as raised:
                _post_json("https://example.com", {"x": 1})

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    def test_post_json_rejects_non_object_json_as_unknown(self):
        for body in (b"[]", b"null"):
            with self.subTest(body=body):
                response = _Response(body)
                with patch("youtube_to_wechat.wechat.urllib.request.urlopen", return_value=response):
                    with self.assertRaises(WechatError) as raised:
                        _post_json("https://example.com", {"x": 1})

                self.assertFalse(raised.exception.retryable)
                self.assertTrue(raised.exception.outcome_unknown)

    def test_post_json_errors_redact_access_token_from_messages(self):
        url = "https://api.weixin.qq.com/cgi-bin/message/mass/sendall?access_token=secret-token"
        cases = (
            ("invalid json", _Response(b"not json")),
            ("invalid utf8", _Response(b"\xff")),
            ("non-object", _Response(b"[]")),
            ("http", urllib.error.HTTPError(url, 500, "server error", {}, io.BytesIO(b"{}"))),
            ("url", urllib.error.URLError(OSError("connection failed"))),
            ("timeout", TimeoutError("timed out")),
        )
        for name, failure in cases:
            with self.subTest(name=name):
                patch_kwargs = (
                    {"side_effect": failure}
                    if isinstance(failure, BaseException)
                    else {"return_value": failure}
                )
                with patch("youtube_to_wechat.wechat.urllib.request.urlopen", **patch_kwargs):
                    with self.assertRaises(WechatError) as raised:
                        _post_json(url, {"x": 1})

                self.assertNotIn("secret-token", str(raised.exception))
                self.assertIn("/cgi-bin/message/mass/sendall", str(raised.exception))

    def test_pre_draft_helpers_map_url_errors_to_structured_errors(self):
        url = "https://api.weixin.qq.com/cgi-bin/token?access_token=secret-token"
        failure = urllib.error.URLError(socket.gaierror("dns failure"))

        with patch("youtube_to_wechat.wechat.urllib.request.urlopen", side_effect=failure):
            try:
                _get_json(url)
            except Exception as exc:
                get_error = exc
            else:
                self.fail("_get_json did not raise")

        self.assertIsInstance(get_error, WechatError)
        self.assertTrue(get_error.retryable)
        self.assertFalse(get_error.outcome_unknown)
        self.assertNotIn("secret-token", str(get_error))
        self.assertIn("/cgi-bin/token", str(get_error))
        self.assertIs(get_error.__cause__, failure)

        upload_url = "https://api.weixin.qq.com/cgi-bin/material/add_material?access_token=secret-token"
        upload_failure = urllib.error.URLError(ConnectionRefusedError("refused"))
        with tempfile.NamedTemporaryFile() as image:
            image.write(b"image")
            image.flush()
            with patch(
                "youtube_to_wechat.wechat.urllib.request.urlopen",
                side_effect=upload_failure,
            ):
                try:
                    _multipart_upload(upload_url, "media", Path(image.name))
                except Exception as exc:
                    upload_error = exc
                else:
                    self.fail("_multipart_upload did not raise")

        self.assertIsInstance(upload_error, WechatError)
        self.assertTrue(upload_error.retryable)
        self.assertFalse(upload_error.outcome_unknown)
        self.assertNotIn("secret-token", str(upload_error))
        self.assertIn("/cgi-bin/material/add_material", str(upload_error))
        self.assertIs(upload_error.__cause__, upload_failure)

    @patch("youtube_to_wechat.wechat.ssl._create_unverified_context", side_effect=AssertionError("insecure TLS"))
    @patch("youtube_to_wechat.wechat.ssl.create_default_context")
    @patch("youtube_to_wechat.wechat.urllib.request.urlopen")
    def test_http_helpers_use_verified_tls_context(self, urlopen, create_context, unverified):
        verified_context = object()
        create_context.return_value = verified_context
        urlopen.return_value = _Response(b"{}")

        _get_json("https://example.com/get")
        _post_json("https://example.com/post", {"x": 1})
        with tempfile.NamedTemporaryFile() as image:
            image.write(b"image")
            image.flush()
            _multipart_upload("https://example.com/upload", "media", Path(image.name))

        self.assertEqual(create_context.call_count, 3)
        self.assertEqual(
            [call.kwargs["context"] for call in urlopen.call_args_list],
            [verified_context, verified_context, verified_context],
        )
        unverified.assert_not_called()

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
