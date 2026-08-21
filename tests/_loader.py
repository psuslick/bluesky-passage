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
