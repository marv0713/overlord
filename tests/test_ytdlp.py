import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from youtube_to_wechat.ytdlp import (
    DEFAULT_SUBTITLE_LANGUAGES,
    download_audio,
    fetch_transcript,
    ytdlp_command,
    ytdlp_options,
    YtDlpError,
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

    @patch("youtube_to_wechat.ytdlp.subprocess.run")
    @patch("youtube_to_wechat.ytdlp.ytdlp_command", return_value=["yt-dlp"])
    def test_fetch_transcript_succeeds_when_vtt_present_despite_nonzero_returncode(self, _cmd, run):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            # Simulate yt-dlp downloading one subtitle file but exiting with non-zero due to second sub language failing
            vtt_file = work_dir / "test.vtt"
            vtt_file.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello world\n")
            run.return_value = Mock(returncode=1, stderr="ERROR: Unable to download video subtitles for 'en'")

            transcript = fetch_transcript("https://example.com/watch?v=123", work_dir)
            self.assertIn("Hello world", transcript)

    @patch("youtube_to_wechat.ytdlp.subprocess.run")
    @patch("youtube_to_wechat.ytdlp.ytdlp_command", return_value=["yt-dlp"])
    def test_fetch_transcript_raises_when_no_vtt_and_nonzero_returncode(self, _cmd, run):
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir)
            run.return_value = Mock(returncode=1, stderr="ERROR: 429 Too Many Requests")

            with self.assertRaises(YtDlpError) as ctx:
                fetch_transcript("https://example.com/watch?v=123", work_dir)
            self.assertIn("429 Too Many Requests", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
