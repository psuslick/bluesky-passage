#!/usr/bin/env python3
"""Run standalone structural checks for a BlueSky Passage source bundle."""

from __future__ import annotations

import compileall
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
if HERE.name == "tools":
    # Source-tree layout.
    ROOT = HERE.parent
    DOCUMENTS = ROOT
    TESTS = ROOT / "tests"
    TOOLS = ROOT / "tools"
else:
    # Home Assistant installation-ZIP layout:
    # /config/bluesky_passage_docs/validate_bundle.py
    ROOT = HERE.parent
    DOCUMENTS = HERE
    TESTS = HERE / "tests"
    TOOLS = ROOT / "bluesky_passage_tools"
COMPONENT = ROOT / "custom_components" / "bluesky_passage"

REQUIRED_MANIFEST_KEYS = {
    "domain",
    "name",
    "version",
    "codeowners",
    "config_flow",
    "documentation",
    "issue_tracker",
    "iot_class",
}
PRIVATE_PUBLICATION_PATTERNS = (
    (
        re.compile(
            r"https://(?:www\.)?predictwind\.com/tracking/"
            r"(?!YOUR-PUBLIC-ID(?:[\s\"'<>]|$))[^\s\"'<>]+"
        ),
        "specific PredictWind tracking URL",
    ),
    (
        re.compile(
            r"https://share\.garmin\.com/"
            r"(?!Feed/Share/\{\}|\{|YOUR-)[A-Za-z0-9_-]+"
        ),
        "specific Garmin share URL",
    ),
    (
        re.compile(
            r"notify\.mobile_app_(?!(?:your_phone|name)\b)[A-Za-z0-9_]+"
        ),
        "specific mobile notification action",
    ),
    (
        re.compile(r"vol\.Required\(\s*CONF_LINK_NAME\s*,\s*default="),
        "hard-coded MapShare default",
    ),
)


def _load_json(path: Path, errors: list[str]) -> dict:
    """Load an object-shaped JSON document and report a useful error."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        errors.append(f"{path.relative_to(ROOT)}: {err}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.relative_to(ROOT)}: expected a JSON object")
        return {}
    return value


def main() -> int:
    errors: list[str] = []
    manifest = _load_json(COMPONENT / "manifest.json", errors)
    _load_json(COMPONENT / "strings.json", errors)
    _load_json(COMPONENT / "translations" / "en.json", errors)
    hacs = _load_json(ROOT / "hacs.json", errors)

    version = (DOCUMENTS / "VERSION").read_text(encoding="utf-8").strip()
    if manifest.get("version") != version:
        errors.append("manifest.json and VERSION disagree")
    if version not in (DOCUMENTS / "README.md").read_text(encoding="utf-8"):
        errors.append("README does not contain VERSION")
    if f"## {version}" not in (DOCUMENTS / "CHANGELOG.md").read_text(
        encoding="utf-8"
    ):
        errors.append("CHANGELOG does not contain the current VERSION")

    missing_manifest = sorted(REQUIRED_MANIFEST_KEYS - manifest.keys())
    if missing_manifest:
        errors.append(
            "manifest.json is missing: " + ", ".join(missing_manifest)
        )
    if manifest.get("domain") != "bluesky_passage":
        errors.append("manifest.json domain is not bluesky_passage")
    if manifest.get("codeowners") != ["@psuslick"]:
        errors.append("manifest.json codeowners does not identify @psuslick")
    if hacs.get("name") != "BlueSky Passage":
        errors.append("hacs.json name is not BlueSky Passage")

    integration_dirs = sorted(
        path.name
        for path in (ROOT / "custom_components").iterdir()
        if path.is_dir()
    )
    if integration_dirs != ["bluesky_passage"]:
        errors.append(
            "custom_components must contain only the bluesky_passage integration"
        )

    for required in (
        ROOT / "README.md",
        ROOT / "LICENSE",
        ROOT / ".github" / "workflows" / "validate.yml",
        COMPONENT / "brand" / "icon.svg",
        COMPONENT / "brand" / "icon.png",
    ):
        if not required.is_file():
            errors.append(
                f"Required repository file is absent: {required.relative_to(ROOT)}"
            )

    icon_png = COMPONENT / "brand" / "icon.png"
    if icon_png.is_file():
        data = icon_png.read_bytes()
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
            errors.append("brand/icon.png is not a valid PNG header")
        else:
            width, height = struct.unpack(">II", data[16:24])
            if (width, height) != (256, 256):
                errors.append("brand/icon.png must be exactly 256 x 256 pixels")

    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() in {".png", ".pyc"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, description in PRIVATE_PUBLICATION_PATTERNS:
            if pattern.search(content):
                errors.append(
                    f"Found {description} in {path.relative_to(ROOT)}"
                )

    if not compileall.compile_dir(COMPONENT, quiet=1):
        errors.append("Python integration compile failed")
    if not compileall.compile_dir(TOOLS, quiet=1):
        errors.append("Python tools compile failed")

    javascript = COMPONENT / "frontend" / "bluesky-passage-panel.js"
    if shutil.which("node"):
        result = subprocess.run(
            ["node", "--check", str(javascript)], capture_output=True, text=True
        )
        if result.returncode:
            errors.append(f"JavaScript syntax: {result.stderr.strip()}")

    frontend_text = javascript.read_text(encoding="utf-8")
    backend_text = (COMPONENT / "websocket.py").read_text(encoding="utf-8")
    client_commands = set(re.findall(r'this\._call\("([a-z_]+)"', frontend_text))
    server_commands = set(re.findall(r'f"\{DOMAIN\}/([a-z_]+)"', backend_text))
    missing = sorted(client_commands - server_commands)
    if missing:
        errors.append(f"Frontend calls missing WebSocket commands: {', '.join(missing)}")

    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        errors.append("Core regression tests failed")

    if errors:
        print("Bundle validation FAILED:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Bundle validation passed for BlueSky Passage {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
