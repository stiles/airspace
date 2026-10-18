"""SQLite persistence for sessions, polls and observations."""

from __future__ import annotations

import gzip
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from airspace.models import ErrorKind, Observation, PollResult, from_iso, to_iso, utcnow

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    end_reason TEXT,
    schema_version INTEGER NOT NULL,
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    -- 'poll' for a scheduled request, 'sleep_gap' for a detected host sleep
    kind TEXT NOT NULL DEFAULT 'poll',
    requested_at TEXT NOT NULL,
    received_at TEXT,
    provider_at TEXT,
    status_code INTEGER,
    latency_ms REAL,
    accepted INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    error_kind TEXT,
    error TEXT,
    retry_after_s REAL,
    gap_s REAL,
    response_bytes INTEGER,
    -- 1 when made in fast mode (a helicopter or public-safety aircraft nearby)
    fast INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS polls_session_time ON polls(session_id, requested_at);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    provider TEXT NOT NULL,
    aircraft_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    provider_at TEXT,
    position_at TEXT,
    position_time_basis TEXT NOT NULL,
    time_flag TEXT,
    -- 1 when position_at advanced past this aircraft's previous position in the session
    new_position INTEGER NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    altitude_baro_ft INTEGER,
    altitude_geom_ft INTEGER,
    on_ground INTEGER,
    ground_speed_kt REAL,
    track_deg REAL,
    vertical_rate_fpm INTEGER,
    callsign TEXT,
    registration TEXT,
    aircraft_type TEXT,
    aircraft_description TEXT,
    category TEXT,
    position_source TEXT,
    owner_operator TEXT,
    squawk TEXT,
    emergency TEXT
);
CREATE INDEX IF NOT EXISTS observations_aircraft_time ON observations(session_id, aircraft_id, position_at);
CREATE INDEX IF NOT EXISTS observations_poll ON observations(poll_id);

-- Individually invalid aircraft records, kept for diagnosis
CREATE TABLE IF NOT EXISTS rejections (
    id INTEGER PRIMARY KEY,
    poll_id INTEGER NOT NULL REFERENCES polls(id),
    reason TEXT NOT NULL,
    raw_json TEXT
);

-- Hourly readings of the collector process, for spotting slow memory growth in long runs
CREATE TABLE IF NOT EXISTS process_stats (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    at TEXT NOT NULL,
    footprint_bytes INTEGER,
    rss_bytes INTEGER
);
"""

OBSERVATION_COLUMNS = [
    "provider", "aircraft_id", "received_at", "provider_at", "position_at", "position_time_basis",
    "time_flag", "latitude", "longitude", "altitude_baro_ft", "altitude_geom_ft", "on_ground",
    "ground_speed_kt", "track_deg", "vertical_rate_fpm", "callsign", "registration", "aircraft_type",
    "aircraft_description", "category", "position_source", "owner_operator", "squawk", "emergency",
]  # fmt: skip


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        # Replay readers must never write. mode=ro can't open a WAL database after the
        # writer has exited (SQLite needs to recreate the -shm file), so use query_only instead
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was created."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(polls)")}
    if "fast" not in columns:
        with conn:
            conn.execute("ALTER TABLE polls ADD COLUMN fast INTEGER NOT NULL DEFAULT 0")


def observation_from_row(row: sqlite3.Row) -> Observation:
    values = {column: row[column] for column in OBSERVATION_COLUMNS}
    for column in ("received_at", "provider_at", "position_at"):
        values[column] = from_iso(values[column])
    if values["on_ground"] is not None:
        values["on_ground"] = bool(values["on_ground"])
    return Observation.model_validate(values)


def _observation_values(obs: Observation) -> list:
    values = []
    for column in OBSERVATION_COLUMNS:
        value = getattr(obs, column)
        if isinstance(value, datetime):
            value = to_iso(value)
        elif isinstance(value, bool):
            value = int(value)
        values.append(value)
    return values


# Polls deleted per transaction while pruning, so the writer never holds a long lock
PRUNE_BATCH_POLLS = 2000


@dataclass
class PruneResult:
    polls: int = 0
    observations: int = 0
    sessions: list[int] | None = None


class Store:
    def __init__(self, data_dir: Path, *, store_raw: bool = False, read_only: bool = False):
        self.data_dir = data_dir
        self.db_path = data_dir / "airspace.sqlite"
        self.raw_dir = data_dir / "raw"
        self.store_raw = store_raw and not read_only
        if read_only and not self.db_path.exists():
            raise FileNotFoundError(f"{self.db_path} not found; nothing recorded yet")
        self.conn = connect(self.db_path, read_only=read_only)
        # Latest position time per aircraft in the active session, for the new_position flag
        self._last_position: dict[str, str] = {}

    def close(self) -> None:
        self.conn.close()

    def start_session(self, provider: str, mode: str, config: dict) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO sessions (provider, mode, started_at, schema_version, config_json) VALUES (?, ?, ?, ?, ?)",
                (provider, mode, to_iso(utcnow()), SCHEMA_VERSION, json.dumps(config, default=str)),
            )
        self._last_position.clear()
        return cur.lastrowid

    def end_session(self, session_id: int, reason: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ?",
                (to_iso(utcnow()), reason, session_id),
            )

    def record_poll(self, session_id: int, result: PollResult, *, fast: bool = False) -> int:
        """Write the poll and its observations in one transaction."""
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO polls (session_id, kind, requested_at, received_at, provider_at, status_code,
                       latency_ms, accepted, rejected, error_kind, error, retry_after_s, response_bytes, fast)
                   VALUES (?, 'poll', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    to_iso(result.requested_at),
                    to_iso(result.received_at),
                    to_iso(result.provider_at),
                    result.status_code,
                    result.latency_ms,
                    len(result.observations),
                    len(result.rejections),
                    result.error_kind.value if result.error_kind else None,
                    result.error,
                    result.retry_after_s,
                    len(result.raw_body) if result.raw_body is not None else None,
                    int(fast),
                ),
            )
            poll_id = cur.lastrowid

            rows = []
            for obs in result.observations:
                position_at = to_iso(obs.position_at)
                previous = self._last_position.get(obs.aircraft_id)
                is_new = position_at is not None and (previous is None or position_at > previous)
                if is_new:
                    self._last_position[obs.aircraft_id] = position_at
                rows.append([poll_id, session_id, int(is_new), *_observation_values(obs)])
            if rows:
                columns = ["poll_id", "session_id", "new_position", *OBSERVATION_COLUMNS]
                self.conn.executemany(
                    f"INSERT INTO observations ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                    rows,
                )
            if result.rejections:
                self.conn.executemany(
                    "INSERT INTO rejections (poll_id, reason, raw_json) VALUES (?, ?, ?)",
                    [(poll_id, r.reason, json.dumps(r.raw, default=str)) for r in result.rejections],
                )

        if self.store_raw and result.raw_body is not None:
            self._append_raw(session_id, poll_id, result)
        return poll_id

    def record_gap(self, session_id: int, kind: str, gap_s: float) -> int:
        now = to_iso(utcnow())
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO polls (session_id, kind, requested_at, gap_s) VALUES (?, ?, ?, ?)",
                (session_id, kind, now, gap_s),
            )
        return cur.lastrowid

    def record_process_stats(self, session_id: int, footprint_bytes: int | None, rss_bytes: int | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO process_stats (session_id, at, footprint_bytes, rss_bytes) VALUES (?, ?, ?, ?)",
                (session_id, to_iso(utcnow()), footprint_bytes, rss_bytes),
            )

    def process_stats(self, session_id: int) -> list[sqlite3.Row]:
        try:
            return self.conn.execute(
                "SELECT at, footprint_bytes, rss_bytes FROM process_stats WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            # A database last written by a version without the table, opened read-only
            return []

    def prune(self, before: datetime, *, keep_session: int | None = None) -> PruneResult:
        """Delete polls, observations and rejections recorded before `before`, then sessions that leaves empty.

        Poll IDs increase with time, so deletion walks ID ranges in short transactions on
        the poll index. Freed pages are reused; the file doesn't shrink without VACUUM.
        """
        cutoff = to_iso(before)
        result = PruneResult(sessions=[])
        bounds = self.conn.execute("SELECT MIN(id), MAX(id) FROM polls WHERE requested_at < ?", (cutoff,)).fetchone()
        lo, hi = bounds[0], bounds[1]
        touched: list[int] = []
        if lo is not None:
            touched = [
                row[0]
                for row in self.conn.execute("SELECT DISTINCT session_id FROM polls WHERE id BETWEEN ? AND ?", (lo, hi))
            ]
            for start in range(lo, hi + 1, PRUNE_BATCH_POLLS):
                end = min(start + PRUNE_BATCH_POLLS - 1, hi)
                with self.conn:
                    self.conn.execute("DELETE FROM rejections WHERE poll_id BETWEEN ? AND ?", (start, end))
                    cur = self.conn.execute("DELETE FROM observations WHERE poll_id BETWEEN ? AND ?", (start, end))
                    result.observations += cur.rowcount
                    cur = self.conn.execute("DELETE FROM polls WHERE id BETWEEN ? AND ?", (start, end))
                    result.polls += cur.rowcount

        with self.conn:
            self.conn.execute("DELETE FROM process_stats WHERE at < ?", (cutoff,))
            # Only sessions this prune emptied; a session that never recorded a poll is left alone
            empty = [
                sid
                for sid in touched
                if sid != keep_session
                and self.conn.execute("SELECT 1 FROM polls WHERE session_id = ? LIMIT 1", (sid,)).fetchone() is None
            ]
            for session_id in empty:
                self.conn.execute("DELETE FROM process_stats WHERE session_id = ?", (session_id,))
                self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        # Raw files go with their sessions; a surviving session keeps its whole raw file
        for session_id in empty:
            self.raw_path(session_id).unlink(missing_ok=True)
        result.sessions = empty
        return result

    def raw_path(self, session_id: int) -> Path:
        return self.raw_dir / f"session-{session_id:04d}.jsonl.gz"

    def _append_raw(self, session_id: int, poll_id: int, result: PollResult) -> None:
        # Appending gzip members yields one stream that gzip.open reads straight through
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "poll_id": poll_id,
                "received_at": to_iso(result.received_at),
                "status_code": result.status_code,
                "body": result.raw_body.decode("utf-8", errors="replace"),
            }
        )
        with gzip.open(self.raw_path(session_id), "at", encoding="utf-8") as f:
            f.write(line + "\n")

    def resolve_session(self, ref: str) -> int:
        if ref == "latest":
            row = self.conn.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                raise LookupError("no recorded sessions")
            return row["id"]
        session_id = int(ref)
        if self.conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
            raise LookupError(f"session {session_id} not found")
        return session_id

    def sessions(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT s.*, COUNT(p.id) FILTER (WHERE p.kind = 'poll') AS polls
               FROM sessions s LEFT JOIN polls p ON p.session_id = s.id
               GROUP BY s.id ORDER BY s.id"""
        ).fetchall()

    def polls_between(self, session_id: int, start: datetime | None, end: datetime) -> list[PollResult]:
        """Polls (with observations) whose receipt time, or request time if never received, is in [start, end]."""
        effective = "COALESCE(received_at, requested_at)"
        params: list = [session_id, to_iso(end)]
        where = f"session_id = ? AND kind = 'poll' AND {effective} <= ?"
        if start is not None:
            where += f" AND {effective} >= ?"
            params.append(to_iso(start))
        polls = self.conn.execute(f"SELECT * FROM polls WHERE {where} ORDER BY id", params).fetchall()
        if not polls:
            return []
        by_poll: dict[int, list[Observation]] = {p["id"]: [] for p in polls}
        rows = self.conn.execute(
            f"SELECT * FROM observations WHERE poll_id IN (SELECT id FROM polls WHERE {where}) ORDER BY id", params
        )
        for row in rows:
            by_poll[row["poll_id"]].append(observation_from_row(row))
        provider = self.conn.execute("SELECT provider FROM sessions WHERE id = ?", (session_id,)).fetchone()[0]
        return [
            PollResult(
                provider=provider,
                requested_at=from_iso(p["requested_at"]),
                received_at=from_iso(p["received_at"]),
                latency_ms=p["latency_ms"],
                status_code=p["status_code"],
                provider_at=from_iso(p["provider_at"]),
                observations=by_poll[p["id"]],
                error_kind=ErrorKind(p["error_kind"]) if p["error_kind"] else None,
                error=p["error"],
                retry_after_s=p["retry_after_s"],
            )
            for p in polls
        ]

    def last_success_before(self, session_id: int, at: datetime) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(received_at) FROM polls WHERE session_id = ? AND kind = 'poll' "
            "AND error_kind IS NULL AND received_at <= ?",
            (session_id, to_iso(at)),
        ).fetchone()
        return from_iso(row[0])

    def session_bounds(self, session_id: int) -> tuple[datetime | None, datetime | None]:
        row = self.conn.execute(
            "SELECT MIN(COALESCE(received_at, requested_at)), MAX(COALESCE(received_at, requested_at)) "
            "FROM polls WHERE session_id = ? AND kind = 'poll'",
            (session_id,),
        ).fetchone()
        return from_iso(row[0]), from_iso(row[1])
