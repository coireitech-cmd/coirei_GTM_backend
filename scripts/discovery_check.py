"""Discovery results of several companies, checked: what to look at before trusting them.

    python scripts/discovery_check.py run1.json [run2.json ...]

Each file: ``{"job": <the discovery job's result>, "view": <GET .../competitors>}``.
Per company, the run's own warnings (searches that failed, no competitor of a kind,
candidates the model gave no answer for), then each competitor ``discovery.checks``
flags. Report only.
"""

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from gtm_crawler.discovery import COMPETITOR_CLASSES, checks


def run_warnings(job: dict[str, Any], view: dict[str, Any]) -> list[str]:
    stats = (job.get("discovery") or {}).get("stats") or job.get("stats") or {}
    out = []
    if errors := stats.get("search_errors"):
        out.append(f"{len(errors)} searches failed: {errors[0]}")
    listing = stats.get("listing_pages") or {}
    if listing.get("tried") and listing.get("read", 0) < listing["tried"] / 2:
        out.append(f"read {listing.get('read', 0)} of {listing['tried']} listing pages")
    dropped = [d for d in stats.get("dropped", []) if not d.get("class")]
    if failed := [d["name"] for d in dropped if d.get("pages")]:
        out.append(f"model gave no class, its pages read, for: {', '.join(failed)}")
    if unread := [d["name"] for d in dropped if not d.get("pages")]:
        out.append(f"no page of its own could be read: {', '.join(unread)}")
    kept = sum(len(v) for v in view["competitors"].values())
    if kept < 3:
        out.append(f"only {kept} kept")
    if empty := [c for c in COMPETITOR_CLASSES if not view["competitors"].get(c)]:
        out.append(f"none found: {', '.join(empty)}")
    if unconfirmed := view.get("unconfirmed"):
        out.append(f"profile unconfirmed: {', '.join(unconfirmed)}")
    return out


def main(paths: list[str]) -> None:
    tally: Counter[str] = Counter()
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        view = data["view"]
        print(f"\n== {view.get('domain')} (job {view.get('discovered_by_job')})")
        for w in run_warnings(data.get("job") or {}, view):
            print(f"  ! {w}")
        for cls, items in view["competitors"].items():
            for item in items:
                found = checks(item)
                tally.update(found)
                mark, score = "?" if found else " ", item["score"] or 0
                print(f"  {mark} {cls:10} {score:.2f} {item['name']} ({item['domain']})"
                      + "".join(f"\n        - {f}" for f in found))  # fmt: skip
    print("\n== checks, all companies")
    for check, n in tally.most_common():
        print(f"  {n:3} {check}")


if __name__ == "__main__":
    main(sys.argv[1:])
