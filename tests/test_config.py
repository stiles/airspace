import tomllib

import pytest
from pydantic import ValidationError

from airspace.config import Config, render


def make(**sections):
    return Config.model_validate({"home": {"latitude": 34.0, "longitude": -118.42}, **sections})


def test_defaults():
    c = make()
    assert c.visible_radius_m < c.collection_radius_m


def test_visible_must_fit_collection():
    with pytest.raises(ValidationError, match="exceeds collection radius"):
        make(area={"visible_radius_mi": 12, "collection_radius_nm": 10})


def test_timeout_below_interval():
    with pytest.raises(ValidationError, match="timeout_s"):
        make(collector={"poll_interval_s": 5, "timeout_s": 5})


def test_provider_radius_cap():
    with pytest.raises(ValidationError, match="250"):
        make(area={"collection_radius_nm": 300})


def test_template_renders_valid_config():
    assert "latitude = 34.0" in render(34.0, -118.42, "Home")
    text = render(34.0, -118.42, "Home", 'Jim\'s "Air" space')
    assert tomllib.loads(text)["home"]["title"] == "Jim's Air space"
    defaults = tomllib.loads(render(34.0, -118.42, "Home"))
    assert defaults["home"]["title"] == "Airspace"
    assert defaults["display"]["tv_display"] is False
    assert defaults["display"]["timezone"] == "America/Los_Angeles"
    with pytest.raises(ValueError):
        render(-118.42, 34.0, "Reversed")


def test_display_timezone_must_be_iana():
    base = {"home": {"latitude": 34.0, "longitude": -118.42}}
    assert Config.model_validate({**base, "display": {"timezone": "America/Denver"}}).display.timezone == "America/Denver"
    with pytest.raises(ValueError, match="unknown timezone"):
        Config.model_validate({**base, "display": {"timezone": "Pacific/NotAZone"}})


def test_la_boundary_toggle_uses_the_bundled_file_once():
    from pathlib import Path

    from airspace.config import LA_BOUNDARY, Display

    assert LA_BOUNDARY.is_file()
    assert Display().outline_paths == []
    assert Display(show_la_boundary=True).outline_paths == [LA_BOUNDARY]
    other = Path("elsewhere.geojson")
    # Listing the bundled file as well doesn't draw it twice
    both = Display(show_la_boundary=True, outlines=[LA_BOUNDARY, other])
    assert both.outline_paths == [LA_BOUNDARY, other]


def test_filters_default_and_altitude_falls_back_to_area():
    base = {"home": {"latitude": 34.0, "longitude": -118.42}}
    config = Config.model_validate(base)
    assert config.filters.max_altitude_baro_ft is None and config.filters.list_visible_only
    assert config.display.show_filters
    legacy = Config.model_validate({**base, "area": {"max_altitude_baro_ft": 10000}})
    assert legacy.filters.max_altitude_baro_ft == 10000
    both = Config.model_validate(
        {**base, "area": {"max_altitude_baro_ft": 10000}, "filters": {"max_altitude_baro_ft": 5000}}
    )
    assert both.filters.max_altitude_baro_ft == 5000
