#!/usr/bin/env python3
"""Make a manual, provenance-preserving PredictWind tracking snapshot.

PredictWind does not document the public page's embedded route payload as a
stable API. This tool is intentionally manual: it saves the page-derived
records once, with URL/time/hash metadata, so the Home Assistant panel can
import them later without continuously scraping an undocumented interface.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

class NextDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_next_data = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag.lower() == "script" and attributes.get("id") == "__NEXT_DATA__":
            self.in_next_data = True

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.in_next_data:
            self.in_next_data = False

    def handle_data(self, data):
        if self.in_next_data:
            self.parts.append(data)


def find_record_array(root) -> list[dict]:
    """Find the largest nested array with a time and position shape."""
    best: list[dict] = []
    queue: list[tuple[object, int]] = [(root, 0)]
    seen: set[int] = set()
    while queue:
        value, depth = queue.pop(0)
        if depth > 14 or not isinstance(value, (dict, list)) or id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, list):
            sample = next((item for item in value if isinstance(item, dict)), None)
            if sample:
                has_time = any(
                    key in sample
                    for key in ("t", "time", "timestamp", "recorded_at_utc", "Time UTC")
                )
                has_position = any(
                    key in sample for key in ("p", "latitude", "lat", "geometry")
                )
                if has_time and has_position and len(value) > len(best):
                    best = value
            queue.extend((item, depth + 1) for item in value[:50])
        else:
            queue.extend((item, depth + 1) for item in value.values())
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        required=True,
        help="Public PredictWind tracking URL (kept out of the source repository)",
    )
    parser.add_argument("--output", type=Path, help="Output JSON filename")
    args = parser.parse_args()
    captured_at = datetime.now(timezone.utc)
    output = args.output or Path(
        f"predictwind-snapshot-{captured_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    request = Request(
        args.url,
        headers={"User-Agent": "BlueSky-Passage-manual-snapshot/2.0"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            body = response.read()
    except (HTTPError, URLError, TimeoutError) as err:
        print(f"Download failed: {err}", file=sys.stderr)
        return 2

    html_parser = NextDataParser()
    html_parser.feed(body.decode("utf-8", errors="replace"))
    if not html_parser.parts:
        print(
            "No __NEXT_DATA__ payload was found. PredictWind may have changed the page; do not import an empty file.",
            file=sys.stderr,
        )
        return 3
    try:
        payload = json.loads("".join(html_parser.parts))
    except json.JSONDecodeError as err:
        print(f"Embedded JSON could not be decoded: {err}", file=sys.stderr)
        return 4
    records = find_record_array(payload)
    if not records:
        print(
            "No timestamped position array was found. PredictWind may have changed its schema.",
            file=sys.stderr,
        )
        return 5

    artifact = {
        "metadata": {
            "source": "predictwind_public_tracking_page",
            "source_url": args.url,
            "captured_at_utc": captured_at.isoformat().replace("+00:00", "Z"),
            "page_sha256": hashlib.sha256(body).hexdigest(),
            "record_count": len(records),
            "warning": "Undocumented public-page payload; preserve as provenance, not a stable API contract.",
        },
        "records": records,
    }
    output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Saved {len(records):,} records to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
