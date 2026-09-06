import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from youtube_to_wechat.cleaner import clean_vtt


DEFAULT_SUBTITLE_LANGUAGES = ["en-orig", "en"]
YOUTUBE_SOURCE_TYPE = "youtube"


class YtDlpError(RuntimeError):
    pass


def ytdlp_command() -> List[str]:
    executable = shutil.which("yt-dlp")
    if executable is not None:
        return [executable]
    return [sys.executable, "-m", "yt_dlp"]


def ytdlp_options(no_check_certificates: bool = False) -> List[str]:
    opts = [
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
    ]
    if no_check_certificates:
        opts.append("--no-check-certificates")
    return opts


def fetch_info(url: str, no_check_certificates: bool = False) -> dict:
    result = subprocess.run(
        [
            *ytdlp_command(),
            *ytdlp_options(no_check_certificates),
            "--dump-single-json",
            "--skip-download",
            url,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise YtDlpError(result.stderr.strip() or "yt-dlp failed while fetching metadata")
    return json.loads(result.stdout)


def fetch_transcript(
    url: str,
    work_dir: Path,
    languages: Optional[List[str]] = None,
    no_check_certificates: bool = False,
) -> str:
    languages = languages or DEFAULT_SUBTITLE_LANGUAGES
    work_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            *ytdlp_command(),
            *ytdlp_options(no_check_certificates),
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            ",".join(languages),
            "--sub-format",
            "vtt",
            "-o",
            str(work_dir / "%(id)s.%(ext)s"),
            url,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    vtt_files = sorted(work_dir.glob("*.vtt"))
    if vtt_files:
        return clean_vtt(vtt_files[0].read_text(errors="ignore"))

    if result.returncode != 0:
        raise YtDlpError(result.stderr.strip() or "yt-dlp failed while fetching subtitles")

    raise YtDlpError("No subtitles were available for this video.")


def download_audio(url: str, work_dir: Path, no_check_certificates: bool = False) -> Optional[Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            *ytdlp_command(),
            *ytdlp_options(no_check_certificates),
            "-f",
            "bestaudio/best",
            "-o",
            str(work_dir / "%(id)s.%(ext)s"),
            url,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    audio_files = sorted(work_dir.glob("*.*"))
    if not audio_files:
        if result.returncode != 0:
            raise YtDlpError(result.stderr.strip() or "yt-dlp failed while downloading audio")
        return None
    return audio_files[0]
