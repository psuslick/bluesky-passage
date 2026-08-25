"""Load pure integration modules without installing Home Assistant."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).parents[1] / "custom_components" / "bluesky_passage"
if not ROOT.exists():
    # Tests are under /config/bluesky_passage_docs/tests in the install ZIP.
    ROOT = Path(__file__).parents[2] / "custom_components" / "bluesky_passage"
PACKAGE = "bluesky_passage_standalone"

package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules.setdefault(PACKAGE, package)


def load(name: str):
    fullname = f"{PACKAGE}.{name}"
    if fullname in sys.modules:
        return sys.modules[fullname]
    spec = importlib.util.spec_from_file_location(fullname, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[fullname] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


calculations = load("calculations")
parser = load("parser")
database = load("database")
exporting = load("exporting")
migration = load("migration")
garmin_dates = load("garmin_dates")
land = load("land")
routing = load("routing")

# weather.py is mostly pure normalization logic but imports two Home Assistant
# helpers for its runtime HTTP client. Tiny stand-ins keep regression tests
# dependency-free without pretending to exercise Home Assistant itself.
homeassistant = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
homeassistant_core = sys.modules.setdefault(
    "homeassistant.core", types.ModuleType("homeassistant.core")
)
homeassistant_core.HomeAssistant = object
homeassistant_helpers = sys.modules.setdefault(
    "homeassistant.helpers", types.ModuleType("homeassistant.helpers")
)
homeassistant_httpx = sys.modules.setdefault(
    "homeassistant.helpers.httpx_client",
    types.ModuleType("homeassistant.helpers.httpx_client"),
)
homeassistant_httpx.get_async_client = lambda _hass: None
httpx = sys.modules.setdefault("httpx", types.ModuleType("httpx"))
httpx.HTTPError = type("HTTPError", (Exception,), {})
httpx.HTTPStatusError = type("HTTPStatusError", (httpx.HTTPError,), {})
weather = load("weather")

homeassistant_aiohttp = sys.modules.setdefault(
    "homeassistant.helpers.aiohttp_client",
    types.ModuleType("homeassistant.helpers.aiohttp_client"),
)
homeassistant_aiohttp.async_get_clientsession = lambda _hass: None
coastline = load("coastline")
