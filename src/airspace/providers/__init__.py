from airspace.providers.adsbfi import AdsbFi
from airspace.providers.base import Provider

PROVIDERS: dict[str, type] = {AdsbFi.name: AdsbFi}


def get_provider(name: str) -> Provider:
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise ValueError(f"unknown provider {name!r}; available: {', '.join(PROVIDERS)}") from None
