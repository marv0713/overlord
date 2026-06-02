import unittest

from PIL import Image, ImageDraw

from scripts.generate_cover import _font, _wrap_text


class GenerateCoverTests(unittest.TestCase):
    def test_wrap_text_splits_long_ascii_words_to_fit_width(self):
        image = Image.new("RGB", (320, 160), "white")
        draw = ImageDraw.Draw(image)
        font = _font(32)
        max_width = 140

        lines = _wrap_text(
            draw,
            "SuperLongUnbrokenTickerName",
            font,
            max_width=max_width,
            max_lines=4,
        )

        self.assertGreater(len(lines), 1)
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            self.assertLessEqual(bbox[2] - bbox[0], max_width)


if __name__ == "__main__":
    unittest.main()
