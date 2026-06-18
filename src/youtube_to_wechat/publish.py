import json
import re
import smtplib
import sys
import urllib.request
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
    get_access_token,
    require_env,
    upload_permanent_thumb,
)


class Publisher(Protocol):
    def publish(self, source_name: str, issue: str, article_path: Path, cover_path: Path, env: dict) -> None:
        ...


class WechatDraftPublisher:
    """Pushes article to WeChat Official Account as a draft."""
    def publish(self, source_name: str, issue: str, article_path: Path, cover_path: Path, env: dict) -> None:
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
            
            # Send notification alerts if configured
            self._send_alerts(source_name, title, env)
        except WechatError as exc:
            print(f"[{source_name}] WechatDraftPublisher Error: {exc}", file=sys.stderr)

    def _send_alerts(self, source_name: str, title: str, env: dict) -> None:
        wecom_webhook = env.get("WECOM_WEBHOOK")
        if wecom_webhook:
            msg = {
                "msgtype": "markdown",
                "markdown": {
                    "content": f"🎉 **新文章草稿已生成并推送**\n\n> **来源**: {source_name}\n> **标题**: {title}\n> **操作**: 请前往微信公众号后台草稿箱预览并群发。"
                }
            }
            req = urllib.request.Request(wecom_webhook, data=json.dumps(msg).encode('utf-8'), headers={'Content-Type': 'application/json'})
            try:
                urllib.request.urlopen(req, timeout=10)
                print(f"[{source_name}] WeCom alert sent.")
            except Exception as e:
                print(f"[{source_name}] WeCom alert failed: {e}", file=sys.stderr)


class PushPlusPublisher:
    """Pushes the full article Markdown text to personal WeChat via PushPlus."""
    def publish(self, source_name: str, issue: str, article_path: Path, cover_path: Path, env: dict) -> None:
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
    def publish(self, source_name: str, issue: str, article_path: Path, cover_path: Path, env: dict) -> None:
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
            print(f"[{source_name}] EmailPublisher: missing env {', '.join(missing)}.", file=sys.stderr)
            return

        text = article_path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else f"{source_name} 最新内容"
        subject = f"[{source_name}] {title}"

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = env["EMAIL_TO"]
        msg.set_content(text)

        host = env["SMTP_HOST"]
        port = int(env.get("SMTP_PORT") or 465)
        use_ssl = _env_bool(env.get("SMTP_USE_SSL"), default=(port == 465))
        use_tls = _env_bool(env.get("SMTP_USE_TLS"), default=(port == 587))
        try:
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
            print(f"[{source_name}] EmailPublisher: email sent to {env['EMAIL_TO']}.")
        except Exception as e:
            print(f"[{source_name}] EmailPublisher Error: {e}", file=sys.stderr)


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_PUBLISHERS: dict[str, Publisher] = {
    "wechat_draft": WechatDraftPublisher(),
    "pushplus": PushPlusPublisher(),
    "email": EmailPublisher(),
}


def publish_article(destination: str, source_name: str, issue: str, article_path: Path, cover_path: Path, env: dict) -> None:
    """Entry point to route to the correct publisher based on destination identifier."""
    publisher = _PUBLISHERS.get(destination)
    if not publisher:
        print(f"[{source_name}] Unknown destination '{destination}', skipping.", file=sys.stderr)
        return
    publisher.publish(source_name, issue, article_path, cover_path, env)
