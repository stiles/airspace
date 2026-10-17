"""Optional FAA registry lookups through hangarbay's published DuckDB.

Only US-registered aircraft are covered, and the data is as current as the
local hangarbay snapshot (`hangar update` refreshes it). Not used in the polling loop.
"""

from __future__ import annotations

from pathlib import Path

QUERY = """
SELECT lower(a.mode_s_code_hex) AS aircraft_id, a.n_number, m.maker, m.model, m.type AS faa_type,
       a.is_deregistered
FROM aircraft_decoded a
LEFT JOIN aircraft_make_model m USING (mfr_mdl_code)
WHERE lower(a.mode_s_code_hex) IN (SELECT unnest(?))
ORDER BY a.is_deregistered  -- prefer active registrations
"""


class HangarbayRegistry:
    def __init__(self, path: Path):
        self.path = path.expanduser()
        self._conn = None

    @classmethod
    def open_optional(cls, path: Path | None) -> HangarbayRegistry | None:
        """Return a registry if the database and duckdb are available; otherwise None."""
        if path is None or not path.expanduser().exists():
            return None
        try:
            import duckdb  # noqa: F401
        except ImportError:
            return None
        return cls(path)

    def _connect(self):
        if self._conn is None:
            import duckdb

            self._conn = duckdb.connect(str(self.path), read_only=True)
        return self._conn

    def lookup(self, aircraft_ids: list[str]) -> dict[str, dict]:
        # Non-ICAO addresses (e.g. "~298a99") cannot match the registry
        ids = sorted({aid.lower() for aid in aircraft_ids if not aid.startswith("~")})
        if not ids:
            return {}
        cursor = self._connect().execute(QUERY, [ids])
        columns = [d[0] for d in cursor.description]
        records: dict[str, dict] = {}
        for row in cursor.fetchall():
            record = dict(zip(columns, row))
            records.setdefault(record["aircraft_id"], record)
        return records

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
