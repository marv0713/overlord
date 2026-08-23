# WeChat Automatic Mass Send Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing WeChat draft pipeline so one cron batch mass-sends its first successful draft and publicly publishes all later drafts, while retaining a default-off master switch and preventing duplicate mass sends after ambiguous transport failures.

**Architecture:** Keep low-level WeChat HTTP/API behavior in `wechat.py`, and put per-batch routing in `publish.py` using an explicit `PublishContext` and `WechatBatchState`. `process_sources.py` creates one shared context per process, passes it to every destination, and persists the structured WeChat result into each article's `run.json`.

**Tech Stack:** Python 3.12 standard library (`urllib`, `socket`, `dataclasses`, `unittest`, `unittest.mock`), WeChat Official Account APIs.

---

## File map

- Modify `src/youtube_to_wechat/wechat.py`: structured WeChat errors, transport classification, mass-send/publication/draft-query API functions.
- Modify `src/youtube_to_wechat/publish.py`: batch state, publish context/result contracts, WeChat action state machine, result-aware notifications.
- Modify `scripts/process_sources.py`: create one context per cron process and persist each WeChat result.
- Modify `tests/test_wechat.py`: endpoint payload, error-code, HTTP inheritance, and draft-consumption tests.
- Modify `tests/test_publish.py`: switch behavior, batch ordering, retry limits, uncertain outcomes, Protocol compatibility, and notification tests.
- Modify `tests/test_process_sources.py`: result persistence helper test.
- Modify `.env.example`: document safe default switches.
- Modify `README.md`: document draft-only, automatic publish, and first-item mass-send modes.

### Task 1: Add structured WeChat API and transport behavior

**Files:**
- Modify: `src/youtube_to_wechat/wechat.py`
- Test: `tests/test_wechat.py`

- [ ] **Step 1: Write failing tests for structured API errors and payloads**

Add these imports and tests to `tests/test_wechat.py`:

```python
import io
import socket
import urllib.error
from unittest.mock import patch

from youtube_to_wechat.wechat import (
    WechatError,
    _post_json,
    get_draft,
    submit_mass_send,
    submit_publish,
)


class WechatApiTests(unittest.TestCase):
    @patch("youtube_to_wechat.wechat._post_json")
    def test_submit_mass_send_uses_all_followers_and_reprint_override(self, post_json):
        post_json.return_value = {"errcode": 0, "errmsg": "send job submission success", "msg_id": 123}

        msg_id = submit_mass_send("token", "draft-1")

        self.assertEqual(msg_id, "123")
        self.assertEqual(
            post_json.call_args.args[1],
            {
                "filter": {"is_to_all": True},
                "mpnews": {"media_id": "draft-1"},
                "msgtype": "mpnews",
                "send_ignore_reprint": 1,
            },
        )

    @patch("youtube_to_wechat.wechat._post_json")
    def test_submit_publish_returns_publish_id(self, post_json):
        post_json.return_value = {"errcode": 0, "publish_id": "publish-1"}

        self.assertEqual(submit_publish("token", "draft-1"), "publish-1")

    @patch("youtube_to_wechat.wechat._post_json")
    def test_errcode_minus_one_is_structured_and_not_retryable(self, post_json):
        post_json.return_value = {"errcode": -1, "errmsg": "system error"}

        with self.assertRaises(WechatError) as raised:
            submit_mass_send("token", "draft-1")

        self.assertEqual(raised.exception.errcode, -1)
        self.assertFalse(raised.exception.retryable)
        self.assertFalse(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat._post_json")
    def test_get_draft_maps_only_invalid_media_id_to_missing(self, post_json):
        post_json.return_value = {"errcode": 40007, "errmsg": "invalid media_id"}
        self.assertIsNone(get_draft("token", "draft-1"))

        post_json.return_value = {"errcode": 40014, "errmsg": "invalid access_token"}
        with self.assertRaises(WechatError) as raised:
            get_draft("token", "draft-1")
        self.assertEqual(raised.exception.errcode, 40014)
```

- [ ] **Step 2: Run the endpoint tests and verify they fail**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_wechat.WechatApiTests -v
```

Expected: import errors for `get_draft`, `submit_mass_send`, and `submit_publish`.

- [ ] **Step 3: Implement structured errors and endpoint functions**

In `src/youtube_to_wechat/wechat.py`, add the standard-library imports and replace `WechatError` with:

```python
import http.client
import socket
import urllib.error


class WechatError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        errcode: int | None = None,
        retryable: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.errcode = errcode
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown


def _raise_api_error(data: dict[str, Any], action: str) -> None:
    errcode = int(data.get("errcode") or 0)
    if errcode != 0:
        raise WechatError(
            f"WeChat {action} error: {data}",
            errcode=errcode,
            retryable=False,
            outcome_unknown=False,
        )
```

Add the endpoint functions directly below `add_draft`:

```python
def submit_mass_send(access_token: str, media_id: str) -> str:
    data = _post_json(
        f"https://api.weixin.qq.com/cgi-bin/message/mass/sendall?access_token={urllib.parse.quote(access_token)}",
        {
            "filter": {"is_to_all": True},
            "mpnews": {"media_id": media_id},
            "msgtype": "mpnews",
            "send_ignore_reprint": 1,
        },
    )
    _raise_api_error(data, "mass send")
    msg_id = data.get("msg_id")
    if msg_id is None:
        raise WechatError(f"WeChat mass send response missing msg_id: {data}")
    return str(msg_id)


def submit_publish(access_token: str, media_id: str) -> str:
    data = _post_json(
        f"https://api.weixin.qq.com/cgi-bin/freepublish/submit?access_token={urllib.parse.quote(access_token)}",
        {"media_id": media_id},
    )
    _raise_api_error(data, "publish")
    publish_id = data.get("publish_id")
    if publish_id is None:
        raise WechatError(f"WeChat publish response missing publish_id: {data}")
    return str(publish_id)


def get_draft(access_token: str, media_id: str) -> dict[str, Any] | None:
    data = _post_json(
        f"https://api.weixin.qq.com/cgi-bin/draft/get?access_token={urllib.parse.quote(access_token)}",
        {"media_id": media_id},
    )
    errcode = int(data.get("errcode") or 0)
    errmsg = str(data.get("errmsg") or "").lower()
    if errcode == 40007 and "invalid media_id" in errmsg:
        return None
    _raise_api_error(data, "get draft")
    return data
```

- [ ] **Step 4: Write failing tests for HTTP classification order**

Add these tests to `WechatApiTests`:

```python
    @patch("youtube_to_wechat.wechat.urllib.request.urlopen")
    def test_http_400_is_not_misclassified_as_retryable_url_error(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            "https://api.weixin.qq.com/test", 400, "bad request", {}, io.BytesIO()
        )

        with self.assertRaises(WechatError) as raised:
            _post_json("https://api.weixin.qq.com/test", {})

        self.assertFalse(raised.exception.retryable)
        self.assertFalse(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat.urllib.request.urlopen")
    def test_http_500_has_unknown_submission_outcome(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            "https://api.weixin.qq.com/test", 500, "server error", {}, io.BytesIO()
        )

        with self.assertRaises(WechatError) as raised:
            _post_json("https://api.weixin.qq.com/test", {})

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat.urllib.request.urlopen")
    def test_dns_failure_is_safe_to_retry(self, urlopen):
        urlopen.side_effect = urllib.error.URLError(socket.gaierror("name not known"))

        with self.assertRaises(WechatError) as raised:
            _post_json("https://api.weixin.qq.com/test", {})

        self.assertTrue(raised.exception.retryable)
        self.assertFalse(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat.urllib.request.urlopen")
    def test_socket_timeout_has_unknown_submission_outcome(self, urlopen):
        urlopen.side_effect = socket.timeout("timed out")

        with self.assertRaises(WechatError) as raised:
            _post_json("https://api.weixin.qq.com/test", {})

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)

    @patch("youtube_to_wechat.wechat.urllib.request.urlopen")
    def test_invalid_json_has_unknown_submission_outcome(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = b"not-json"

        with self.assertRaises(WechatError) as raised:
            _post_json("https://api.weixin.qq.com/test", {})

        self.assertFalse(raised.exception.retryable)
        self.assertTrue(raised.exception.outcome_unknown)
```

- [ ] **Step 5: Run the transport tests and verify they fail**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_wechat.WechatApiTests -v
```

Expected: endpoint tests pass; HTTP/transport tests fail because raw urllib exceptions escape.

- [ ] **Step 6: Implement transport classification with `HTTPError` first**

Replace `_post_json` with:

```python
def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=60, context=ssl._create_unverified_context()
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise WechatError(
            f"WeChat HTTP {exc.code}: {exc.reason}",
            retryable=False,
            outcome_unknown=exc.code >= 500,
        ) from exc
    except (socket.timeout, TimeoutError, http.client.RemoteDisconnected, ConnectionResetError) as exc:
        raise WechatError(
            f"WeChat transport outcome unknown: {exc}",
            retryable=False,
            outcome_unknown=True,
        ) from exc
    except urllib.error.URLError as exc:
        request_not_sent = isinstance(exc.reason, (socket.gaierror, ConnectionRefusedError))
        raise WechatError(
            f"WeChat transport error: {exc}",
            retryable=request_not_sent,
            outcome_unknown=not request_not_sent,
        ) from exc
    except json.JSONDecodeError as exc:
        raise WechatError(
            f"WeChat returned invalid JSON: {exc}",
            retryable=False,
            outcome_unknown=True,
        ) from exc
```

The `HTTPError` branch must remain before `URLError` because `HTTPError` subclasses `URLError`.

- [ ] **Step 7: Run the low-level tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_wechat -v
```

Expected: all tests in `tests.test_wechat` pass.

- [ ] **Step 8: Commit the low-level API work**

```bash
git add src/youtube_to_wechat/wechat.py tests/test_wechat.py
git commit -m "feat: add wechat mass send api primitives"
```

### Task 2: Implement the batch-aware publisher state machine

**Files:**
- Modify: `src/youtube_to_wechat/publish.py`
- Test: `tests/test_publish.py`

- [ ] **Step 1: Write failing contract and default-mode tests**

Add imports and a reusable fixture helper to `tests/test_publish.py`:

```python
from youtube_to_wechat.publish import (
    PublishContext,
    PublishResult,
    WechatBatchState,
)


def make_article_files(temp_dir: str) -> tuple[Path, Path]:
    temp_path = Path(temp_dir)
    article_path = temp_path / "article.md"
    cover_path = temp_path / "cover.png"
    article_path.write_text("# Demo Title\n\n正文", encoding="utf-8")
    cover_path.write_bytes(b"cover")
    return article_path, cover_path
```

Add these tests to `PublishTests`:

```python
    @patch("youtube_to_wechat.publish.add_draft", return_value="draft-1")
    @patch("youtube_to_wechat.publish.upload_permanent_thumb", return_value="thumb-1")
    @patch("youtube_to_wechat.publish.get_access_token", return_value="token")
    def test_auto_publish_defaults_off(self, get_token, upload_thumb, add_draft):
        with tempfile.TemporaryDirectory() as temp_dir:
            article_path, cover_path = make_article_files(temp_dir)
            result = WechatDraftPublisher().publish(
                "Demo", "No.001", article_path, cover_path,
                {"WECHAT_APPID": "id", "WECHAT_APPSECRET": "secret", "WECHAT_AUTHOR": "author"},
                context=PublishContext(wechat_batch=WechatBatchState(mass_send_enabled=True)),
            )

        self.assertEqual(result.action, "draft")
        self.assertEqual(result.media_id, "draft-1")

    def test_batch_state_starts_with_zero_attempts(self):
        state = WechatBatchState(mass_send_enabled=True)
        self.assertEqual(state.mass_send_attempts, 0)
```

- [ ] **Step 2: Run the contract tests and verify they fail**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_publish.PublishTests.test_batch_state_starts_with_zero_attempts tests.test_publish.PublishTests.test_auto_publish_defaults_off -v
```

Expected: import errors for the new contract types.

- [ ] **Step 3: Add the shared publish contracts**

At the top of `publish.py`, import `dataclass`, `asdict`, and `Any`, then define:

```python
@dataclass
class WechatBatchState:
    mass_send_enabled: bool
    mass_send_attempts: int = 0


@dataclass
class PublishContext:
    wechat_batch: WechatBatchState | None = None


@dataclass
class PublishResult:
    action: str
    media_id: str = ""
    task_id: str = ""
    retried: bool = False
    error_code: int | None = None
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

Update the Protocol and all three publisher method signatures:

```python
class Publisher(Protocol):
    def publish(
        self,
        source_name: str,
        issue: str,
        article_path: Path,
        cover_path: Path,
        env: dict,
        context: PublishContext | None = None,
    ) -> PublishResult | None:
        raise NotImplementedError
```

`PushPlusPublisher.publish` and `EmailPublisher.publish` accept the same optional `context` argument and continue returning `None`. Update `publish_article` to accept `context`, pass it through, and return `PublishResult | None`.

- [ ] **Step 4: Write failing tests for normal batch routing and retry limits**

Import `WechatError` from `youtube_to_wechat.wechat` and add these tests:

```python
    @patch("youtube_to_wechat.publish.submit_publish", return_value="publish-2")
    @patch("youtube_to_wechat.publish.submit_mass_send", return_value="msg-1")
    @patch("youtube_to_wechat.publish.add_draft", side_effect=["draft-1", "draft-2"])
    @patch("youtube_to_wechat.publish.upload_permanent_thumb", return_value="thumb-1")
    @patch("youtube_to_wechat.publish.get_access_token", return_value="token")
    def test_first_article_mass_sends_and_second_publishes(
        self, get_token, upload_thumb, add_draft, mass_send, publish
    ):
        state = WechatBatchState(mass_send_enabled=True)
        context = PublishContext(wechat_batch=state)
        env = {
            "WECHAT_APPID": "id", "WECHAT_APPSECRET": "secret", "WECHAT_AUTHOR": "author",
            "WECHAT_AUTO_PUBLISH": "true", "WECHAT_MASS_SEND": "true",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            article_path, cover_path = make_article_files(temp_dir)
            first = WechatDraftPublisher().publish("Demo", "No.001", article_path, cover_path, env, context)
            second = WechatDraftPublisher().publish("Demo", "No.002", article_path, cover_path, env, context)

        self.assertEqual(first, PublishResult(action="mass_send", media_id="draft-1", task_id="msg-1"))
        self.assertEqual(second, PublishResult(action="publish", media_id="draft-2", task_id="publish-2"))
        self.assertEqual(state.mass_send_attempts, 1)
        mass_send.assert_called_once_with("token", "draft-1")
        publish.assert_called_once_with("token", "draft-2")

    @patch("youtube_to_wechat.publish.submit_publish", return_value="publish-1")
    @patch("youtube_to_wechat.publish.submit_mass_send")
    def test_safe_connection_failure_retries_once_then_publishes(self, mass_send, publish):
        mass_send.side_effect = WechatError("dns", retryable=True, outcome_unknown=False)
        state = WechatBatchState(mass_send_enabled=True)

        result = WechatDraftPublisher()._route_created_draft("token", "draft-1", state)

        self.assertEqual(mass_send.call_count, 2)
        self.assertEqual(state.mass_send_attempts, 2)
        publish.assert_called_once_with("token", "draft-1")
        self.assertEqual(result.action, "publish")
        self.assertTrue(result.retried)

    @patch("youtube_to_wechat.publish.submit_publish", return_value="publish-1")
    @patch("youtube_to_wechat.publish.submit_mass_send")
    def test_mass_send_disabled_publishes_without_mass_attempt(self, mass_send, publish):
        state = WechatBatchState(mass_send_enabled=False)

        result = WechatDraftPublisher()._route_created_draft("token", "draft-1", state)

        mass_send.assert_not_called()
        publish.assert_called_once_with("token", "draft-1")
        self.assertEqual(result.action, "publish")
        self.assertEqual(state.mass_send_attempts, 0)
```

- [ ] **Step 5: Run the routing tests and verify they fail**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_publish.PublishTests.test_first_article_mass_sends_and_second_publishes tests.test_publish.PublishTests.test_safe_connection_failure_retries_once_then_publishes -v
```

Expected: failures because `_route_created_draft` and automatic routing do not exist.

- [ ] **Step 6: Implement normal routing and the two-attempt cap**

Import `get_draft`, `submit_mass_send`, and `submit_publish` from `wechat.py`. Add these methods to `WechatDraftPublisher` and call `_route_created_draft` immediately after `add_draft` when `WECHAT_AUTO_PUBLISH` is true:

```python
    def _publish_created_draft(
        self, token: str, media_id: str, *, retried: bool = False, cause: WechatError | None = None
    ) -> PublishResult:
        try:
            publish_id = submit_publish(token, media_id)
            return PublishResult(
                action="publish",
                media_id=media_id,
                task_id=publish_id,
                retried=retried,
                error_code=cause.errcode if cause else None,
                error_message=str(cause) if cause else "",
            )
        except WechatError as exc:
            return PublishResult(
                action="draft",
                media_id=media_id,
                retried=retried,
                error_code=exc.errcode,
                error_message=str(exc),
            )

    def _route_created_draft(
        self, token: str, media_id: str, state: WechatBatchState
    ) -> PublishResult:
        if not state.mass_send_enabled or state.mass_send_attempts > 0:
            return self._publish_created_draft(token, media_id)

        last_error: WechatError | None = None
        while state.mass_send_attempts < 2:
            state.mass_send_attempts += 1
            try:
                msg_id = submit_mass_send(token, media_id)
                return PublishResult(
                    action="mass_send",
                    media_id=media_id,
                    task_id=msg_id,
                    retried=state.mass_send_attempts == 2,
                )
            except WechatError as exc:
                last_error = exc
                if exc.outcome_unknown:
                    return self._resolve_unknown_mass_send(token, media_id, exc, state)
                if not exc.retryable:
                    return self._publish_created_draft(
                        token, media_id, retried=state.mass_send_attempts == 2, cause=exc
                    )

        return self._publish_created_draft(token, media_id, retried=True, cause=last_error)
```

Inside `publish`, use this explicit guard:

```python
            if not _env_bool(env.get("WECHAT_AUTO_PUBLISH"), default=False):
                result = PublishResult(action="draft", media_id=media_id)
            else:
                state = context.wechat_batch if context and context.wechat_batch else WechatBatchState(
                    mass_send_enabled=_env_bool(env.get("WECHAT_MASS_SEND"), default=True)
                )
                result = self._route_created_draft(token, media_id, state)
            self._send_alerts(source_name, title, env, result)
            return result
```

- [ ] **Step 7: Write failing tests for uncertain outcomes and `40007` reconciliation**

Add these tests:

```python
    @patch("youtube_to_wechat.publish.get_draft", return_value=None)
    @patch("youtube_to_wechat.publish.submit_publish")
    @patch("youtube_to_wechat.publish.submit_mass_send")
    def test_timeout_and_consumed_draft_never_retry_or_publish(self, mass_send, publish, get_draft_mock):
        mass_send.side_effect = WechatError("timeout", outcome_unknown=True)
        state = WechatBatchState(mass_send_enabled=True)

        result = WechatDraftPublisher()._route_created_draft("token", "draft-1", state)

        self.assertEqual(mass_send.call_count, 1)
        publish.assert_not_called()
        get_draft_mock.assert_called_once_with("token", "draft-1")
        self.assertEqual(result.action, "mass_send_unknown")
        self.assertIn("很可能已群发", result.error_message)

    @patch("youtube_to_wechat.publish.get_draft", return_value={"news_item": []})
    @patch("youtube_to_wechat.publish.submit_publish")
    @patch("youtube_to_wechat.publish.submit_mass_send")
    def test_timeout_and_existing_draft_still_never_retry_or_publish(self, mass_send, publish, get_draft_mock):
        mass_send.side_effect = WechatError("timeout", outcome_unknown=True)

        result = WechatDraftPublisher()._route_created_draft(
            "token", "draft-1", WechatBatchState(mass_send_enabled=True)
        )

        self.assertEqual(mass_send.call_count, 1)
        publish.assert_not_called()
        self.assertEqual(result.action, "mass_send_unknown")
        self.assertIn("结果不确定", result.error_message)

    @patch("youtube_to_wechat.publish.get_draft")
    @patch("youtube_to_wechat.publish.submit_publish")
    @patch("youtube_to_wechat.publish.submit_mass_send")
    def test_timeout_and_failed_reconciliation_never_retry_or_publish(self, mass_send, publish, get_draft_mock):
        mass_send.side_effect = WechatError("timeout", outcome_unknown=True)
        get_draft_mock.side_effect = WechatError("invalid token", errcode=40014)

        result = WechatDraftPublisher()._route_created_draft(
            "token", "draft-1", WechatBatchState(mass_send_enabled=True)
        )

        self.assertEqual(mass_send.call_count, 1)
        publish.assert_not_called()
        self.assertEqual(result.action, "mass_send_unknown")
        self.assertIn("结果不确定", result.error_message)
```

- [ ] **Step 8: Run uncertain-outcome tests and verify they fail**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_publish.PublishTests.test_timeout_and_consumed_draft_never_retry_or_publish tests.test_publish.PublishTests.test_timeout_and_existing_draft_still_never_retry_or_publish tests.test_publish.PublishTests.test_timeout_and_failed_reconciliation_never_retry_or_publish -v
```

Expected: failures because `_resolve_unknown_mass_send` is not defined.

- [ ] **Step 9: Implement conservative reconciliation**

Add this method to `WechatDraftPublisher`:

```python
    def _resolve_unknown_mass_send(
        self,
        token: str,
        media_id: str,
        cause: WechatError,
        state: WechatBatchState,
    ) -> PublishResult:
        try:
            draft = get_draft(token, media_id)
        except WechatError as check_error:
            detail = f"群发结果不确定，请人工确认；草稿查询失败：{check_error}"
        else:
            if draft is None:
                detail = "草稿已被消费，很可能已群发，请人工确认；msg_id 未知"
            else:
                detail = "群发结果不确定，请人工确认；不得自动重试或发表首篇"
        return PublishResult(
            action="mass_send_unknown",
            media_id=media_id,
            retried=state.mass_send_attempts == 2,
            error_code=cause.errcode,
            error_message=detail,
        )
```

- [ ] **Step 10: Update result-aware notifications and test Protocol compatibility**

Change `_send_alerts` to accept `result: PublishResult` and select exactly one action line. A `draft` result without an error is the unchanged manual mode; a `draft` result with an error means automatic publication failed; a `publish` result with an error means mass send was explicitly rejected and the article was published instead:

```python
        if result.action == "draft":
            if result.error_message:
                action = f"自动发表失败，草稿已保留：{result.error_message}"
            else:
                action = "请前往微信公众号后台草稿箱预览并群发。"
        elif result.action == "mass_send":
            action = f"已提交群发，msg_id={result.task_id}。"
        elif result.action == "publish":
            action = f"已自动发表、未群发，publish_id={result.task_id}。"
            if result.error_message:
                action += f" 群发未提交：{result.error_message}"
        else:
            action = result.error_message
```

Add `publish_article` to the imports from `youtube_to_wechat.publish`, then add:

```python
    @patch("smtplib.SMTP_SSL")
    def test_email_publisher_accepts_publish_context(self, smtp_ssl):
        with tempfile.TemporaryDirectory() as temp_dir:
            article_path, cover_path = make_article_files(temp_dir)
            result = EmailPublisher().publish(
                source_name="Demo Source",
                issue="No.001",
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
                context=PublishContext(),
            )

        self.assertIsNone(result)
        smtp_ssl.return_value.__enter__.return_value.send_message.assert_called_once()

    def test_unknown_destination_returns_none_with_context(self):
        result = publish_article(
            destination="missing",
            source_name="Demo",
            issue="No.001",
            article_path=Path("article.md"),
            cover_path=Path("cover.png"),
            env={},
            context=PublishContext(),
        )

        self.assertIsNone(result)

    @patch("youtube_to_wechat.publish.urllib.request.urlopen")
    def test_wecom_notification_matches_draft_and_unknown_actions(self, urlopen):
        publisher = WechatDraftPublisher()
        env = {"WECOM_WEBHOOK": "https://example.com/webhook"}

        publisher._send_alerts(
            "Demo", "Title", env, PublishResult(action="draft", media_id="draft-1")
        )
        draft_body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("请前往微信公众号后台草稿箱预览并群发", draft_body["markdown"]["content"])

        publisher._send_alerts(
            "Demo",
            "Title",
            env,
            PublishResult(
                action="mass_send_unknown",
                media_id="draft-1",
                error_message="很可能已群发，请人工确认",
            ),
        )
        unknown_body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("很可能已群发，请人工确认", unknown_body["markdown"]["content"])

        publisher._send_alerts(
            "Demo",
            "Title",
            env,
            PublishResult(
                action="publish",
                media_id="draft-1",
                task_id="publish-1",
                error_code=-1,
                error_message="system error",
            ),
        )
        fallback_body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("已自动发表、未群发", fallback_body["markdown"]["content"])
        self.assertIn("群发未提交：system error", fallback_body["markdown"]["content"])
```

Add `import json` to `tests/test_publish.py` for decoding the webhook request.

- [ ] **Step 11: Add a test proving a failed draft does not consume the batch candidate**

```python
    @patch("youtube_to_wechat.publish.submit_mass_send", return_value="msg-2")
    @patch(
        "youtube_to_wechat.publish.add_draft",
        side_effect=[WechatError("draft failed", errcode=53503), "draft-2"],
    )
    @patch("youtube_to_wechat.publish.upload_permanent_thumb", return_value="thumb-1")
    @patch("youtube_to_wechat.publish.get_access_token", return_value="token")
    def test_failed_draft_does_not_consume_mass_send_candidate(
        self, get_token, upload_thumb, add_draft, mass_send
    ):
        state = WechatBatchState(mass_send_enabled=True)
        context = PublishContext(wechat_batch=state)
        env = {
            "WECHAT_APPID": "id", "WECHAT_APPSECRET": "secret", "WECHAT_AUTHOR": "author",
            "WECHAT_AUTO_PUBLISH": "true", "WECHAT_MASS_SEND": "true",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            article_path, cover_path = make_article_files(temp_dir)
            first = WechatDraftPublisher().publish("Demo", "No.001", article_path, cover_path, env, context)
            second = WechatDraftPublisher().publish("Demo", "No.002", article_path, cover_path, env, context)

        self.assertIsNone(first)
        self.assertEqual(second.action, "mass_send")
        self.assertEqual(second.media_id, "draft-2")
        self.assertEqual(state.mass_send_attempts, 1)
        mass_send.assert_called_once_with("token", "draft-2")
```

- [ ] **Step 12: Run publisher tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_publish -v
```

Expected: all tests in `tests.test_publish` pass; no route can call `submit_mass_send` more than twice.

- [ ] **Step 13: Commit the publisher state machine**

```bash
git add src/youtube_to_wechat/publish.py tests/test_publish.py
git commit -m "feat: route wechat batch publication safely"
```

### Task 3: Integrate one shared batch context and persist results

**Files:**
- Modify: `scripts/process_sources.py`
- Test: `tests/test_process_sources.py`

- [ ] **Step 1: Write a failing result-persistence test**

Add `import json`, `_record_publish_result`, and `PublishResult` to the existing imports in `tests/test_process_sources.py`, then add this method to `ProcessSourcesTests`:

```python
import json

from scripts.process_sources import _record_publish_result
from youtube_to_wechat.publish import PublishResult


    def test_record_publish_result_updates_run_json_without_losing_existing_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_json_path = Path(temp_dir) / "run.json"
            run_json_path.write_text(
                json.dumps({"status": "ok", "issue": "No.001"}, ensure_ascii=False),
                encoding="utf-8",
            )

            _record_publish_result(
                run_json_path,
                PublishResult(action="mass_send", media_id="draft-1", task_id="msg-1"),
            )

            saved = json.loads(run_json_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "ok")
            self.assertEqual(
                saved["wechat_publish"],
                {
                    "action": "mass_send",
                    "media_id": "draft-1",
                    "task_id": "msg-1",
                    "retried": False,
                    "error_code": None,
                    "error_message": "",
                },
            )
```

- [ ] **Step 2: Run the persistence test and verify it fails**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_process_sources.ProcessSourcesTests.test_record_publish_result_updates_run_json_without_losing_existing_fields -v
```

Expected: import error for `_record_publish_result`.

- [ ] **Step 3: Add the persistence helper**

Import `PublishResult` from `youtube_to_wechat.publish` at module scope, then add this module-level function to `scripts/process_sources.py`:

```python
def _record_publish_result(run_json_path: Path, result: PublishResult | None) -> None:
    if result is None or not run_json_path.exists():
        return
    try:
        run_data = json.loads(run_json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    run_data["wechat_publish"] = result.to_dict()
    run_json_path.write_text(
        json.dumps(run_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
```

- [ ] **Step 4: Create one context per process and pass it to every publisher**

Immediately after loading `env_vars` in `main()`, import and create the shared context:

```python
    from youtube_to_wechat.publish import PublishContext, WechatBatchState, _env_bool

    publish_context = PublishContext(
        wechat_batch=WechatBatchState(
            mass_send_enabled=_env_bool(env_vars.get("WECHAT_MASS_SEND"), default=True)
        )
    )
```

Replace the per-destination call with:

```python
                    result = publish_article(
                        destination=destination,
                        source_name=candidate.source.name,
                        issue=issue_val,
                        article_path=output_dir / "article.md",
                        cover_path=cover_path,
                        env=env,
                        context=publish_context,
                    )
                    _record_publish_result(run_json_path, result)
```

The same `publish_context` instance must be reused for all candidates and destinations in this one `main()` call.

- [ ] **Step 5: Run process-source tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_process_sources -v
```

Expected: all `tests.test_process_sources` tests pass, including result persistence.

- [ ] **Step 6: Commit batch integration**

```bash
git add scripts/process_sources.py tests/test_process_sources.py
git commit -m "feat: persist wechat batch publish outcomes"
```

### Task 4: Document and verify deployment switches

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Add the exact switch defaults to `.env.example`**

Insert after `WECHAT_AUTHOR=`:

```dotenv
# false: only create drafts; true: automatically publish after draft creation.
WECHAT_AUTO_PUBLISH=false
# Effective only when auto publish is enabled. Defaults to true if omitted.
WECHAT_MASS_SEND=true
```

- [ ] **Step 2: Update README behavior and deployment instructions**

Replace “可选推送到微信公众号草稿箱，不自动发布” with wording that says draft-only remains the safe default. Under WeChat configuration, document:

```text
WECHAT_AUTO_PUBLISH=false
WECHAT_MASS_SEND=true
```

Document the three modes exactly:

- `WECHAT_AUTO_PUBLISH=false`: all articles remain drafts.
- `WECHAT_AUTO_PUBLISH=true` and `WECHAT_MASS_SEND=false`: all articles are publicly published without follower pushes.
- `WECHAT_AUTO_PUBLISH=true` and `WECHAT_MASS_SEND=true`: the first successful draft in the cron batch is mass-sent; later drafts are publicly published without follower pushes.

Also state that the account must expose the required mass-send and free-publish permissions, the VPS IP must be whitelisted, and an uncertain first-item mass-send outcome stops automatic action for that item and requires manual verification.

- [ ] **Step 3: Verify documentation consistency**

Run:

```bash
rg -n "WECHAT_AUTO_PUBLISH|WECHAT_MASS_SEND|草稿|群发|自动发表" .env.example README.md docs/superpowers/specs/2026-08-23-wechat-automatic-mass-send-design.md
git diff --check
```

Expected: both variables and all three modes are present; `git diff --check` emits no errors.

- [ ] **Step 4: Commit documentation**

```bash
git add .env.example README.md
git commit -m "docs: explain wechat automatic publishing switches"
```

### Task 5: Run full verification

**Files:**
- Verify only; no planned file changes.

- [ ] **Step 1: Run the focused WeChat suites**

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_wechat tests.test_publish tests.test_process_sources -v
```

Expected: every focused test passes.

- [ ] **Step 2: Run the complete test suite**

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Expected: all repository tests pass with zero failures and zero errors.

- [ ] **Step 3: Verify repository hygiene and acceptance coverage**

```bash
git diff --check
git status --short
git log -5 --oneline
```

Expected: no uncommitted files from this feature; pre-existing unrelated untracked files remain untouched. Recent commits show the API primitives, publisher state machine, process integration, and documentation commits.

- [ ] **Step 4: Perform one controlled VPS acceptance run**

On an account with mass-send and free-publish permissions and the VPS IP whitelisted, set:

```dotenv
WECHAT_AUTO_PUBLISH=true
WECHAT_MASS_SEND=true
```

Run the existing cron command once with at least two queued articles. Confirm from WeChat and each `run.json` that the first successful draft has `action=mass_send`, later drafts have `action=publish`, and no later article consumes a second mass-send attempt. If the first result is `mass_send_unknown`, do not rerun the batch; inspect the public-account backend manually.
