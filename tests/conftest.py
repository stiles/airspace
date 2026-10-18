from pathlib import Path

import pytest

from airspace.config import DEMO_LATITUDE, DEMO_LONGITUDE, Config
from airspace.storage import Store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_bytes() -> bytes:
    return (FIXTURES / "adsbfi_v3_la.json").read_bytes()


@pytest.fixture
def config(tmp_path) -> Config:
    return Config.model_validate(
        {
            "home": {"latitude": DEMO_LATITUDE, "longitude": DEMO_LONGITUDE},
            "storage": {"data_dir": str(tmp_path / "data")},
            # Tests opt in to catalogs explicitly; never the repo's own
            "enrich": {"hangarbay_db": None, "catalogs": []},
        }
    )


@pytest.fixture
def store(config):
    s = Store(config.storage.data_dir, store_raw=True)
    yield s
    s.close()
