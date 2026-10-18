from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    channel_id INTEGER NOT NULL,
    day        TEXT    NOT NULL,
    slot       INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    user_name  TEXT    NOT NULL,
    claimed_at TEXT    NOT NULL,
    PRIMARY KEY (channel_id, day, slot)
);

CREATE TABLE IF NOT EXISTS notices (
    channel_id INTEGER NOT NULL,
    day        TEXT    NOT NULL,
    slot       INTEGER NOT NULL,
    sent_at    TEXT    NOT NULL,
    PRIMARY KEY (channel_id, day, slot)
);

CREATE TABLE IF NOT EXISTS summaries (
    channel_id INTEGER NOT NULL,
    day        TEXT    NOT NULL,
    sent_at    TEXT    NOT NULL,
    PRIMARY KEY (channel_id, day)
);

CREATE TABLE IF NOT EXISTS daily_posts (
    channel_id INTEGER NOT NULL,
    day        TEXT    NOT NULL,
    message_id INTEGER NOT NULL,
    sent_at    TEXT    NOT NULL,
    PRIMARY KEY (channel_id, day)
);

CREATE TABLE IF NOT EXISTS panels (
    channel_id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL,
    day        TEXT    NOT NULL,
    page       INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass(frozen=True)
class Claim:
    user_id: int
    user_name: str
    claimed_at: str


@dataclass(frozen=True)
class Panel:
    channel_id: int
    message_id: int
    day: str
    page: int


class Store:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 2:
                self._conn.executescript(
                    "DROP TABLE IF EXISTS claims;"
                    "DROP TABLE IF EXISTS notices;"
                    "DROP TABLE IF EXISTS panels;"
                    "DROP TABLE IF EXISTS summaries;"
                )
            self._conn.executescript(SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def claims(self, channel_id: int, day: date) -> dict[int, Claim]:
        rows = self._conn.execute(
            "SELECT slot, user_id, user_name, claimed_at FROM claims"
            " WHERE channel_id = ? AND day = ? ORDER BY slot",
            (channel_id, day.isoformat()),
        ).fetchall()
        return {
            row["slot"]: Claim(row["user_id"], row["user_name"], row["claimed_at"])
            for row in rows
        }

    def add_claim(
        self, channel_id: int, day: date, slot: int, user_id: int, user_name: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO claims (channel_id, day, slot, user_id, user_name, claimed_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(channel_id, day, slot) DO UPDATE SET"
                " user_id = excluded.user_id, user_name = excluded.user_name,"
                " claimed_at = excluded.claimed_at",
                (
                    channel_id,
                    day.isoformat(),
                    slot,
                    user_id,
                    user_name,
                    datetime.now().strftime("%d.%m %H:%M"),
                ),
            )
            self._conn.commit()

    def remove_claim(self, channel_id: int, day: date, slot: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM claims WHERE channel_id = ? AND day = ? AND slot = ?",
                (channel_id, day.isoformat(), slot),
            )
            self._conn.commit()

    def clear_day(self, channel_id: int, day: date) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM claims WHERE channel_id = ? AND day = ?",
                (channel_id, day.isoformat()),
            )
            self._conn.execute(
                "DELETE FROM notices WHERE channel_id = ? AND day = ?",
                (channel_id, day.isoformat()),
            )
            self._conn.execute(
                "DELETE FROM summaries WHERE channel_id = ? AND day = ?",
                (channel_id, day.isoformat()),
            )
            self._conn.commit()
            return cursor.rowcount

    def notice_sent(self, channel_id: int, day: date, slot: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM notices WHERE channel_id = ? AND day = ? AND slot = ?",
            (channel_id, day.isoformat(), slot),
        ).fetchone()
        return row is not None

    def mark_notice(self, channel_id: int, day: date, slot: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO notices (channel_id, day, slot, sent_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    channel_id,
                    day.isoformat(),
                    slot,
                    datetime.now().strftime("%d.%m %H:%M"),
                ),
            )
            self._conn.commit()

    def summary_sent(self, channel_id: int, day: date) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM summaries WHERE channel_id = ? AND day = ?",
            (channel_id, day.isoformat()),
        ).fetchone()
        return row is not None

    def mark_summary(self, channel_id: int, day: date) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO summaries (channel_id, day, sent_at) VALUES (?, ?, ?)",
                (channel_id, day.isoformat(), datetime.now().strftime("%d.%m %H:%M")),
            )
            self._conn.commit()

    def post_sent(self, channel_id: int, day: date) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM daily_posts WHERE channel_id = ? AND day = ?",
            (channel_id, day.isoformat()),
        ).fetchone()
        return row is not None

    def mark_post(self, channel_id: int, day: date, message_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO daily_posts (channel_id, day, message_id, sent_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    channel_id,
                    day.isoformat(),
                    message_id,
                    datetime.now().strftime("%d.%m %H:%M"),
                ),
            )
            self._conn.commit()

    def purge_before(self, day: date) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM claims WHERE day < ?", (day.isoformat(),))
            self._conn.execute("DELETE FROM notices WHERE day < ?", (day.isoformat(),))
            self._conn.execute("DELETE FROM daily_posts WHERE day < ?", (day.isoformat(),))
            self._conn.commit()

    def panel(self, channel_id: int) -> Panel | None:
        row = self._conn.execute(
            "SELECT channel_id, message_id, day, page FROM panels WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            return None
        return Panel(row["channel_id"], row["message_id"], row["day"], row["page"])

    def all_panels(self) -> list[Panel]:
        rows = self._conn.execute(
            "SELECT channel_id, message_id, day, page FROM panels"
        ).fetchall()
        return [
            Panel(row["channel_id"], row["message_id"], row["day"], row["page"])
            for row in rows
        ]

    def save_panel(self, channel_id: int, message_id: int, day: date, page: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO panels (channel_id, message_id, day, page) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(channel_id) DO UPDATE SET"
                " message_id = excluded.message_id, day = excluded.day, page = excluded.page",
                (channel_id, message_id, day.isoformat(), page),
            )
            self._conn.commit()

    def delete_panel(self, channel_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM panels WHERE channel_id = ?", (channel_id,))
            self._conn.commit()
