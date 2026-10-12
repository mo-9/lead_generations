"""Saved progress. Every search, place and website result is written to a SQLite file
immediately, so an interrupted run picks up where it stopped."""
from __future__ import annotations

import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT PRIMARY KEY,      -- keyword | area | box
    keyword     TEXT,
    area        TEXT,
    box         TEXT,
    depth       INTEGER,
    status      TEXT,                  -- done | split | failed
    results     INTEGER,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS places (
    place_key        TEXT PRIMARY KEY,
    place_id         TEXT,
    ftid             TEXT,
    maps_url         TEXT,
    name             TEXT,
    latitude         REAL,
    longitude        REAL,
    card_category    TEXT,
    card_address     TEXT,
    card_phone       TEXT,
    card_website     TEXT,
    card_rating      REAL,
    card_reviews     INTEGER,
    details_status   TEXT,             -- NULL | ok | failed
    details_attempts INTEGER DEFAULT 0,
    google_category  TEXT,
    address          TEXT,
    phone            TEXT,
    website          TEXT,
    rating           REAL,
    review_count     INTEGER,
    closed           TEXT,
    enrich_status    TEXT,             -- NULL | ok | nothing | robots | skipped | unreachable | needs_js
    enrich_attempts  INTEGER DEFAULT 0,
    email            TEXT,
    emails_all       TEXT,
    decision_maker_name  TEXT,
    decision_maker_title TEXT,
    linkedin         TEXT,
    instagram        TEXT,
    facebook         TEXT,
    first_seen       TEXT,
    details_at       TEXT,
    enriched_at      TEXT
);
CREATE TABLE IF NOT EXISTS hits (           -- which search (keyword + area) found which place
    place_key   TEXT,
    keyword     TEXT,
    persona     TEXT,
    area        TEXT,
    first_seen  TEXT,
    PRIMARY KEY (place_key, keyword, area)
);
"""

CARD_FIELDS = ("place_id", "ftid", "maps_url", "name", "latitude", "longitude", "card_category",
               "card_address", "card_phone", "card_website", "card_rating", "card_reviews")
DETAIL_FIELDS = ("google_category", "address", "phone", "website", "rating", "review_count", "closed")
ENRICH_FIELDS = ("email", "emails_all", "decision_maker_name", "decision_maker_title",
                 "linkedin", "instagram", "facebook")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path), isolation_level=None)   # autocommit: every write is saved
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # ── searches ──
    def task_status(self, task_id: str) -> str | None:
        row = self.db.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return row["status"] if row else None

    def set_task(self, task_id, keyword, area, box, status, results):
        self.db.execute(
            "INSERT OR REPLACE INTO tasks (task_id, keyword, area, box, depth, status, results, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (task_id, keyword, area, box.key(), box.depth, status, results, now()))

    def count_tasks(self, status: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM tasks WHERE status=?", (status,)).fetchone()[0]

    # ── places ──
    def get(self, place_key: str):
        return self.db.execute("SELECT * FROM places WHERE place_key=?", (place_key,)).fetchone()

    def save_card(self, card: dict) -> bool:
        """Insert a place seen in a results list. Returns True if it is new."""
        if self.get(card["place_key"]):
            sets = ", ".join(f"{f}=COALESCE(?, {f})" for f in CARD_FIELDS)
            values = [card.get(f) if card.get(f) not in ("", None) else None for f in CARD_FIELDS]
            self.db.execute(f"UPDATE places SET {sets} WHERE place_key=?", (*values, card["place_key"]))
            return False
        cols = ("place_key",) + CARD_FIELDS + ("first_seen",)
        values = [card["place_key"]] + [card.get(f) for f in CARD_FIELDS] + [now()]
        self.db.execute(f"INSERT INTO places ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)
        return True

    def add_hit(self, place_key, keyword, persona, area):
        self.db.execute("INSERT OR IGNORE INTO hits VALUES (?,?,?,?,?)",
                        (place_key, keyword, persona, area, now()))

    def save_details(self, place_key: str, details: dict | None):
        if details is None:
            self.db.execute("UPDATE places SET details_status='failed', details_attempts=details_attempts+1 "
                            "WHERE place_key=?", (place_key,))
            return
        sets = ", ".join(f"{f}=?" for f in DETAIL_FIELDS)
        self.db.execute(
            f"UPDATE places SET {sets}, details_status='ok', details_attempts=details_attempts+1, details_at=?, "
            "name=COALESCE(NULLIF(name, ''), ?), place_id=COALESCE(NULLIF(place_id, ''), ?), "
            "latitude=COALESCE(latitude, ?), longitude=COALESCE(longitude, ?) WHERE place_key=?",
            (*[details.get(f) for f in DETAIL_FIELDS], now(), details.get("name"),
             details.get("place_id") or None, details.get("latitude"), details.get("longitude"), place_key))

    def save_enrichment(self, place_key: str, status: str, data: dict):
        sets = ", ".join(f"{f}=?" for f in ENRICH_FIELDS)
        self.db.execute(
            f"UPDATE places SET {sets}, enrich_status=?, enrich_attempts=enrich_attempts+1, enriched_at=? "
            "WHERE place_key=?",
            (*[data.get(f) or "" for f in ENRICH_FIELDS], status, now(), place_key))

    def places(self) -> list:
        return self.db.execute("SELECT * FROM places").fetchall()

    def hits(self) -> list:
        return self.db.execute("SELECT * FROM hits ORDER BY first_seen, rowid").fetchall()
