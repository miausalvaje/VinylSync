"""SQLite-backed lyrics cache."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from vinyl_lyrics.lyrics.lrclib import LyricResult


class LyricsCache:
    """Persist lyrics and cover URLs keyed by artist + title + duration."""

    def __init__(self, db_path: str | Path = "data/lyrics.sqlite3") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS lyrics (
                    cache_key TEXT PRIMARY KEY,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    duration REAL,
                    lyric_text TEXT NOT NULL,
                    synced INTEGER NOT NULL,
                    cover_url TEXT
                )
                """
            )

    @staticmethod
    def build_cache_key(artist: str, title: str, duration: float | None) -> str:
        normalized_artist = (artist or "").strip().lower()
        normalized_title = (title or "").strip().lower()
        normalized_duration = int(round(float(duration))) if duration is not None else 0
        return f"{normalized_artist}|{normalized_title}|{normalized_duration}"

    @staticmethod
    def cache_keys_for(artist: str, title: str, duration: float | None) -> list[str]:
        keys = [LyricsCache.build_cache_key(artist, title, duration)]
        if duration is not None:
            keys.append(LyricsCache.build_cache_key(artist, title, None))
        return keys

    def get(self, artist: str, title: str, duration: float | None) -> LyricResult | None:
        for key in self.cache_keys_for(artist, title, duration):
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT artist, title, duration, lyric_text, synced, cover_url
                    FROM lyrics
                    WHERE cache_key = ?
                    """,
                    (key,),
                ).fetchone()
                if row is not None:
                    return LyricResult(
                        text=row["lyric_text"],
                        synced=bool(row["synced"]),
                        title=row["title"],
                        artist=row["artist"],
                        duration=float(row["duration"]) if row["duration"] is not None else None,
                        cover_url=row["cover_url"],
                    )
        return None

    def save(self, result: LyricResult) -> None:
        keys = self.cache_keys_for(result.artist, result.title, result.duration)
        with self._connect() as connection:
            for key in dict.fromkeys(keys):
                connection.execute(
                    """
                    INSERT OR REPLACE INTO lyrics (
                        cache_key, artist, title, duration, lyric_text, synced, cover_url
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        result.artist,
                        result.title,
                        result.duration,
                        result.text,
                        int(bool(result.synced)),
                        result.cover_url,
                    ),
                )

    def save_payload(
        self,
        artist: str,
        title: str,
        duration: float | None,
        lyric_text: str,
        synced: bool,
        cover_url: str | None = None,
    ) -> None:
        self.save(
            LyricResult(
                text=lyric_text,
                synced=synced,
                title=title,
                artist=artist,
                duration=duration,
                cover_url=cover_url,
            )
        )

    def get_payload(
        self, artist: str, title: str, duration: float | None
    ) -> dict[str, object] | None:
        result = self.get(artist, title, duration)
        if result is None:
            return None
        return {
            "text": result.text,
            "synced": result.synced,
            "title": result.title,
            "artist": result.artist,
            "duration": result.duration,
            "cover_url": result.cover_url,
        }


__all__ = ["LyricsCache"]
