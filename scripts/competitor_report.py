"""The competitor discovery results as a review report (HTML, and PDF through Chromium).

    python scripts/competitor_report.py view.json out.html [out.pdf]

``view.json``: the body of ``GET /v1/companies/{id}/competitors``, or an object with
it under ``view``. Report only: what it shows is what the API returned.
"""

import html
import json
import sys
from pathlib import Path
from typing import Any

CLASSES = {"direct": "Direct competitors", "indirect": "Indirect competitors",
           "substitute": "Substitutes", "emerging": "Emerging competitors"}  # fmt: skip
BASIS = {"named_by_source": "named by a source", "inferred_overlap": "overlap inferred",
         "user_added": "added by you"}  # fmt: skip


def esc(value: Any) -> str:
    return html.escape(str(value))


def cited(node: Any) -> str:
    """A cited value (or list of them) as text with its sources as links."""
    nodes = node if isinstance(node, list) else [node]
    parts = []
    for n in nodes:
        if not isinstance(n, dict) or "value" not in n:
            continue
        urls = list(dict.fromkeys(c["url"] for c in n.get("citations", []) if c.get("url")))
        refs = " ".join(f'<a href="{esc(u)}">[{i + 1}]</a>' for i, u in enumerate(urls[:3]))
        parts.append(f"{esc(n['value'])} <span class=src>{refs}</span>")
    return "; ".join(parts)


def card(c: dict[str, Any]) -> str:
    p = c.get("profile") or {}
    rows = [(label, cited(p.get(key))) for key, label in (
        ("sells", "Sells"), ("positioning", "Positioning"), ("pricing", "Pricing"),
        ("target_customers", "For"), ("same_buyers", "Same buyers"),
        ("same_problem", "Same problem"), ("same_budget", "Same budget"))]  # fmt: skip
    found = "".join(
        f'<li>{esc(f.get("how"))}: <a href="{esc(f["url"])}">{esc(f.get("title") or f["url"])}'
        f"</a> <span class=q>({esc(f.get('query') or '')})</span></li>"
        for f in c.get("found_by", [])[:4] if f.get("url")
    )  # fmt: skip
    if any(f.get("how") == "alternative" for f in c.get("found_by", [])):
        found += "<li>the business profile names it as what buyers use instead</li>"
    score = "" if c.get("score") is None else f"{c['score']:.2f}"
    checks = "".join(f"<li>{esc(x)}</li>" for x in c.get("checks") or [])
    return f"""<section class=card>
<h3>{esc(c["name"])} <a href="https://{esc(c["domain"])}/">{esc(c["domain"])}</a></h3>
<p class=meta>score {score} · {esc(BASIS.get(c["basis"], c["basis"]))} ·
review: <b>{esc(c.get("review"))}</b></p>
{f"<div class=warn>Check first:<ul>{checks}</ul></div>" if checks else ""}
{f"<p>{esc(c['reasoning'])}</p>" if c.get("reasoning") else ""}
<table>{"".join(f"<tr><th>{esc(k)}</th><td>{v}</td></tr>" for k, v in rows if v)}</table>
{f"<p class=meta>Found on:</p><ul>{found}</ul>" if found else ""}
</section>"""


def run_note(job: dict[str, Any] | None, view: dict[str, Any]) -> str:
    """What the run searched for and its warnings (``discovery_check.run_warnings``)."""
    if not job:
        return ""
    from discovery_check import run_warnings

    stats = (job.get("discovery") or {}).get("stats") or {}
    hyp = stats.get("hypotheses") or {}
    keys = ("kind", "where", "categories", "rivals", "alternatives")
    searched = ", ".join(f"{k}: {esc('; '.join(hyp[k]))}" for k in keys if hyp.get(k))
    warn = "".join(f"<li>{esc(w)}</li>" for w in run_warnings(job, view))
    return (f"<p class=meta>Searched as {searched} · {len(stats.get('queries') or {})} queries"
            f" · {stats.get('candidates', 0)} candidates, {stats.get('profiled', 0)} read</p>"
            + (f"<div class=warn>Run warnings:<ul>{warn}</ul></div>" if warn else ""))  # fmt: skip


def report(view: dict[str, Any], title: str, job: dict[str, Any] | None = None) -> str:
    body = [run_note(job, view)]
    for cls, label in CLASSES.items():
        items = view["competitors"].get(cls) or []
        body.append(f"<h2>{label} ({len(items)})</h2>")
        body += [card(c) for c in items] or ["<p class=meta>None found.</p>"]
    unconfirmed = view.get("unconfirmed") or []
    note = (f"<p class=warn>Based on profile fields not yet confirmed: "
            f"{esc(', '.join(unconfirmed))}.</p>" if unconfirmed else "")  # fmt: skip
    return f"""<!doctype html><html><head><meta charset=utf-8><title>{esc(title)}</title>
<style>body{{font:14px/1.45 system-ui,sans-serif;max-width:900px;margin:24px auto;
padding:0 16px;color:#1d1d1f}}h2{{border-bottom:1px solid #ddd;padding-bottom:4px;
margin-top:28px}}.card{{border:1px solid #e3e3e3;border-radius:8px;padding:10px 14px;
margin:10px 0;break-inside:avoid}}.card h3{{margin:2px 0}}.card h3 a{{font-size:12px;
font-weight:400;margin-left:6px}}.meta,.q,.src{{color:#666;font-size:12px}}
th{{text-align:left;vertical-align:top;padding-right:12px;white-space:nowrap;
font-weight:600}}.warn{{background:#fff4e5;padding:8px;border-radius:6px}}</style>
</head><body><h1>{esc(title)}</h1>
<p class=meta>Counts: {esc(view.get("counts"))} · discovery job
{esc(view.get("discovered_by_job"))}</p>{note}{"".join(body)}</body></html>"""


def main() -> None:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    view = data.get("view", data)
    out = Path(sys.argv[2])
    title = f"Competitors of {view.get('domain') or view['company_id']}"
    out.write_text(report(view, title, data.get("job")), encoding="utf-8")
    if len(sys.argv) > 3:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(out.resolve().as_uri())
            page.pdf(path=sys.argv[3], format="A4", print_background=True,
                     margin={"top": "12mm", "bottom": "12mm"})  # fmt: skip
            browser.close()


if __name__ == "__main__":
    main()
