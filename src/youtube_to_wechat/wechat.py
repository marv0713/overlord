import ssl
import json
import mimetypes
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional


class WechatError(RuntimeError):
    pass


MOBILE_TEXT_STYLE = (
    "margin: 0 0 0.58em; "
    "line-height: 1.72; "
    "font-size: 16px; "
    "letter-spacing: 0; "
    "text-align: left; "
    "word-break: break-word; "
    "color: #333;"
)
MOBILE_LIST_STYLE = (
    "margin: 0 0 0.46em; "
    "padding-left: 0; "
    "line-height: 1.72; "
    "font-size: 16px; "
    "letter-spacing: 0; "
    "text-align: left; "
    "word-break: break-word; "
    "color: #333;"
)


def _markdown_to_html(md: str) -> str:
    import html
    import re

    def inline(text: str) -> str:
        escaped = html.escape(text, quote=False)
        escaped = re.sub(
            r"\*\*(.+?)\*\*",
            r"<strong>\1</strong>",
            escaped,
        )
        return re.sub(
            r"`(.+?)`",
            r"<code style='background: #f4f4f4; padding: 2px 4px; font-family: monospace;'>\1</code>",
            escaped,
        )

    lines = []
    in_quote = False
    for line in md.splitlines():
        stripped = line.strip()

        # Handle Blockquotes
        if stripped.startswith("> "):
            if not in_quote:
                lines.append("<blockquote style='border-left: 3px solid #d4af37; padding: 8px 12px; color: #555; margin: 0.75em 0 0.85em; font-style: italic; background: #fdfaf2;'>")
                in_quote = True
            lines.append(f"<p style='{MOBILE_TEXT_STYLE}'>{inline(stripped[2:])}</p>")
            continue
        elif in_quote:
            lines.append("</blockquote>")
            in_quote = False

        # Handle Headings
        if line.startswith("# "):
            lines.append(f"<h1 style='font-size: 22px; line-height: 1.35; color: #d4af37; border-bottom: 2px solid #d4af37; padding-bottom: 8px; margin: 1.05em 0 0.68em; text-align: left;'>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            lines.append(f"<h2 style='font-size: 19px; line-height: 1.45; color: #333; margin: 1.15em 0 0.56em; border-left: 4px solid #d4af37; padding-left: 9px; text-align: left;'>{inline(line[3:])}</h2>")
        elif line.startswith("### "):
            lines.append(f"<h3 style='font-size: 17px; line-height: 1.45; color: #444; margin: 1em 0 0.5em; text-align: left;'>🔸 {inline(line[4:])}</h3>")
        # Handle Lists (Both - and *)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            content = stripped[2:]
            lines.append(f"<p style='{MOBILE_LIST_STYLE}'>• {inline(content)}</p>")
        # Handle Images
        elif stripped.startswith("![") and "](" in stripped:
            alt_match = re.search(r"!\[(.*?)\]\((.*?)\)", stripped)
            if alt_match:
                alt, src = alt_match.groups()
                escaped_alt = html.escape(alt, quote=True)
                escaped_src = html.escape(src, quote=True)
                lines.append(f"<p style='text-align: center; margin: 1em 0;'><img src='{escaped_src}' alt='{escaped_alt}' style='max-width: 100%; border-radius: 8px;'></p>")
        elif stripped:
            lines.append(f"<p style='{MOBILE_TEXT_STYLE}'>{inline(line)}</p>")

    if in_quote:
        lines.append("</blockquote>")

    return "\n".join(lines)


def parse_env_text(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_env(path: Path = Path(".env")) -> dict[str, str]:
    values = dict(os.environ)
    if path.exists():
        values.update(parse_env_text(path.read_text(encoding="utf-8")))
    return values


def require_env(env: dict[str, str], keys: list[str]) -> dict[str, str]:
    missing = [key for key in keys if not env.get(key)]
    if missing:
        raise WechatError(f"Missing required environment variables: {', '.join(missing)}")
    return {key: env[key] for key in keys}


def get_access_token(appid: str, appsecret: str) -> str:
    params = urllib.parse.urlencode(
        {"grant_type": "client_credential", "appid": appid, "secret": appsecret}
    )
    data = _get_json(f"https://api.weixin.qq.com/cgi-bin/token?{params}")
    token = data.get("access_token")
    if not token:
        raise WechatError(f"WeChat access_token error: {data}")
    return token


def upload_permanent_thumb(access_token: str, image_path: Path) -> str:
    data = _multipart_upload(
        url=f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={urllib.parse.quote(access_token)}&type=thumb",
        field_name="media",
        file_path=image_path,
    )
    media_id = data.get("media_id")
    if not media_id:
        raise WechatError(f"WeChat thumb upload error: {data}")
    return media_id


def upload_content_image(access_token: str, image_path: Path) -> str:
    """Upload an image for use in article content (returns a persistent URL)."""
    data = _multipart_upload(
        url=f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={urllib.parse.quote(access_token)}",
        field_name="media",
        file_path=image_path,
    )
    url = data.get("url")
    if not url:
        raise WechatError(f"WeChat content image upload error: {data}")
    return url


def add_draft(access_token: str, article: dict[str, Any]) -> str:
    data = _post_json(
        f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={urllib.parse.quote(access_token)}",
        {"articles": [article]},
    )
    media_id = data.get("media_id")
    if not media_id:
        raise WechatError(f"WeChat add draft error: {data}")
    return media_id


def build_draft_article(
    title: str,
    author: str,
    digest: str,
    content: str,
    thumb_media_id: str,
    column: str,
    source_url: str = "",
    open_comment: bool = True,
    ai_generated: bool = True,
) -> dict[str, Any]:
    clean_title = title.replace(f"{column}｜", "").replace(f"{column} |", "").strip()
    final_title = f"{column}｜{clean_title}"

    if ai_generated:
        ai_notice = (
            '<p style="color:#888888;font-size:13px;border-left:3px solid #d4af37;'
            'padding:6px 10px;margin:0 0 16px 0;">'
            '⚠️ 本文由 AI 辅助对海外公开资讯进行整理输出，经人工审阅后发布。'
            '内容仅供参考，不构成投资建议。'
            '</p>'
        )
        content = ai_notice + content

    return {
        "title": final_title[:64],
        "author": author,
        "digest": digest[:120],
        "content": content,
        "content_source_url": source_url,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 1 if open_comment else 0,
        "only_fans_can_comment": 0,
    }


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=30, context=ssl._create_unverified_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60, context=ssl._create_unverified_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def _multipart_upload(url: str, field_name: str, file_path: Path) -> dict[str, Any]:
    boundary = "----overlord-wechat-boundary"
    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()
    parts = [
        f"--{boundary}\r\n".encode("utf-8"),
        (
            f'Content-Disposition: form-data; name="{field_name}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8"),
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    request = urllib.request.Request(
        url,
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60, context=ssl._create_unverified_context()) as response:
        return json.loads(response.read().decode("utf-8"))
