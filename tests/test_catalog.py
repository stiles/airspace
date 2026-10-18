import pytest
from helpers import at, obs, poll

from airspace import export, report
from airspace.catalog import Catalog, CatalogError
from airspace.replay import RegistryCache
from airspace.state import StateBuilder

AIRCRAFT = """registration,icao_hex,operator,operator_short,role,callsign_prefix,type_note,source,checked
N230LA,a2097b,LAPD Air Support Division,LAPD,police,,AS350B3,FAA registry owner,2026-10-18
N190LA,a16926,LA County Fire Air Operations,LACoFD,fire,LAC,S-70A,FAA registry owner,
N955LA,,LASD Aero Bureau,LASD,police,,AS350B2,Inferred from base,
"""
PATTERNS = """pattern,operator,operator_short,role,source
CALIFORNIA HIGHWAY PATROL,California Highway Patrol,CHP,police,Agency name
\\b(BROADCASTING|TELEVISION)\\b,,,news,Generic: broadcaster
"""


@pytest.fixture
def catalog_dir(tmp_path):
    d = tmp_path / "catalogs"
    d.mkdir()
    (d / "la.csv").write_text(AIRCRAFT)
    (d / "operators.csv").write_text(PATTERNS)
    return d


@pytest.fixture
def catalog(catalog_dir):
    return Catalog.load([catalog_dir])


class Registry:
    """Stand-in for hangarbay: owner type and name per hex."""

    def __init__(self, records):
        self.records = records

    def lookup(self, ids):
        return {aid: self.records[aid] for aid in ids if aid in self.records}


def test_loads_both_kinds_of_file(catalog):
    assert len(catalog) == 3 and len(catalog.patterns) == 2
    assert [r.pattern for r, _ in catalog.callsign_prefixes] == [r"^LAC\d{1,4}$"]


def test_match_order_and_rules(catalog):
    by_hex = catalog.match(aircraft_id="A2097B", registration="N999XX")
    assert (by_hex.short, by_hex.rule, by_hex.checked) == ("LAPD", "catalog_hex", "2026-10-18")
    by_reg = catalog.match(aircraft_id="ad4835", registration="n955la")
    assert (by_reg.short, by_reg.rule) == ("LASD", "catalog_registration")
    by_pattern = catalog.match(aircraft_id="adbabd", owners=[None, "CALIFORNIA HIGHWAY PATROL, OFFICE OF AIR OPS"])
    assert (by_pattern.short, by_pattern.rule) == ("CHP", "owner_pattern")
    # A generic pattern names the operator after the owner (an organization)
    generic = catalog.match(aircraft_id="a00001", owners=["GRIFFIN TELEVISION OKC LLC"])
    assert (generic.name, generic.role) == ("GRIFFIN TELEVISION OKC LLC", "news")
    assert catalog.match(aircraft_id="a00002", owners=["CHARLIE PAPA LLC"], owner_type="7") is None


def test_callsign_prefix_only_for_helicopters(catalog):
    assert catalog.match(aircraft_id="a00003", callsign="LAC14", rotorcraft=True).short == "LACoFD"
    # LAC could be an airline's ICAO code; a fixed-wing LAC flight isn't county fire
    assert catalog.match(aircraft_id="a00003", callsign="LAC14", rotorcraft=False) is None
    assert catalog.match(aircraft_id="a00003", callsign="LACK", rotorcraft=True) is None


def test_individual_owners_become_private(catalog):
    op = catalog.match(aircraft_id="a00004", owners=["JANE Q PUBLIC"], owner_type="1")
    assert (op.role, op.name, op.rule) == ("private", None, "registry_owner_type")
    # A catalog entry still wins over owner type
    assert catalog.match(aircraft_id="a2097b", owner_type="1").role == "police"


@pytest.mark.parametrize(
    "row, message",
    [
        ("N1,,Op,,cops,,,src,", "role 'cops'"),
        (",,Op,,police,,,src,", "needs a registration or icao_hex"),
        ("N1,xyz,Op,,police,,,src,", "isn't six hex digits"),
        ("N1,,Op,,police,,,,", "needs a source"),
        ("N1,,,,police,,,src,", "needs an operator"),
        ("N1,,Op,,police,L4C,,src,", "callsign_prefix"),
        ("N230LA,,Other Agency,,fire,,,src,", "already listed as LAPD Air Support Division"),
    ],
)
def test_bad_rows_are_reported_with_location(tmp_path, row, message):
    path = tmp_path / "bad.csv"
    path.write_text(AIRCRAFT + row + "\n")
    with pytest.raises(CatalogError) as exc:
        Catalog.load([path])
    assert "bad.csv:5" in str(exc.value) and message in str(exc.value)


def test_missing_catalog_directory_means_no_catalog(tmp_path):
    assert len(Catalog.load([tmp_path / "absent"])) == 0


def _cache(catalog, records=None):
    cache = RegistryCache(Registry(records or {}), catalog=catalog)
    return cache


def test_snapshot_carries_operator_and_hides_private_owners(config, catalog):
    cache = _cache(catalog, {"a00004": {"owner_type": "1", "owner_name": "JANE Q PUBLIC", "n_number": "4Q"}})
    cache.resolve(["a2097b", "a00004", "a00005"])
    builder = StateBuilder(config, identity=cache)
    builder.ingest(poll(at(0), [
        obs(at(0), "a2097b", category="A7", registration="N230LA", owner_operator="LAPD AIR SUPPORT DIVISION"),
        obs(at(0), "a00004", category="A1", registration="N4Q", owner_operator="JANE Q PUBLIC"),
        obs(at(0), "a00005", category="A1"),
    ]))  # fmt: skip
    snap = {a["id"]: a for a in builder.snapshot(at(1))["aircraft"]}
    assert (snap["a2097b"]["operator_short"], snap["a2097b"]["role"], snap["a2097b"]["role_rule"]) == (
        "LAPD", "police", "catalog_hex",
    )  # fmt: skip
    assert snap["a00004"]["role"] == "private" and snap["a00004"]["operator"] is None
    assert snap["a00005"]["role"] is None
    assert "_operator" not in snap["a2097b"]

    private = builder.aircraft_detail("a00004", at(1))
    assert private["owner_operator"] is None and private["owner_hidden"] is True
    lapd = builder.aircraft_detail("a2097b", at(1))
    assert lapd["owner_operator"] == "LAPD AIR SUPPORT DIVISION"
    assert (lapd["role_source"], lapd["role_checked"], lapd["type_note"]) == ("FAA registry owner", "2026-10-18", "AS350B3")


def _record(store):
    sid = store.start_session("adsbfi", "test", {})
    for i in range(3):
        t = at(10 * i)
        store.record_poll(sid, poll(t, [
            obs(t, "a16926", category="A7", callsign="LAC19", miles=2 + 0.05 * i),
            obs(t, "a00004", category="A1", registration="N4Q", owner_operator="JANE Q PUBLIC", miles=3 + 0.05 * i),
        ]))  # fmt: skip
    return sid


def test_exports_carry_roles_and_drop_private_owner_names(config, store, catalog, tmp_path):
    sid = _record(store)
    cache = _cache(catalog, {"a00004": {"owner_type": "1", "owner_name": "JANE Q PUBLIC"}})
    rows = export.to_json(store, sid, config, cache)["observations"]
    fire = next(r for r in rows if r["aircraft_id"] == "a16926")
    assert (fire["operator_short"], fire["role"], fire["role_rule"]) == ("LACoFD", "fire", "catalog_hex")
    private = [r for r in rows if r["aircraft_id"] == "a00004"]
    assert all(r["owner_operator"] is None and r["role"] == "private" for r in private)

    tracks = export.to_geojson(store, sid, config, cache)["features"]
    assert {f["properties"]["aircraft_id"]: f["properties"]["role"] for f in tracks} == {
        "a16926": "fire", "a00004": "private",
    }  # fmt: skip

    folder = tmp_path / "run"
    export.to_flighttracer(store, sid, config, folder, "a00004", cache=cache)
    assert "JANE" not in (folder / "trace.csv").read_text()


def test_report_lists_operators(config, store, catalog):
    sid = _record(store)
    data = report.build(store, sid, config, Registry({"a00004": {"owner_type": "1"}}), catalog=catalog)
    assert data["operators"]["by_role"] == {"fire": 1, "private": 1}
    assert data["operators"]["by_operator"] == {"LA County Fire Air Operations": 1}
    text = report.render(data, config)
    assert "## Operators" in text and "| a16926 |  | LAC19 | LACoFD |" in text
    assert "JANE" not in text


def _config_file(tmp_path, catalog_path):
    path = tmp_path / "airspace.toml"
    path.write_text(
        f'[home]\nlatitude = 34.0\nlongitude = -118.42\n[storage]\ndata_dir = "{tmp_path / "data"}"\n'
        f'[enrich]\nhangarbay_db = "{tmp_path / "no-registry.duckdb"}"\ncatalogs = ["{catalog_path}"]\n'
    )
    return path


def test_cli_catalog_summary_and_errors(tmp_path, catalog_dir):
    from typer.testing import CliRunner

    from airspace.cli import app

    result = CliRunner().invoke(app, ["catalog", "-c", str(_config_file(tmp_path, catalog_dir))])
    assert result.exit_code == 0, result.output
    assert "3 aircraft and 2 owner patterns from 2 files" in result.output
    assert "police 2, fire 1" in result.output
    assert "Unconfirmed identifications (no checked date): 2" in result.output

    (catalog_dir / "broken.csv").write_text("registration,operator,role,source\nN1,Op,cops,src\n")
    result = CliRunner().invoke(app, ["catalog", "-c", str(_config_file(tmp_path, catalog_dir))])
    assert result.exit_code == 2 and "broken.csv:2: role 'cops'" in result.output
