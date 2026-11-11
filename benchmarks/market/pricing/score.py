"""Score price extraction against the labelled pricing benchmark (README.md).

    python benchmarks/market/pricing/score.py fixtures [--json out.json]
    python benchmarks/market/pricing/score.py live --raw <dir with run6/ run7/> [--json ...]

Run with PYTHONPATH at the gtm-crawler folder of the code to score (the current
tree, or an older one extracted with ``git archive``): code without price terms
(``Metric.price``) is scored on what its unit string says.

An extracted price matches a label when the amount (and percent-ness) agree;
precision = matched / extracted, recall = matched / labelled. Of the matched:
type accuracy (``pricing_type``), exact-unit accuracy (currency, percent,
period, billed, basis, basis quantity all as labelled), and each field. An
extracted price whose amount the label lists as not a price is a "non-price"
false positive. Live: the stored pages of runs 6-7 (``--raw``: the benchmark
runs' raw stores, not committed); each extracted evidence record is mapped to
the labelled sentence or table row its excerpt is in (when the same text is in
several, the first still expecting its amounts); records outside any labelled
item (e.g. JSON-LD) are counted apart, not scored. The live labellers saw only
each item's text (with the page's title and URL): a plan, product, billing or
date read from around it (a heading, the page) that the text does not state is
not judged there ("not_judged_outside_item"); the fixtures judge them. A table cell harvested both
as its own item and inside its row's item is scored once, in the row. The harvest
labelled each text once per page, and each amount once per item: a record that
repeats a labelled item's text elsewhere on the page (an add-on line on every
plan card), or a cell repeated within the labelled row, with only that item's
labelled amounts once these are matched, is counted apart
("repeats_of_labelled_text"), neither right nor wrong; with any other amount it
is scored.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from gtm_crawler.config import load_settings
from gtm_crawler.market.extract import extract_evidence, subject_terms
from gtm_crawler.parse import parse_page

HERE = Path(__file__).parent
TODAY = date(2026, 10, 21)
BASIC_FIELDS = ("currency", "period", "basis")
UNIT_FIELDS = ("currency", "percent", "period", "billed", "basis", "basis_quantity")
FIELDS = (
    "pricing_type",
    *UNIT_FIELDS,
    "bound",
    "plan",
    "promotional",
    "tax",
    "included",
    "currency_assumed",
    "product",
    "effective",
)
# Fields judged together per matched price (Score._record).
RECORD_METRICS = {
    "amount_currency": ("currency", "percent"),
    "period_billing_basis": ("period", "billed", "basis", "basis_quantity"),
    "product_plan": ("plan", "product"),
    "full_record": ("currency", "percent", "period", "basis", "basis_quantity", "plan", "product"),
}
# Labellers disagreed (live.json "about"): not scored.
DISPUTED = {("currency", "www.waveapps.com"), ("currency_assumed", "www.waveapps.com")}
_SYN = {"pound": "lb", "lbs": "lb", "licence": "license", "api calls": "api call",
        "api": "api call", "kilogram": "kg", "people": "person", "sms": "message",
        "text": "message", "text message": "message"}  # fmt: skip
_PERIOD_WORDS = {"month", "year", "week", "day", "hour", "quarter"}
settings = load_settings()
# The market collector's text where the code has its own (collect.parse_settings).
try:
    from gtm_crawler.market.collect import HTML_TEXT_VERSION, parse_settings

    PARSE = parse_settings(settings.parse, HTML_TEXT_VERSION)
except ImportError:  # code before text versions
    PARSE = settings.parse


def norm(field: str, v: Any) -> Any:
    if v is None:
        return None
    if field == "basis":
        b = str(v).lower().strip()
        b = _SYN.get(b, b)
        b = b[:-1] if b.endswith("s") and not b.endswith("ss") else b
        return _SYN.get(b, b)
    if field in ("plan", "product", "included", "effective"):
        return re.sub(r"\s+(plan|tier)$", "", " ".join(str(v).lower().split()))
    if field == "basis_quantity":
        return float(v)
    return v


def terms_of(m: Any) -> dict[str, Any]:
    """The extracted price's terms; for code without ``Metric.price``, what its unit says."""
    price = getattr(m, "price", None)
    if price is not None:
        d = price.model_dump(mode="json")
        d["amount"] = None if m.kind.value == "price_on_request" else m.value
        return d
    parts = m.unit.split("/")
    period = next((p for p in parts[1:] if p in _PERIOD_WORDS), None)
    basis = next((p for p in parts[1:] if p not in _PERIOD_WORDS), None)
    return {"amount": m.value, "currency": parts[0], "percent": False, "period": period,
            "basis": basis, "pricing_type": None, "billed": None, "basis_quantity": None,
            "bound": None, "plan": None, "promotional": None, "tax": None, "included": None,
            "currency_assumed": None, "product": None, "effective": None}  # fmt: skip


def extracted_prices(html: bytes, url: str, terms: set[str], geography: str | None,
                     vendor: str | None) -> list[tuple[str, list[dict[str, Any]]]]:  # fmt: skip
    """(excerpt, its prices' terms) of each evidence record that states a price."""
    page = parse_page(html, url, "text/html", PARSE, settings.urls)
    kwargs: dict[str, Any] = {"geography": geography, "today": TODAY,
                              "always_relevant": vendor is not None}  # fmt: skip
    try:
        found = extract_evidence(page, "src", terms, vendor=vendor, **kwargs)
    except TypeError:  # code before price terms
        found = extract_evidence(page, "src", terms, **kwargs)
    out = [
        (
            e.excerpt,
            [terms_of(m) for m in e.metrics if m.kind.value in ("price_point", "price_on_request")],
        )
        for e in found
    ]
    return [(x, ts) for x, ts in out if ts]


def same_amount(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-9)


class Score:
    def __init__(self) -> None:
        self.tp = self.fp = self.fn = self.fp_nonprice = 0
        self.right: Counter[str] = Counter()
        self.judged: Counter[str] = Counter()
        self.unit_ok = self.unit_judged = self.basic_ok = 0
        self.unjudged: Counter[str] = Counter()
        self.examples: dict[str, list[str]] = {"fp": [], "fn": [], "wrong": []}
        self.by_category: dict[str, Counter[str]] = {}
        # Record-level: each matched price judged on several fields together.
        self.records: Counter[str] = Counter()  # "<metric>_ok" / "<metric>_judged"
        self.fp_why: Counter[str] = Counter()
        self.fn_why: Counter[str] = Counter()
        self.attribution: Counter[str] = Counter()  # "<label value>:<ok|wrong>"
        self.unjudgeable_records = 0

    def page(self, where: str, host: str, expected: list[dict[str, Any]],
             not_prices: list[dict[str, Any]], got: list[dict[str, Any]],
             category: str = "", seen_text: str | None = None) -> None:  # fmt: skip
        cat = self.by_category.setdefault(category, Counter())
        free = list(range(len(got)))
        for exp in expected:
            cands = [i for i in free if same_amount(got[i]["amount"], exp["amount"])
                     and bool(got[i].get("percent")) == bool(exp.get("percent"))]  # fmt: skip
            if {"*", "amount"} & set(exp.get("unjudgeable", ())):
                # A price the labeller could not judge (or whose amount is unclear, so it
                # cannot be matched): neither right nor wrong, and an extracted price of
                # its amount is not a false positive.
                self.unjudgeable_records += 1
                if cands:
                    free.remove(cands[0])
                continue
            if not cands:
                self.fn += 1
                cat["fn"] += 1
                self.fn_why[f"{exp.get('layout', 'n/a')} / {exp['pricing_type']}"] += 1
                self.examples["fn"].append(f"{where}: {exp['amount']} {exp.get('currency')} "
                                           f"{exp['pricing_type']}")  # fmt: skip
                continue
            best = max(cands, key=lambda i: sum(norm(f, got[i].get(f)) == norm(f, exp.get(f))
                                                for f in FIELDS))  # fmt: skip
            free.remove(best)
            g = got[best]
            self.tp += 1
            cat["tp"] += 1
            wrong = []
            unit_ok = basic_ok = True
            for f in FIELDS:
                if (f, host) in DISPUTED or g.get(f, "n/a") is None and f == "pricing_type":
                    continue
                if f in exp.get("unjudgeable", ()):
                    self.unjudged[f"{f} (labelled unjudgeable)"] += 1
                    continue
                if g.get(f, "n/a") == "n/a":
                    continue
                if seen_text is not None and unseen(f, g.get(f), exp.get(f), seen_text):
                    self.unjudged[f] += 1
                    continue
                if f != "pricing_type" and terms_missing(g):
                    if f not in BASIC_FIELDS:
                        continue
                ok = norm(f, g.get(f)) == norm(f, exp.get(f))
                self.judged[f] += 1
                self.right[f] += ok
                if not ok:
                    wrong.append(f"{f}={g.get(f)!r} (label {exp.get(f)!r})")
                    if f in UNIT_FIELDS:
                        unit_ok = False
                    if f in BASIC_FIELDS:
                        basic_ok = False
            self.unit_judged += 1
            self.unit_ok += unit_ok
            self.basic_ok += basic_ok
            cat["unit_ok"] += unit_ok
            self._record(g, exp, host, seen_text)
            if wrong:
                self.examples["wrong"].append(f"{where}: {exp['amount']}: " + "; ".join(wrong))
        nonprice = [n["amount"] for n in not_prices]
        for i in free:
            self.fp += 1
            cat["fp"] += 1
            why = next((n.get("why", "not a price") for n in not_prices
                        if same_amount(got[i]["amount"], n["amount"])), None)  # fmt: skip
            self.fp_why[
                f"labelled not a price: {why}"
                if why
                else f"amount not labelled ({got[i].get('pricing_type')})"
            ] += 1
            if any(same_amount(got[i]["amount"], a) for a in nonprice):
                self.fp_nonprice += 1
            self.examples["fp"].append(f"{where}: {got[i]['amount']} {got[i].get('currency')} "
                                       f"{got[i].get('pricing_type')}")  # fmt: skip

    def _record(self, g: dict[str, Any], exp: dict[str, Any], host: str,
                seen_text: str | None) -> None:  # fmt: skip
        """Several fields of one matched price judged together: a correct amount with the
        wrong plan or product is not a correct product-price record."""
        skip = set(exp.get("unjudgeable", ()))

        def judgeable(f: str) -> bool:
            if f in skip or (f, host) in DISPUTED or terms_missing(g) and f not in BASIC_FIELDS:
                return False
            return not (seen_text is not None and unseen(f, g.get(f), exp.get(f), seen_text))

        def ok(f: str) -> bool:
            return bool(norm(f, g.get(f)) == norm(f, exp.get(f)))

        for name, fields in RECORD_METRICS.items():
            if all(judgeable(f) for f in fields):
                self.records[f"{name}_judged"] += 1
                self.records[f"{name}_ok"] += all(ok(f) for f in fields)
        if judgeable("plan") and judgeable("product"):
            verdict = "right" if ok("plan") and ok("product") else "wrong"
            self.attribution[f"label {exp.get('attribution', 'n/a')}: {verdict}"] += 1
            self.attribution[f"extracted from {g.get('attribution')}: {verdict}"] += 1

    def summary(self) -> dict[str, Any]:
        def pct(a: int, b: int) -> float | None:
            return round(100 * a / b, 1) if b else None

        return {
            "labelled": self.tp + self.fn, "extracted": self.tp + self.fp, "matched": self.tp,
            "precision": pct(self.tp, self.tp + self.fp), "recall": pct(self.tp, self.tp + self.fn),
            "false_positives": self.fp, "false_positives_non_price": self.fp_nonprice,
            "false_negatives": self.fn,
            "exact_unit_accuracy": pct(self.unit_ok, self.unit_judged),
            # What old code (a unit string only) can be judged on, for old-vs-new.
            "currency_period_basis_accuracy": pct(self.basic_ok, self.unit_judged),
            "field_accuracy": {f: pct(self.right[f], self.judged[f]) for f in FIELDS
                               if self.judged[f]},
            "not_judged_outside_item": dict(self.unjudged),
            "record_accuracy": {n: {"accuracy": pct(self.records[f"{n}_ok"],
                                                    self.records[f"{n}_judged"]),
                                    "judged": self.records[f"{n}_judged"]}
                                for n in RECORD_METRICS},
            "attribution": dict(sorted(self.attribution.items())),
            "false_positive_categories": dict(self.fp_why.most_common()),
            "false_negative_categories": dict(self.fn_why.most_common()),
            "unjudgeable_records": self.unjudgeable_records,
            "by_category": {c: {"precision": pct(v["tp"], v["tp"] + v["fp"]),
                                "recall": pct(v["tp"], v["tp"] + v["fn"]),
                                "exact_unit": pct(v["unit_ok"], v["tp"]), **dict(v)}
                            for c, v in sorted(self.by_category.items()) if c},
            "examples": {k: v[:400] for k, v in self.examples.items()},
        }  # fmt: skip


_BILLING_WORDS = re.compile(r"bill|annual|yearly|monthly|month-to-month|commit|prepa", re.I)


def unseen(field: str, got: Any, label: Any, text: str) -> bool:
    """Live labels saw only the item's text: a value read from around it (a heading's plan,
    the page's "prices as of", a billing toggle) that the text does not state is not judged."""
    if label is not None or got is None:
        return False
    if field == "billed":
        return not _BILLING_WORDS.search(text)
    if field in ("plan", "product", "included", "effective"):
        return str(got).lower() not in text.lower()
    return False


def terms_missing(g: dict[str, Any]) -> bool:
    return g.get("pricing_type") is None  # old code: only the unit string


def fixtures() -> dict[str, Any]:
    cases = json.load(open(HERE / "cases.json", encoding="utf-8"))["cases"]
    s = Score()
    render: Counter[str] = Counter()
    for case in cases:
        for n, pg in enumerate(case["pages"]):
            html = (f"<html><head><title>{pg['title']}</title></head><body>{pg['html']}"
                    "</body></html>").encode()  # fmt: skip
            got = [t for _, ts in extracted_prices(html, pg["url"], set(), case["geography"],
                                                    pg["vendor"]) for t in ts]  # fmt: skip
            s.page(f"{case['id']}#{n}", urlsplit(pg["url"]).hostname or "", pg["expected"],
                   pg["not_prices"], got, case["category"])  # fmt: skip
            if pg.get("needs_render"):
                page = parse_page(html, pg["url"], "text/html", PARSE, settings.urls)
                from gtm_crawler.market.extract import states_money

                fires = "pric" in urlsplit(pg["url"]).path.lower() and \
                    not states_money(page.main_text)  # fmt: skip
                render["needs_render"] += 1
                render["render_triggered"] += fires
    out = s.summary()
    out["render"] = dict(render)
    return out


def live(raw: Path) -> dict[str, Any]:
    items = json.load(open(HERE / "live.json", encoding="utf-8"))["items"]
    by_page: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for it in items:
        by_page.setdefault((it["run"], it["url"]), []).append(it)
    s, main = Score(), Score()
    unmapped = duplicates = repeats = 0
    for (run, url), its in by_page.items():
        case = its[0]["case"]
        report = json.load(open(raw / run.replace("run", "bench") / f"{case}.json",
                                encoding="utf-8"))["report"]  # fmt: skip
        req = report["request"]
        src = next(x for x in report["sources"] if x["url"] == url)
        sha = src["raw_sha256"]
        body = gzip.decompress((raw / run.replace("run", "bench") / "raw" / sha[:2] / sha[2:4] /
                                f"{sha}.gz").read_bytes())  # fmt: skip
        product = req.get("product") or {}
        terms = (
            subject_terms(req.get("industry"))
            or subject_terms(
                product.get("problem") or product.get("description") or product.get("name")
            )
            if req["mode"] == "product"
            else subject_terms(req["subject"], req.get("industry"))
        )
        vendor = its[0]["vendor"] if src["source_type"] == "company_website" else None
        got = extracted_prices(body, src["final_url"] or url, terms, req.get("geography"),
                               vendor)  # fmt: skip
        # A table cell was harvested both as its own block and inside its row: the row
        # (with its labels) is scored, the cell's copy of the same prices is not.
        cells = {c for i in its if i["kind"] == "table_row" for c in i["text"].split(" | ")}
        dup = [i for i in its if i["kind"] == "block" and i["text"] in cells]
        duplicates += len(dup)
        its = [i for i in its if i not in dup]
        per_item: dict[str, list[dict[str, Any]]] = {it["id"]: [] for it in its}
        mapped: Counter[tuple[str, str]] = Counter()
        wanted = {it["id"]: [e["amount"] for e in it["expected"]] for it in its}
        for excerpt, ts in got:
            # Inside an item, not inside one of its numbers ("50" in "$1,500").
            inside = re.compile(rf"(?<![\d.,]){re.escape(excerpt)}(?![\d.,]\d|\d)")
            cands = [i for i in its if i["text"] == excerpt or inside.search(i["text"])]
            if not cands:
                unmapped += len(ts)
                continue
            # The same text on several items (a cell repeated in rows): the record goes to
            # the first item still expecting its amounts.
            it = max(cands, key=lambda i: (sum(any(same_amount(t["amount"], a)
                                                   for a in wanted[i["id"]]) for t in ts),
                                           -cands.index(i)))  # fmt: skip
            labelled = [e["amount"] for e in it["expected"]]
            seen_here = mapped[(it["id"], excerpt)]
            mapped[(it["id"], excerpt)] += 1
            again = it["text"] == excerpt or seen_here < len(inside.findall(it["text"]))
            if (
                seen_here
                and again
                and all(any(same_amount(t["amount"], a) for a in labelled) for t in ts)
                and not any(any(same_amount(t["amount"], a) for a in wanted[it["id"]]) for t in ts)
            ):
                repeats += len(ts)  # the labelled text again (fmt: skip)
                continue
            for t in ts:
                per_item[it["id"]].append(t)
                hit = next((a for a in wanted[it["id"]] if same_amount(t["amount"], a)), None)
                if hit is not None:
                    wanted[it["id"]].remove(hit)
        host = urlsplit(url).hostname or ""
        for it in its:
            s.page(it["id"], host, it["expected"], it["not_prices"], per_item[it["id"]],
                   it["case"], it["text"])  # fmt: skip
            if it["region"] not in ("nav", "header", "footer", "aside"):
                main.page(it["id"], host, it["expected"], it["not_prices"],
                          per_item[it["id"]], it["case"], it["text"])  # fmt: skip
    out = s.summary()
    out["main_content_only"] = {k: v for k, v in main.summary().items()
                                if k not in ("examples", "by_category")}  # fmt: skip
    out["unmapped_extracted"] = unmapped
    out["cell_items_scored_in_their_row"] = duplicates
    out["repeats_of_labelled_text"] = repeats
    return out


def heldout(code: str, where: Path = HERE / "heldout", development: bool = False) -> dict[str, Any]:
    """The held-out set (heldout/README.md), scored once per (dataset version, code):
    every snapshot must still have its manifest hash, and an existing result for the same
    pair is never overwritten (a second run would be a second look at the same data).
    ``development``: a set already used to guide changes, scored as development data."""
    manifest = json.load(open(where / "manifest.json", encoding="utf-8"))
    labels = json.load(open(where / "labels.json", encoding="utf-8"))["pages"]
    out_path = where / "results" / f"{manifest['version']}__{code}.json"
    if out_path.exists() and not development:
        sys.exit(f"{out_path} exists: {manifest['version']} was already scored with {code}")
    s = Score()
    for pg in manifest["pages"]:
        html = gzip.decompress((where / "pages" / f"{pg['id']}.html.gz").read_bytes())
        if hashlib.sha256(html).hexdigest() != pg["snapshot_sha256"]:
            sys.exit(f"{pg['id']}: snapshot changed since the set was frozen")
        lab = labels[pg["id"]]
        got = [t for _, ts in extracted_prices(html, pg["url"], set(), pg.get("geography"),
                                                pg.get("vendor")) for t in ts]  # fmt: skip
        s.page(pg["id"], urlsplit(pg["url"]).hostname or "", lab["expected"],
               lab["not_prices"], got, pg.get("page_kind", ""))  # fmt: skip
    result = s.summary()
    result["dataset"] = {"version": manifest["version"], "pages": len(manifest["pages"]),
                         "code": code, "scored": date.today().isoformat(),
                         "development": development}  # fmt: skip
    if development:  # no held-out claim, nothing saved
        return result
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("set", choices=("fixtures", "live", "heldout"))
    ap.add_argument("--raw", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--code", help="heldout: the commit (or label) of the code scored")
    ap.add_argument("--set-dir", type=Path, default=HERE / "heldout", help="heldout: the set")
    ap.add_argument(
        "--development",
        action="store_true",
        help="heldout: a set already used for development (scored, not saved)",
    )
    a = ap.parse_args()
    if a.set == "heldout" and not a.code:
        ap.error("heldout needs --code (the commit scored)")
    result = (fixtures() if a.set == "fixtures" else live(a.raw) if a.set == "live"
              else heldout(a.code, a.set_dir, a.development))  # fmt: skip
    if a.json:
        a.json.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    brief = {k: v for k, v in result.items() if k not in ("examples", "by_category")}
    json.dump(brief, sys.stdout, indent=1)
