#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

from youtube_to_wechat.article_prepare import (
    extract_digest,
    extract_wechat_title,
    strip_leading_title_block,
)
from youtube_to_wechat.wechat import (
    WechatError,
    add_draft,
    build_draft_article,
    get_access_token,
    load_env,
    require_env,
    upload_permanent_thumb,
    upload_content_image,
    _markdown_to_html,
)


def _read_article(article_path: Path, access_token: str = "") -> tuple[str, str, str]:
    text = article_path.read_text(encoding="utf-8")
    title = _extract_title(text, article_path)
    digest = _extract_digest(text)
    if article_path.suffix.lower() == ".html":
        content = text
    else:
        content = _markdown_to_html(strip_leading_title_block(text))

    # NEW: Find and upload local images to WeChat
    if access_token:
        content = _replace_local_images(content, article_path.parent, access_token)

    return title, digest, content


def _replace_local_images(content: str, base_dir: Path, access_token: str) -> str:
    import re
    def img_replacer(match):
        img_tag = match.group(0)
        src_match = re.search(r'src=[\'"](.+?)[\'"]', img_tag)
        if not src_match:
            return img_tag

        src = src_match.group(1)
        if src.startswith(("http://", "https://", "data:")):
            return img_tag

        local_path = base_dir / src
        if local_path.exists():
            print(f"Uploading embedded image: {src} ...")
            try:
                wechat_url = upload_content_image(access_token, local_path)
                return img_tag.replace(src, wechat_url)
            except Exception as e:
                print(f"Warning: Failed to upload image {src}: {e}")
        return img_tag

    return re.sub(r'<img [^>]+>', img_replacer, content)


def _extract_title(text: str, article_path: Path) -> str:
    return extract_wechat_title(text, article_path)


def _extract_digest(text: str) -> str:
    return extract_digest(text)


def _source_url(article_path: Path) -> str:
    meta_path = article_path.parent / "meta.json"
    if not meta_path.exists():
        return ""
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return meta.get("webpage_url") or meta.get("url") or ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Push a local article to WeChat draft box.")
    parser.add_argument("article", help="Path to article.md or article.html")
    parser.add_argument("--cover", required=True, help="Cover image path")
    parser.add_argument("--column", default="炼金投研", help="Column name included in title")
    parser.add_argument("--env", default=".env", help="Env file path")
    args = parser.parse_args()

    try:
        article_path = Path(args.article)
        cover_path = Path(args.cover)
        env = load_env(Path(args.env))
        required = require_env(env, ["WECHAT_APPID", "WECHAT_APPSECRET", "WECHAT_AUTHOR"])

        token = get_access_token(required["WECHAT_APPID"], required["WECHAT_APPSECRET"])
        title, digest, content = _read_article(article_path, token)
        thumb_media_id = upload_permanent_thumb(token, cover_path)
        article = build_draft_article(
            title=title,
            author=required["WECHAT_AUTHOR"],
            digest=digest,
            content=content,
            thumb_media_id=thumb_media_id,
            column=args.column,
            source_url=_source_url(article_path),
        )
        draft_media_id = add_draft(token, article)
    except (OSError, WechatError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"WeChat draft created: {draft_media_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
