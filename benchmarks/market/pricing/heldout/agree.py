"""Agreement of the two held-out labellers (labels_a.json, labels_b.json).

    python benchmarks/market/pricing/heldout/agree.py [set directory, default: this one]

Per page, B's prices are paired with A's by amount and percent-ness (the closest on the
other fields when several share an amount); every unpaired price and every field the two
pair disagree on is written to ``disagreements.json`` for adjudication. Prints counts only.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
FIELDS = ("currency", "currency_assumed", "percent", "pricing_type", "period", "billed",
          "basis", "basis_quantity", "bound", "plan", "product", "promotional", "tax",
          "included", "effective", "attribution")  # fmt: skip


def norm(v: Any) -> Any:
    return " ".join(v.lower().split()) if isinstance(v, str) else v


def same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-9)


def pair(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> tuple[list, list, list]:
    free = list(range(len(b)))
    pairs, only_a = [], []
    for x in a:
        cands = [i for i in free if same(b[i]["amount"], x["amount"])
                 and bool(b[i].get("percent")) == bool(x.get("percent"))]  # fmt: skip
        if not cands:
            only_a.append(x)
            continue
        best = max(cands, key=lambda i: sum(norm(b[i].get(f)) == norm(x.get(f)) for f in FIELDS))
        free.remove(best)
        pairs.append((x, b[best]))
    return pairs, only_a, [b[i] for i in free]


def main() -> None:
    a = json.load(open(HERE / "labels_a.json", encoding="utf-8"))["pages"]
    b = json.load(open(HERE / "labels_b.json", encoding="utf-8"))["pages"]
    out: dict[str, Any] = {}
    counts: Counter[str] = Counter()
    fields: Counter[str] = Counter()
    for page in sorted(set(a) | set(b)):
        pa, pb = a.get(page, {}).get("expected", []), b.get(page, {}).get("expected", [])
        pairs, only_a, only_b = pair(pa, pb)
        diffs = []
        for x, y in pairs:
            d = {f: [x.get(f), y.get(f)] for f in FIELDS if norm(x.get(f)) != norm(y.get(f))}
            counts["paired"] += 1
            counts["paired_identical"] += not d
            fields.update(d.keys())
            if d:
                diffs.append({"amount": x["amount"], "a_text": x.get("evidence_text"),
                              "b_text": y.get("evidence_text"), "fields": d})  # fmt: skip
        counts["only_a"] += len(only_a)
        counts["only_b"] += len(only_b)
        if diffs or only_a or only_b:
            out[page] = {"field_disagreements": diffs, "only_a": only_a, "only_b": only_b}
    (HERE / "disagreements.json").write_text(json.dumps(out, indent=1, ensure_ascii=False),
                                             encoding="utf-8")  # fmt: skip
    print(json.dumps({"counts": dict(counts), "fields": dict(fields.most_common()),
                      "pages_with_disagreements": len(out)}, indent=1))  # fmt: skip


if __name__ == "__main__":
    main()
