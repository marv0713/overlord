from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from youtube_to_wechat.ytdlp import (
    DEFAULT_SUBTITLE_LANGUAGES,
    download_audio,
    ytdlp_command,
    ytdlp_options,
)


class YtDlpCommandTests(unittest.TestCase):
    @patch("youtube_to_wechat.ytdlp.shutil.which", return_value="/usr/local/bin/yt-dlp")
    def test_uses_executable_when_available(self, _which):
        self.assertEqual(ytdlp_command(), ["/usr/local/bin/yt-dlp"])

    @patch("youtube_to_wechat.ytdlp.shutil.which", return_value=None)
    def test_falls_back_to_python_module(self, _which):
        command = ytdlp_command()
        self.assertEqual(command[-2:], ["-m", "yt_dlp"])

    def test_adds_no_check_certificates_when_requested(self):
        self.assertEqual(
            ytdlp_options(no_check_certificates=True),
            [
                "--js-runtimes",
                "node",
                "--remote-components",
                "ejs:github",
                "--no-check-certificates",
            ],
        )

    def test_omits_no_check_certificates_by_default(self):
        self.assertEqual(
            ytdlp_options(no_check_certificates=False),
            ["--js-runtimes", "node", "--remote-components", "ejs:github"],
        )

    def test_default_subtitle_languages_prioritize_english_original(self):
        self.assertEqual(DEFAULT_SUBTITLE_LANGUAGES[:2], ["en-orig", "en"])

    @patch("youtube_to_wechat.ytdlp.subprocess.run")
    @patch("youtube_to_wechat.ytdlp.ytdlp_command", return_value=["yt-dlp"])
    def test_download_audio_requests_audio_file(self, _command, run):
        run.return_value = Mock(returncode=0, stderr="")

        download_audio(
            url="https://www.youtube.com/watch?v=6C7FjGs22g8",
            work_dir=Path("outputs/youtube/6C7FjGs22g8/audio"),
            no_check_certificates=True,
        )

        command = run.call_args.args[0]
        self.assertIn("-f", command)
        self.assertIn("bestaudio/best", command)
        self.assertIn("--no-check-certificates", command)


if __name__ == "__main__":
    unittest.main()
