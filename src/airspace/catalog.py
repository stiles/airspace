"""Who operates an aircraft and in what role, from curated catalog files.

Two kinds of CSV live in the catalog directory, told apart by their header:

- Aircraft files (a `registration` or `icao_hex` column): one row per aircraft.
- Operator files (a `pattern` column): regular expressions matched against the
  registered owner's name, for aircraft no aircraft file lists.

Matching order: ICAO hex, registration, callsign prefix (helicopters only, since a
three-letter prefix can collide with an airline's ICAO code), owner pattern, then
private when the FAA registry lists individuals as owners. Each match records its
rule. Nothing is guessed from aircraft type or flight pattern.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

ROLES = ("police", "fire", "medical", "news", "military", "tour", "utility", "training", "private")
# Roles whose aircraft respond to emergencies
PUBLIC_SAFETY = frozenset({"police", "fire", "medical"})

# FAA registry owner types: 1 individual, 4 co-owned (typically individuals)
INDIVIDUAL_OWNER_TYPES = frozenset({"1", "4"})

HEX = re.compile(r"^[0-9a-f]{6}$")
ROLE_CODES = {
    "police": "PD", "fire": "FD", "medical": "MED", "news": "NEWS", "military": "MIL",
    "tour": "TOUR", "utility": "UTIL", "training": "TRN", "private": "PVT",
}  # fmt: skip


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Operator:
    name: str | None  # None for private owners: never shown
    short: str | None
    role: str
    rule: str  # catalog_hex, catalog_registration, catalog_callsign, owner_pattern or registry_owner_type
    source: str | None = None
    checked: str | None = None
    note: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def private(self) -> bool:
        return self.role == "private"


def normalize_registration(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().upper().replace("-", "") or None


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern
    operator: Operator


class Catalog:
    def __init__(self):
        self.by_hex: dict[str, Operator] = {}
        self.by_registration: dict[str, Operator] = {}
        self.callsign_prefixes: list[tuple[re.Pattern, Operator]] = []
        self.patterns: list[_Pattern] = []
        self.files: list[Path] = []
        # One (icao_hex, registration, operator) per aircraft row, for checks against the registry
        self.rows: list[tuple[str | None, str | None, Operator]] = []

    def __len__(self) -> int:
        return len(self.rows)

    @classmethod
    def load(cls, paths: Iterable[Path]) -> Catalog:
        """Load CSV files and directories of them. Problems are collected and raised together."""
        catalog = cls()
        errors: list[str] = []
        for path in paths:
            path = Path(path).expanduser()
            if path.is_dir():
                files = sorted(path.glob("*.csv"))
            elif path.exists():
                files = [path]
            else:
                continue  # an absent catalog directory means no catalog
            for file in files:
                catalog._load_file(file, errors)
        if errors:
            raise CatalogError("catalog problems:\n" + "\n".join(f"  {e}" for e in errors))
        return catalog

    def _load_file(self, file: Path, errors: list[str]) -> None:
        with file.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            columns = set(reader.fieldnames or [])
            if "pattern" in columns:
                load = self._pattern_row
            elif columns & {"registration", "icao_hex"}:
                load = self._aircraft_row
            else:
                errors.append(f"{file}: header needs a pattern, registration or icao_hex column")
                return
            self.files.append(file)
            for line, row in enumerate(reader, start=2):
                row = {k: (v or "").strip() for k, v in row.items() if k}
                try:
                    load(row)
                except CatalogError as exc:
                    errors.append(f"{file}:{line}: {exc}")

    @staticmethod
    def _role(row: dict) -> str:
        role = row.get("role", "").lower()
        if role not in ROLES:
            raise CatalogError(f"role {role!r} is not one of {', '.join(ROLES)}")
        return role

    def _aircraft_row(self, row: dict) -> None:
        role = self._role(row)
        hex_id = row.get("icao_hex", "").lower()
        registration = normalize_registration(row.get("registration"))
        if not hex_id and not registration:
            raise CatalogError("needs a registration or icao_hex")
        if hex_id and not HEX.match(hex_id):
            raise CatalogError(f"icao_hex {hex_id!r} isn't six hex digits")
        if not row.get("operator") and role != "private":
            raise CatalogError("needs an operator")
        if not row.get("source"):
            raise CatalogError("needs a source for the identification")

        def entry(rule: str) -> Operator:
            return Operator(
                name=row.get("operator") or None, short=row.get("operator_short") or None, role=role, rule=rule,
                source=row["source"], checked=row.get("checked") or None, note=row.get("type_note") or None,
            )  # fmt: skip

        for table, key, rule in ((self.by_hex, hex_id, "catalog_hex"), (self.by_registration, registration, "catalog_registration")):
            if not key:
                continue
            existing = table.get(key)
            if existing and (existing.name, existing.role) != (row.get("operator") or None, role):
                raise CatalogError(f"{key} is already listed as {existing.name} ({existing.role})")
            table[key] = entry(rule)
        self.rows.append((hex_id or None, registration, entry("catalog_hex" if hex_id else "catalog_registration")))
        if prefix := row.get("callsign_prefix", "").upper():
            if not re.fullmatch(r"[A-Z]{2,4}", prefix):
                raise CatalogError(f"callsign_prefix {prefix!r} should be 2-4 letters")
            regex = re.compile(rf"^{prefix}\d{{1,4}}$")
            if not any(r.pattern == regex.pattern for r, _ in self.callsign_prefixes):
                self.callsign_prefixes.append((regex, entry("catalog_callsign")))

    def _pattern_row(self, row: dict) -> None:
        role = self._role(row)
        if not row.get("source"):
            raise CatalogError("needs a source")
        try:
            regex = re.compile(row["pattern"], re.IGNORECASE)
        except re.error as exc:
            raise CatalogError(f"pattern {row['pattern']!r}: {exc}") from None
        self.patterns.append(
            _Pattern(
                regex,
                Operator(
                    name=row.get("operator") or None, short=row.get("operator_short") or None, role=role,
                    rule="owner_pattern", source=row["source"],
                ),
            )  # fmt: skip
        )

    def match(
        self,
        *,
        aircraft_id: str,
        registration: str | None = None,
        callsign: str | None = None,
        owners: Iterable[str | None] = (),
        rotorcraft: bool = False,
        owner_type: str | None = None,
    ) -> Operator | None:
        if op := self.by_hex.get(aircraft_id.lower()):
            return op
        if (reg := normalize_registration(registration)) and (op := self.by_registration.get(reg)):
            return op
        if rotorcraft and callsign:
            for regex, op in self.callsign_prefixes:
                if regex.match(callsign.strip().upper()):
                    return op
        for owner in owners:
            if not owner:
                continue
            for p in self.patterns:
                if p.regex.search(owner):
                    # A pattern without an operator name uses the owner's own (organizations only)
                    return p.operator if p.operator.name else Operator(
                        name=owner.strip(), short=None, role=p.operator.role, rule="owner_pattern", source=p.operator.source
                    )  # fmt: skip
        if owner_type in INDIVIDUAL_OWNER_TYPES:
            return Operator(name=None, short=None, role="private", rule="registry_owner_type", source="FAA registry")
        return None
