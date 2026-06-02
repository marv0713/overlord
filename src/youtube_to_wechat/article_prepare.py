import re
from pathlib import Path


def extract_wechat_title(text: str, article_path: Path) -> str:
    suggested = _extract_prefixed_line(text, "> 标题建议：")
    if suggested:
        return compact_wechat_title(suggested)

    match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    h1_title = match.group(1).strip() if match else ""

    subtitle = _first_plain_line_after_h1(text)
    if subtitle and (not h1_title or len(subtitle) < len(h1_title)):
        return compact_wechat_title(subtitle)

    if h1_title:
        return compact_wechat_title(h1_title)

    h1_match = re.search(r"<h1>(.+?)</h1>", text, re.IGNORECASE)
    if h1_match:
        return compact_wechat_title(h1_match.group(1).strip())

    return article_path.parent.name


def extract_digest(text: str) -> str:
    digest = _extract_prefixed_line(text, "> 摘要：")
    if digest:
        return digest
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"[#>*_`\\-]+", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:110] or "AI 提炼的投研内容草稿。"


def strip_leading_title_block(text: str) -> str:
    lines = text.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1

    if index < len(lines) and lines[index].startswith("# "):
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index < len(lines) and _looks_like_lead_title(lines[index]):
            index += 1
            while index < len(lines) and not lines[index].strip():
                index += 1

    return "\n".join(lines[index:]).lstrip()


def compact_wechat_title(title: str, max_chars: int = 24) -> str:
    cleaned = title.strip()
    for prefix in ("炼金投研｜", "炼金投研 |", "炼金投研：", "炼金投研:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.replace("Sven Carlin", "Sven")

    issue = ""
    match = re.match(r"^(No\.\d+)\s*[|｜]\s*(.+)$", cleaned)
    if match:
        issue = match.group(1)
        cleaned = match.group(2).strip()

    for separator in ("：", ":", " - ", "——", "，", ","):
        if separator in cleaned:
            cleaned = cleaned.split(separator, 1)[0].strip()
            break

    if len(cleaned) > max_chars and " " in cleaned:
        parts = []
        for part in cleaned.split():
            test = " ".join([*parts, part])
            if len(test) > max_chars:
                break
            parts.append(part)
        if parts:
            cleaned = " ".join(parts)

    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip()

    return f"{issue} | {cleaned}" if issue else cleaned


def _extract_prefixed_line(text: str, prefix: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    return ""


def _first_plain_line_after_h1(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("# "):
            continue
        for candidate in lines[index + 1:]:
            stripped = candidate.strip()
            if not stripped:
                continue
            if _looks_like_lead_title(stripped):
                return stripped
            return ""
    return ""


def _looks_like_lead_title(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("#", ">", "-", "*", "![", "|")):
        return False
    if stripped.startswith(("No.", "第")):
        return True
    return "｜" in stripped or " | " in stripped or "：" in stripped or ":" in stripped
