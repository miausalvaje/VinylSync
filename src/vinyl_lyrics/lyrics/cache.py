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
    def _normalize_text(text: str) -> str:
        if not text:
            return ""
        # Standardize curved quotes, curly apostrophes, dashes, and whitespace
        t = text.replace("’", "'").replace("‘", "'").replace("`", "'").replace("´", "'")
        t = t.replace("“", '"').replace("”", '"').replace("«", '"').replace("»", '"')
        t = t.replace("–", "-").replace("—", "-")
        import re

        return re.sub(r"\s+", " ", t).strip().lower()

    @classmethod
    def build_cache_key(cls, artist: str, title: str, duration: float | None) -> str:
        normalized_artist = cls._normalize_text(artist)
        normalized_title = cls._normalize_text(title)
        normalized_duration = int(round(float(duration))) if duration is not None else 0
        return f"{normalized_artist}|{normalized_title}|{normalized_duration}"

    @classmethod
    def cache_keys_for(cls, artist: str, title: str, duration: float | None) -> list[str]:
        import re

        keys = [cls.build_cache_key(artist, title, duration)]
        if duration is not None:
            keys.append(cls.build_cache_key(artist, title, None))

        clean_title = re.sub(r"\s*[\(\[].*?[\)\]]", "", title).strip()
        if clean_title and clean_title.casefold() != title.casefold():
            keys.append(cls.build_cache_key(artist, clean_title, duration))
            if duration is not None:
                keys.append(cls.build_cache_key(artist, clean_title, None))

        primary_artist = re.split(
            r"\s*(?:&|feat\.?|ft\.?|featuring|,)\s*", artist, flags=re.IGNORECASE
        )[0].strip()
        if primary_artist and primary_artist.casefold() != artist.casefold():
            keys.append(cls.build_cache_key(primary_artist, title, duration))
            if duration is not None:
                keys.append(cls.build_cache_key(primary_artist, title, None))
            if clean_title and clean_title.casefold() != title.casefold():
                keys.append(cls.build_cache_key(primary_artist, clean_title, duration))
                if duration is not None:
                    keys.append(cls.build_cache_key(primary_artist, clean_title, None))

        return list(dict.fromkeys(keys))

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

    def save(
        self,
        result: LyricResult,
        alias_artist: str | None = None,
        alias_title: str | None = None,
    ) -> None:
        keys = self.cache_keys_for(result.artist, result.title, result.duration)
        if alias_artist or alias_title:
            keys.extend(
                self.cache_keys_for(
                    alias_artist or result.artist,
                    alias_title or result.title,
                    result.duration,
                )
            )
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
