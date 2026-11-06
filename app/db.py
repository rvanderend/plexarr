"""SQLite: settings, the index of the friends' libraries, downloads and a small activity log."""
import json
import sqlite3
import threading
import time

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- One row per file on a friend's Plex server (a movie or an episode can have several versions).
CREATE TABLE IF NOT EXISTS releases (
    part_id INTEGER NOT NULL,
    server TEXT NOT NULL,          -- clientIdentifier of the Plex server
    kind TEXT NOT NULL,            -- movie | episode
    rating_key TEXT NOT NULL,      -- the movie or episode
    show_key TEXT,                 -- episode: the show
    title TEXT NOT NULL,           -- movie title or show title
    original_title TEXT,
    year INTEGER,
    season INTEGER, episode INTEGER, episode_title TEXT,
    imdb TEXT, tmdb INTEGER, tvdb INTEGER,  -- episode: ids of the show
    added_at INTEGER,
    part_key TEXT NOT NULL, file TEXT, size INTEGER,
    container TEXT, resolution TEXT, vcodec TEXT, acodec TEXT, channels INTEGER, bitrate INTEGER,
    thumb TEXT,
    PRIMARY KEY (server, part_id)
);
CREATE INDEX IF NOT EXISTS releases_tmdb ON releases(tmdb);
CREATE INDEX IF NOT EXISTS releases_imdb ON releases(imdb);
CREATE INDEX IF NOT EXISTS releases_tvdb ON releases(tvdb, season, episode);
CREATE INDEX IF NOT EXISTS releases_added ON releases(added_at);
CREATE TABLE IF NOT EXISTS jobs (
    nzo_id TEXT PRIMARY KEY,
    server TEXT NOT NULL, part_id INTEGER NOT NULL,
    name TEXT NOT NULL,            -- release title: the job and folder name Sonarr/Radarr see
    category TEXT NOT NULL,
    kind TEXT NOT NULL, label TEXT NOT NULL, sublabel TEXT DEFAULT '',
    rating_key TEXT, show_key TEXT,
    size INTEGER NOT NULL, done INTEGER DEFAULT 0,
    status TEXT NOT NULL,          -- Queued | Downloading | Paused | Completed | Failed | Deleted
    fail_message TEXT DEFAULT '',
    storage TEXT DEFAULT '',
    attempts INTEGER DEFAULT 0,
    created REAL NOT NULL, started REAL, finished REAL,
    removed INTEGER DEFAULT 0,     -- removed from the history by Sonarr/Radarr (kept for the log)
    subs TEXT                      -- JSON: the subtitles fetched along, [{"lang": "nl", "forced": false}]
);
-- Subtitles Bazarr fetched through PlexArr (the Subsarr API).
CREATE TABLE IF NOT EXISTS subs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    server TEXT NOT NULL, part_id INTEGER, stream_id INTEGER NOT NULL,
    kind TEXT, label TEXT NOT NULL, sublabel TEXT DEFAULT '',
    rating_key TEXT, show_key TEXT,
    lang TEXT, hi INTEGER DEFAULT 0, ext TEXT, size INTEGER
);
CREATE TABLE IF NOT EXISTS requests (
    key TEXT PRIMARY KEY,          -- movie:<tmdb> | episode:<tvdb>:<season>:<episode>
    until REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sweeps (key TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
    data TEXT                      -- JSON the web UI builds a translated line from (text is the fallback)
);
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DATA_DIR / "plexarr.db", check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        if "data" not in {r["name"] for r in _conn.execute("PRAGMA table_info(events)")}:
            _conn.execute("ALTER TABLE events ADD COLUMN data TEXT")
        if "subs" not in {r["name"] for r in _conn.execute("PRAGMA table_info(jobs)")}:
            _conn.execute("ALTER TABLE jobs ADD COLUMN subs TEXT")
        if "rating_key" not in {r["name"] for r in _conn.execute("PRAGMA table_info(subs)")}:
            _conn.execute("ALTER TABLE subs ADD COLUMN rating_key TEXT")
            _conn.execute("ALTER TABLE subs ADD COLUMN show_key TEXT")
    return _conn


def requeue_interrupted():
    """At startup: downloads that were running when the service stopped continue where they were. Only the
    service itself calls this; another process opening the database must not touch a running download."""
    x("UPDATE jobs SET status='Queued' WHERE status='Downloading'")


def q(sql: str, args=()) -> list[sqlite3.Row]:
    with _lock:
        return conn().execute(sql, args).fetchall()


def q1(sql: str, args=()) -> sqlite3.Row | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql: str, args=()):
    with _lock:
        return conn().execute(sql, args)


def many(sql: str, rows):
    with _lock:
        c = conn()
        c.execute("BEGIN")
        try:
            c.executemany(sql, rows)
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise


# --- settings and state ----------------------------------------------------------------------
def get_settings() -> dict:
    s = dict(config.DEFAULT_SETTINGS)
    for r in q("SELECT key, value FROM settings"):
        if r["key"] in s:
            s[r["key"]] = json.loads(r["value"])
    return s


def set_settings(values: dict):
    with _lock:
        for k, v in values.items():
            if k in config.DEFAULT_SETTINGS:
                x("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (k, json.dumps(v)))


def get_state(key: str, default=None):
    r = q1("SELECT value FROM state WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default


def set_state(key: str, value):
    x("INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, json.dumps(value)))


def event(kind: str, text: str, **data):
    """Activity log: searches answered, downloads, sweeps. Keeps the last 2000 lines."""
    x("INSERT INTO events(at, kind, text, data) VALUES(?, ?, ?, ?)",
      (time.time(), kind, text, json.dumps(data) if data else None))
    x("DELETE FROM events WHERE id <= (SELECT MAX(id) - 2000 FROM events)")
