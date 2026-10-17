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
    with pytest.raises(ValueError):
        render(-118.42, 34.0, "Reversed")
