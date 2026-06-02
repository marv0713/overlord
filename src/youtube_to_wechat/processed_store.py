import json
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from youtube_to_wechat.youtube_channel import ChannelVideo


def slugify_source_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "source"


class BaseProcessedStore(ABC):
    @abstractmethod
    def is_processed(self, video_id: str) -> bool: ...

    @abstractmethod
    def processed_video_ids(self) -> set[str]: ...

    @abstractmethod
    def processed_video_ids_for_source(self, source_slug: str) -> set[str]: ...

    @abstractmethod
    def has_source(self, source_slug: str) -> bool: ...

    @abstractmethod
    def allocate_issue(self, series: str) -> str: ...

    @abstractmethod
    def preview_next_issue(self, series: str) -> str: ...

    @abstractmethod
    def get_current_issue(self, series: str) -> str: ...

    @abstractmethod
    def processing_record(self, video_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def source_record(self, source_slug: str) -> dict[str, Any]: ...

    @abstractmethod
    def record_source_scan(
        self, source_name: str, source_slug: str, videos: list[ChannelVideo]
    ) -> None: ...

    @abstractmethod
    def mark_processed(
        self,
        video_id: str,
        source_name: str,
        source_slug: str,
        title: str,
        url: str,
        output_dir: str,
        status: str,
        series: str = "",
        issue: str = "",
        writer_profile: str = "",
        ticker: str = "",
        cover_hook: str = "",
    ) -> None: ...


class JSONDictStore(BaseProcessedStore):
    """Shared implementation for any store that manages state as a single JSON dict."""
    def __init__(self) -> None:
        self._data = self._load()

    @abstractmethod
    def _load(self) -> dict[str, Any]: ...

    @abstractmethod
    def _save(self) -> None: ...

    def is_processed(self, video_id: str) -> bool:
        return video_id in self._data.get("processed_videos", {})

    def processed_video_ids(self) -> set[str]:
        return set(self._data.get("processed_videos", {}).keys())

    def processed_video_ids_for_source(self, source_slug: str) -> set[str]:
        return {
            video_id
            for video_id, record in self._data.get("processed_videos", {}).items()
            if record.get("source_slug") == source_slug
        }

    def has_source(self, source_slug: str) -> bool:
        return source_slug in self._data.get("sources", {})

    def allocate_issue(self, series: str) -> str:
        if not series:
            return ""
        series_record = self._data.setdefault("series", {}).setdefault(
            series, {"next_issue": 1}
        )
        issue_number = int(series_record.get("next_issue", 1))
        series_record["next_issue"] = issue_number + 1
        self._save()
        return f"No.{issue_number:03d}"

    def preview_next_issue(self, series: str) -> str:
        if not series:
            return ""
        series_record = self._data.get("series", {}).get(series, {"next_issue": 1})
        issue_number = int(series_record.get("next_issue", 1))
        return f"No.{issue_number:03d}"

    def get_current_issue(self, series: str) -> str:
        if not series:
            return ""
        series_record = self._data.get("series", {}).get(series, {"next_issue": 1})
        issue_number = max(1, int(series_record.get("next_issue", 1)) - 1)
        return f"No.{issue_number:03d}"

    def processing_record(self, video_id: str) -> dict[str, Any]:
        return self._data.get("processed_videos", {})[video_id]

    def source_record(self, source_slug: str) -> dict[str, Any]:
        return self._data.get("sources", {})[source_slug]

    def record_source_scan(
        self,
        source_name: str,
        source_slug: str,
        videos: list[ChannelVideo],
    ) -> None:
        self._data.setdefault("sources", {})[source_slug] = {
            "name": source_name,
            "scanned_at": _now(),
            "latest_videos": [
                {
                    "video_id": video.video_id,
                    "title": video.title,
                    "url": video.url,
                    "duration_seconds": video.duration_seconds,
                    "published_at": video.published_at,
                }
                for video in videos
            ],
        }
        self._save()

    def mark_processed(
        self,
        video_id: str,
        source_name: str,
        source_slug: str,
        title: str,
        url: str,
        output_dir: str,
        status: str,
        series: str = "",
        issue: str = "",
        writer_profile: str = "",
        ticker: str = "",
        cover_hook: str = "",
    ) -> None:
        record = {
            "source_name": source_name,
            "source_slug": source_slug,
            "title": title,
            "url": url,
            "output_dir": output_dir,
            "status": status,
            "processed_at": _now(),
        }
        if series:
            record["series"] = series
        if issue:
            record["issue"] = issue
        if writer_profile:
            record["writer_profile"] = writer_profile
        if ticker:
            record["ticker"] = ticker
        if cover_hook:
            record["cover_hook"] = cover_hook
        self._data.setdefault("processed_videos", {})[video_id] = record
        self._save()


class LocalProcessedStore(JSONDictStore):
    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2) + "\n")

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"sources": {}, "processed_videos": {}, "series": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))


class SupabaseProcessedStore(JSONDictStore):
    def __init__(self, db_url: str) -> None:
        import psycopg2
        self.db_url = db_url
        self._ensure_table()
        super().__init__()

    def _ensure_table(self):
        import psycopg2
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS overlord_state (
                        key VARCHAR PRIMARY KEY,
                        value JSONB
                    );
                """)
            conn.commit()

    def _save(self) -> None:
        import psycopg2
        import json
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO overlord_state (key, value)
                    VALUES ('state', %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
                """, (json.dumps(self._data, ensure_ascii=False),))
            conn.commit()

    def _load(self) -> dict[str, Any]:
        import psycopg2
        with psycopg2.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM overlord_state WHERE key = 'state';")
                row = cur.fetchone()
                if row and row[0]:
                    return row[0]
        return {"sources": {}, "processed_videos": {}, "series": {}}


def create_store(path: str | Path, db_url: str | None = None) -> BaseProcessedStore:
    if db_url:
        return SupabaseProcessedStore(db_url)
    return LocalProcessedStore(Path(path))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
ProcessedStore = LocalProcessedStore
