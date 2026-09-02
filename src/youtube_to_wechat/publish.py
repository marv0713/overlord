import json
import re
import smtplib
import sys
import urllib.request
from dataclasses import asdict, dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from youtube_to_wechat.article_prepare import (
    extract_digest,
    extract_wechat_title,
    strip_leading_title_block,
)
from youtube_to_wechat.wechat import (
    WechatError,
    _markdown_to_html,
    add_draft,
    build_draft_article,
    get_draft,
    get_access_token,
    require_env,
    submit_mass_send,
    submit_publish,
    upload_permanent_thumb,
)


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

    def to_dict(self) -> dict:
        return asdict(self)


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
        ...


class WechatDraftPublisher:
    """Pushes article to WeChat Official Account as a draft."""
    def publish(
        self,
        source_name: str,
        issue: str,
        article_path: Path,
        cover_path: Path,
        env: dict,
        context: PublishContext | None = None,
    ) -> PublishResult | None:
        if not cover_path.exists():
            print(f"[{source_name}] WechatDraftPublisher: missing cover image at {cover_path}, skipping.", file=sys.stderr)
            return

        text = article_path.read_text(encoding="utf-8")
        title = extract_wechat_title(text, article_path)
        digest = extract_digest(text)

        try:
            content = _markdown_to_html(strip_leading_title_block(text))
            required = require_env(env, ["WECHAT_APPID", "WECHAT_APPSECRET", "WECHAT_AUTHOR"])
            token = get_access_token(required["WECHAT_APPID"], required["WECHAT_APPSECRET"])
            thumb_media_id = upload_permanent_thumb(token, cover_path)
            article_payload = build_draft_article(
                title=title,
                author=required["WECHAT_AUTHOR"],
                digest=digest,
                content=content,
                thumb_media_id=thumb_media_id,
                column="炼金投研",
            )
            media_id = add_draft(token, article_payload)
            print(f"[{source_name}] WechatDraftPublisher: draft created {media_id}")
            result = self._route_created_draft(token, media_id, env, context)
            self._log_result(source_name, result)
            self._send_alerts(source_name, title, env, result)
            return result
        except WechatError as exc:
            print(f"[{source_name}] WechatDraftPublisher Error: {exc}", file=sys.stderr)

    def _route_created_draft(
        self,
        token: str,
        media_id: str,
        env: dict,
        context: PublishContext | None,
    ) -> PublishResult:
        if not _env_bool(env.get("WECHAT_AUTO_PUBLISH"), default=False):
            return PublishResult(action="draft", media_id=media_id)

        if context is None or context.wechat_batch is None:
            return self._publish_created_draft(token, media_id)

        state = context.wechat_batch
        if not state.mass_send_enabled or state.mass_send_attempts > 0:
            return self._publish_created_draft(token, media_id)

        state.mass_send_attempts += 1
        try:
            msg_id = submit_mass_send(token, media_id)
            return PublishResult(action="mass_send", media_id=media_id, task_id=msg_id)
        except WechatError as exc:
            return self._handle_mass_failure(token, media_id, state, exc)

    def _handle_mass_failure(
        self,
        token: str,
        media_id: str,
        state: WechatBatchState,
        error: WechatError,
    ) -> PublishResult:
        if error.outcome_unknown:
            return self._reconcile_mass_send(token, media_id, error)

        if error.errcode is not None or not error.retryable:
            return self._publish_created_draft(token, media_id, error)

        # The initial candidate always starts at zero, so a retry is bounded to
        # exactly one more mass-send call for this batch.
        if state.mass_send_attempts < 2:
            state.mass_send_attempts += 1
            try:
                msg_id = submit_mass_send(token, media_id)
                return PublishResult(action="mass_send", media_id=media_id, task_id=msg_id, retried=True)
            except WechatError as retry_error:
                if retry_error.outcome_unknown:
                    result = self._reconcile_mass_send(token, media_id, retry_error)
                    result.retried = True
                    return result
                return self._publish_created_draft(token, media_id, retry_error, retried=True)

        return self._publish_created_draft(token, media_id, error, retried=True)

    def _publish_created_draft(
        self,
        token: str,
        media_id: str,
        mass_error: WechatError | None = None,
        retried: bool = False,
    ) -> PublishResult:
        try:
            publish_id = submit_publish(token, media_id)
            return PublishResult(
                action="publish",
                media_id=media_id,
                task_id=publish_id,
                retried=retried,
                error_code=mass_error.errcode if mass_error else None,
                error_message=str(mass_error) if mass_error else "",
            )
        except WechatError as exc:
            error_message = self._compose_publish_error(exc, mass_error)
            if exc.outcome_unknown:
                return PublishResult(
                    action="publish_unknown",
                    media_id=media_id,
                    retried=retried,
                    error_code=exc.errcode,
                    error_message=error_message,
                )
            return PublishResult(
                action="draft",
                media_id=media_id,
                retried=retried,
                error_code=exc.errcode,
                error_message=error_message,
            )

    @staticmethod
    def _compose_publish_error(publish_error: WechatError, mass_error: WechatError | None) -> str:
        if publish_error.outcome_unknown:
            message = f"自动发表结果不确定，请人工确认。发布错误: {publish_error}"
        else:
            message = f"自动发布失败，草稿已保留。发布错误: {publish_error}"
        if mass_error:
            message += f" 群发降级原因: {mass_error}"
        return message

    def _reconcile_mass_send(self, token: str, media_id: str, error: WechatError) -> PublishResult:
        try:
            draft = get_draft(token, media_id)
        except WechatError as query_error:
            return PublishResult(
                action="mass_send_unknown",
                media_id=media_id,
                error_code=error.errcode,
                error_message=f"群发结果不确定；原始群发错误: {error}；查询草稿失败: {query_error}",
            )
        if draft is None:
            return PublishResult(
                action="mass_send_unknown",
                media_id=media_id,
                error_code=error.errcode,
                error_message=f"群发很可能已群发，msg_id 未知；原始群发错误: {error}；请前往微信公众号后台核对。",
            )
        return PublishResult(
            action="mass_send_unknown",
            media_id=media_id,
            error_code=error.errcode,
            error_message=f"群发结果不确定；原始群发错误: {error}；草稿仍存在，请前往微信公众号后台核对。",
        )

    @staticmethod
    def _log_result(source_name: str, result: PublishResult) -> None:
        if result.action == "draft":
            detail = result.error_message or "草稿已保留，请前往微信公众号后台草稿箱预览并群发。"
        elif result.action == "mass_send":
            detail = f"已提交群发，msg_id: {result.task_id}。"
        elif result.action == "publish":
            detail = f"已自动发布（未群发），publish_id: {result.task_id}。"
            if result.error_message:
                detail += f" 群发降级原因: {result.error_message}"
        elif result.action == "mass_send_unknown":
            detail = f"群发结果不确定，请人工确认。{result.error_message}"
        else:
            detail = result.error_message
            if "自动发表结果不确定" not in detail:
                detail = f"自动发表结果不确定，请人工确认。{detail}"
        print(f"[{source_name}] WechatDraftPublisher: {detail}")

    def _send_alerts(self, source_name: str, title: str, env: dict, result: PublishResult) -> None:
        wecom_webhook = env.get("WECOM_WEBHOOK")
        if wecom_webhook:
            operation = self._alert_operation(result)
            msg = {
                "msgtype": "markdown",
                "markdown": {
                    "content": f"🎉 **新文章草稿已生成并推送**\n\n> **来源**: {source_name}\n> **标题**: {title}\n> **操作**: {operation}"
                }
            }
            try:
                req = urllib.request.Request(
                    wecom_webhook,
                    data=json.dumps(msg).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10)
                print(f"[{source_name}] WeCom alert sent.")
            except Exception as e:
                print(f"[{source_name}] WeCom alert failed: {e}", file=sys.stderr)

    @staticmethod
    def _alert_operation(result: PublishResult) -> str:
        if result.action == "draft":
            if result.error_message:
                return f"自动发布失败，草稿已保留。错误: {result.error_message}"
            return "请前往微信公众号后台草稿箱预览并群发。"
        if result.action == "mass_send":
            return f"已提交群发，msg_id: {result.task_id}。"
        if result.action == "publish":
            message = f"已自动发布（未群发），publish_id: {result.task_id}。"
            if result.error_message:
                message += f" 群发降级原因: {result.error_message}"
            return message
        if result.action == "publish_unknown":
            message = result.error_message
            if "自动发表结果不确定" not in message:
                message = f"自动发表结果不确定，请人工确认。{message}"
            return f"{message} 请手动核对后台。"
        return f"群发结果不确定，请手动核对后台。{result.error_message}"


class PushPlusPublisher:
    """Pushes the full article Markdown text to personal WeChat via PushPlus."""
    def publish(
        self,
        source_name: str,
        issue: str,
        article_path: Path,
        cover_path: Path,
        env: dict,
        context: PublishContext | None = None,
    ) -> None:
        token = env.get("PUSHPLUS_TOKEN")
        if not token:
            print(f"[{source_name}] PushPlusPublisher: PUSHPLUS_TOKEN missing in env.", file=sys.stderr)
            return

        text = article_path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else f"{source_name} 最新内容"

        # PushPlus has length limits. Let's safely truncate at 15000 characters just in case.
        if len(text) > 15000:
            text = text[:15000] + "\n\n...(文章过长已截断)..."

        # Prepend some meta
        full_content = f"**来源：** {source_name}\n**期号：** {issue}\n\n---\n\n{text}"

        msg = {
            "token": token,
            "title": f"[{source_name}] {title[:20]}...",
            "content": full_content,
            "template": "markdown"
        }
        req = urllib.request.Request("http://www.pushplus.plus/send", data=json.dumps(msg).encode('utf-8'), headers={'Content-Type': 'application/json'})
        try:
            urllib.request.urlopen(req, timeout=10)
            print(f"[{source_name}] PushPlusPublisher: Full article delivered successfully.")
        except Exception as e:
            print(f"[{source_name}] PushPlusPublisher Error: {e}", file=sys.stderr)


class EmailPublisher:
    """Sends the full article Markdown by SMTP email."""
    def publish(
        self,
        source_name: str,
        issue: str,
        article_path: Path,
        cover_path: Path,
        env: dict,
        context: PublishContext | None = None,
    ) -> None:
        text = article_path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else f"{source_name} 最新内容"
        subject = f"[{source_name}] {title}"

        try:
            _deliver_email(subject, text, env)
            print(f"[{source_name}] EmailPublisher: email sent to {env['EMAIL_TO']}.")
        except Exception as e:
            print(f"[{source_name}] EmailPublisher Error: {e}", file=sys.stderr)


def _deliver_email(subject: str, body: str, env: dict) -> None:
    """Send a plain-text email through the SMTP settings in ``env``. Raises on failure."""
    from_addr = env.get("EMAIL_FROM") or env.get("SMTP_FROM") or env.get("SMTP_USER")
    required = {
        "SMTP_HOST": env.get("SMTP_HOST"),
        "SMTP_USER": env.get("SMTP_USER"),
        "SMTP_PASSWORD": env.get("SMTP_PASSWORD"),
        "EMAIL_TO": env.get("EMAIL_TO"),
        "EMAIL_FROM/SMTP_FROM": from_addr,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ValueError(f"missing env {', '.join(missing)}")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = env["EMAIL_TO"]
    msg.set_content(body)

    host = env["SMTP_HOST"]
    port = int(env.get("SMTP_PORT") or 465)
    use_ssl = _env_bool(env.get("SMTP_USE_SSL"), default=(port == 465))
    use_tls = _env_bool(env.get("SMTP_USE_TLS"), default=(port == 587))
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
            smtp.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if use_tls:
                smtp.starttls()
            smtp.login(env["SMTP_USER"], env["SMTP_PASSWORD"])
            smtp.send_message(msg)


def send_crash_email(source_name: str, error_message: str, env: dict) -> None:
    """Send a best-effort email alert when a whole run crashes.

    Unlike per-item ``send_failure_alert`` (WeCom/PushPlus), this uses the SMTP
    channel so a hard process crash still reaches the operator's mailbox.
    """
    subject = f"[{source_name}] ⚠️ 任务崩溃"
    body = (
        "Overlord 任务异常退出，未能正常处理内容。\n\n"
        f"来源：{source_name}\n"
        f"错误：{error_message}\n\n"
        "请登录服务器查看日志（logs/cron.log）排查。"
    )
    try:
        _deliver_email(subject, body, env)
        print(f"[{source_name}] Crash alert email sent to {env.get('EMAIL_TO')}.")
    except Exception as e:
        print(f"[{source_name}] Crash alert email failed: {e}", file=sys.stderr)


def send_failure_alert(
    source_name: str,
    error_message: str,
    item_title: str = "",
    issue: str = "",
    env: dict | None = None,
) -> None:
    """Send failure notification via configured channels (WeCom webhook, PushPlus).

    Reuses the same PushPlus / WeCom webhook infrastructure already used for
    success alerts in WechatDraftPublisher._send_alerts().
    """
    if env is None:
        env = {}

    source_label = f"{source_name}"
    if item_title:
        source_label += f" — {item_title[:40]}"

    # WeCom webhook (same mechanism as success alerts)
    wecom_webhook = env.get("WECOM_WEBHOOK")
    if wecom_webhook:
        lines = [
            f"❌ **任务处理失败**",
            f"",
            f"> **来源**: {source_label}",
            f"> **错误**: {error_message[:200]}",
        ]
        if issue:
            lines.append(f"> **期号**: {issue}")
        lines.append(f"> **操作**: 请检查日志排查问题。")

        msg = {"msgtype": "markdown", "markdown": {"content": "\n".join(lines)}}
        req = urllib.request.Request(
            wecom_webhook,
            data=json.dumps(msg).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            print(f"[{source_name}] Failure alert sent via WeCom.")
        except Exception as e:
            print(f"[{source_name}] Failure alert via WeCom failed: {e}", file=sys.stderr)

    # PushPlus (pushes directly to personal WeChat)
    pushplus_token = env.get("PUSHPLUS_TOKEN")
    if pushplus_token:
        content = f"**来源：** {source_label}\n**错误：** {error_message[:500]}"
        if issue:
            content = f"**期号：** {issue}\n" + content
        msg = {
            "token": pushplus_token,
            "title": f"❌ 任务失败: {source_name}",
            "content": content,
            "template": "markdown",
        }
        req = urllib.request.Request(
            "http://www.pushplus.plus/send",
            data=json.dumps(msg).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            print(f"[{source_name}] Failure alert sent via PushPlus.")
        except Exception as e:
            print(f"[{source_name}] Failure alert via PushPlus failed: {e}", file=sys.stderr)


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_PUBLISHERS: dict[str, Publisher] = {
    "wechat_draft": WechatDraftPublisher(),
    "pushplus": PushPlusPublisher(),
    "email": EmailPublisher(),
}


def publish_article(
    destination: str,
    source_name: str,
    issue: str,
    article_path: Path,
    cover_path: Path,
    env: dict,
    context: PublishContext | None = None,
) -> PublishResult | None:
    """Entry point to route to the correct publisher based on destination identifier."""
    publisher = _PUBLISHERS.get(destination)
    if not publisher:
        print(f"[{source_name}] Unknown destination '{destination}', skipping.", file=sys.stderr)
        return
    return publisher.publish(source_name, issue, article_path, cover_path, env, context)
