#!/usr/bin/env python3
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def generate_cover(
    output: Path,
    column: str,
    ticker: str,
    subtitle: str = "",
    issue: str = "",
    hook: str = "",
) -> None:
    # 900×500 ≈ 1.8:1  ← gives more vertical room for text; WeChat accepts up to ~2.35:1 for thumb
    width, height = 900, 500
    image = Image.new("RGB", (width, height), "#101113")
    draw = ImageDraw.Draw(image)

    # Gradient background
    for y in range(height):
        shade = int(16 + y / height * 20)
        draw.line([(0, y), (width, y)], fill=(shade, shade, shade + 2))

    gold = "#d4af37"
    muted_gold = "#7b6425"

    # Diagonal decorative lines
    for x in range(80, width, 140):
        draw.line([(x, 50), (x + 80, height - 50)], fill=muted_gold, width=1)
    for y in [110, 240, 380]:
        draw.line([(90, y), (810, y - 18)], fill="#242424", width=2)

    # Border frames
    draw.rectangle((36, 36, width - 36, height - 36), outline=gold, width=3)
    draw.rectangle((50, 50, width - 50, height - 50), outline="#4b3d19", width=1)

    # Safe zone — wider than before so text has more room
    safe_left = 100
    safe_right = width - 100
    max_text_width = safe_right - safe_left  # 700px

    # ── Top label row: issue (left) + column (right) ──
    label_font = _font(22)
    label_y = 64
    if issue:
        draw.text((70, label_y), issue, font=label_font, fill="#9c8a52")
    if column:
        col_bbox = draw.textbbox((0, 0), column, font=label_font)
        col_w = col_bbox[2] - col_bbox[0]
        draw.text((width - 70 - col_w, label_y), column, font=label_font, fill="#9c8a52")

    # ── Main content block (vertically centred in remaining space) ──
    element_spacing = 22
    line_spacing = 10
    top_reserved = 110   # space used by label row
    bottom_reserved = 60
    available_h = height - top_reserved - bottom_reserved

    elements: list[dict] = []
    total_h = 0
    for scale in (1.0, 0.92, 0.84, 0.76, 0.68):
        headline_font = _font(max(32, int(54 * scale)))
        hook_font = _font(max(22, int(32 * scale)))
        subtitle_font = _font(max(18, int(24 * scale)))
        elements = []

        # headline = ticker only (column is already shown in the label row)
        if ticker:
            headline_lines = _wrap_text(draw, ticker, headline_font, max_text_width, max_lines=2)
            elements.append({"lines": headline_lines, "font": headline_font, "fill": gold})

        if hook:
            hook_lines = _wrap_text(draw, hook, hook_font, max_text_width, max_lines=2)
            elements.append({"lines": hook_lines, "font": hook_font, "fill": "#f2f0e8"})

        if subtitle:
            subtitle_lines = _wrap_text(draw, subtitle, subtitle_font, max_text_width, max_lines=1)
            elements.append({"lines": subtitle_lines, "font": subtitle_font, "fill": "#c8b46b"})

        total_h = _stack_height(draw, elements, line_spacing, element_spacing)
        if total_h <= available_h:
            break

    # Vertically centre the block in the available zone
    content_top = top_reserved
    current_y = content_top + (available_h - total_h) / 2

    for el in elements:
        font = el["font"]
        lines = el["lines"]
        fill = el["fill"]
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_w = bbox[2] - bbox[0]
            line_h = bbox[3] - bbox[1]
            x = safe_left + (max_text_width - line_w) / 2
            draw.text((x, current_y), line, font=font, fill=fill)
            current_y += line_h + line_spacing
        current_y += element_spacing - line_spacing

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    """Wrap text to fit within max_width, returning a list of lines."""
    wrapped_lines = []
    current_line = ""
    # Simple word wrapping that respects English words and Chinese chars
    words = []
    current_word = ""
    for char in text:
        if char.isascii() and not char.isspace():
            current_word += char
        else:
            if current_word:
                words.append(current_word)
                current_word = ""
            words.append(char)
    if current_word:
        words.append(current_word)
        
    for word in words:
        if _text_width(draw, word, font) > max_width:
            if current_line:
                wrapped_lines.append(current_line)
                current_line = ""
            wrapped_lines.extend(_split_token_to_width(draw, word, font, max_width))
            continue

        test_line = current_line + word
        if _text_width(draw, test_line, font) > max_width and current_line:
            wrapped_lines.append(current_line)
            current_line = word
        else:
            current_line = test_line
            
    if current_line:
        wrapped_lines.append(current_line)
        
    if len(wrapped_lines) > max_lines:
        wrapped_lines = wrapped_lines[:max_lines]
        wrapped_lines[-1] = _fit_with_ellipsis(draw, wrapped_lines[-1], font, max_width)
        
    return wrapped_lines


def _stack_height(
    draw: ImageDraw.ImageDraw,
    elements: list[dict],
    line_spacing: int,
    element_spacing: int,
) -> int:
    total_h = 0
    for el in elements:
        font = el["font"]
        lines = el["lines"]
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            total_h += bbox[3] - bbox[1]
        total_h += line_spacing * (len(lines) - 1)
    total_h += element_spacing * max(0, len(elements) - 1)
    return total_h


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _split_token_to_width(
    draw: ImageDraw.ImageDraw,
    token: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    parts = []
    current = ""
    for char in token:
        test = current + char
        if current and _text_width(draw, test, font) > max_width:
            parts.append(current)
            current = char
        else:
            current = test
    if current:
        parts.append(current)
    return parts


def _fit_with_ellipsis(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    ellipsis = "..."
    while text and _text_width(draw, text + ellipsis, font) > max_width:
        text = text[:-1]
    return text + ellipsis if text else ellipsis


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a recurring WeChat cover image.")
    parser.add_argument("--ticker", required=True, help="Ticker or topic shown as the main text")
    parser.add_argument("--subtitle", default="", help="Small subtitle shown below the ticker")
    parser.add_argument("--issue", default="", help="Series issue label, e.g. No.001")
    parser.add_argument("--hook", default="", help="Short hook line based on article content")
    parser.add_argument("--column", default="炼金投研", help="Column label")
    parser.add_argument("--output", required=True, help="Output PNG path")
    args = parser.parse_args()

    generate_cover(
        Path(args.output),
        column=args.column,
        ticker=args.ticker,
        subtitle=args.subtitle,
        issue=args.issue,
        hook=args.hook,
    )
    print(f"Wrote cover to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
