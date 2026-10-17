import pytest

from airspace.geometry import METERS_PER_MILE, METERS_PER_NM, bearing_distance, destination, distance_m, within_radius

HOME = (34.0, -118.42)


def test_known_distance():
    # One degree of latitude at the equator on WGS84
    assert distance_m(0, 0, 1, 0) == pytest.approx(110_574.4, abs=1)


def test_units():
    assert 5 * METERS_PER_MILE == pytest.approx(8046.72)
    assert 10 * METERS_PER_NM == 18_520


def test_boundary_is_inside():
    lat, lon = destination(*HOME, 45, 5 * METERS_PER_MILE)
    assert within_radius(*HOME, lat, lon, 5 * METERS_PER_MILE + 1e-6)
    assert not within_radius(*HOME, lat, lon, 5 * METERS_PER_MILE - 1)


def test_bearing():
    lat, lon = destination(*HOME, 90, 1000)
    bearing, meters = bearing_distance(*HOME, lat, lon)
    assert bearing == pytest.approx(90, abs=0.01)
    assert meters == pytest.approx(1000, abs=0.01)


def test_reversed_coordinates_rejected():
    with pytest.raises(ValueError, match="reversed"):
        distance_m(-118.42, 34.0, 34.0, -118.42)
