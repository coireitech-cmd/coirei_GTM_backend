"""Same fixture scenarios on the pre-milestone engine (4b91711) and the current one
(docs/MARKET_BENCHMARK.md, "Source selection"). Run with PYTHONPATH pointing at the
gtm-crawler folder of either code tree (the old one extracted from commit 4b91711):
``python benchmarks/market/compare_selection.py <label>``; prints one JSON line per
scenario. Results of 2026-10-21: results/2026-10-21/compare-selection.jsonl."""

import asyncio
import json
import sys
import tempfile
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx

from gtm_crawler.config import CrawlSettings, FetchSettings, RawStoreSettings, Settings
from gtm_crawler.market.engine import run_research
from gtm_crawler.market.models import Budget, ResearchMode, ResearchRequest, SizingRequest
from gtm_crawler.search import SearchResult
from tests.helpers import IDENTITY, response
from tests.pdfs import make_pdf, paragraph

TOTAL = (
    "The European EV charging infrastructure market was valued at USD 13.8 billion in "
    "2024, according to our market outlook."
)
CONTEXT = (
    "Public EV charging infrastructure deployment continued across member states in "
    "2024, driven by the new regulation on alternative fuels."
)


def page(title: str, text: str) -> bytes:
    head = f"<html><head><title>{title}</title></head>"
    return f"{head}<body><main><p>{text}</p></main></body></html>".encode()


def pdf_doc(text: str, pages: int = 1) -> bytes:
    words, lines, line = text.split(), [], ""
    for w in words:
        if len(line) + len(w) > 78:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    return make_pdf([paragraph(56, 760, lines)] * pages)


SCENARIOS = {
    "html estimate vs long contextual PDF": (
        {
            "https://observatory.europa.eu/progress.pdf": pdf_doc(CONTEXT, 12),
            "https://advisory.example.com/europe-ev-charging-market-outlook": page(
                "Europe EV charging infrastructure market outlook 2030", TOTAL
            ),
        },
        [
            (
                "https://observatory.europa.eu/progress.pdf",
                "Charging infrastructure deployment report",
            ),
            (
                "https://advisory.example.com/europe-ev-charging-market-outlook",
                "Europe EV charging infrastructure market outlook 2030",
            ),
        ],
        1,
    ),
    "irrelevant high-ranked pages": (
        {
            "https://stats.gov/water": page(
                "Water utility statistics",
                "The municipal water utility served more households in 2023 than ever before.",
            ),
            "https://data.gov/map": page(
                "Map", "Interactive map of local authority boundaries for the year."
            ),
            "https://advisory.example.com/ev-market": page("Europe EV charging market", TOTAL),
        },
        [
            ("https://stats.gov/water", "Water utility statistics 2023"),
            ("https://data.gov/map", "Local authority map"),
            ("https://advisory.example.com/ev-market", "Europe EV charging infrastructure market"),
        ],
        1,
    ),
    "failed top source, then an alternative": (
        {
            "https://research.example.com/europe-ev-charging-market": 403,
            "https://advisory.example.org/ev-outlook": page("Europe EV charging outlook", TOTAL),
        },
        [
            (
                "https://research.example.com/europe-ev-charging-market",
                "Europe EV charging infrastructure market size report",
            ),
            (
                "https://advisory.example.org/ev-outlook",
                "EV charging infrastructure outlook Europe",
            ),
        ],
        1,
    ),
    "PDFs competing for a slot": (
        {
            "https://y.europa.eu/notes.pdf": pdf_doc(CONTEXT),
            "https://z.europa.eu/annex.pdf": pdf_doc(CONTEXT),
            "https://x.europa.eu/europe-ev-charging-market-size-2024.pdf": pdf_doc(TOTAL),
        },
        [
            ("https://y.europa.eu/notes.pdf", "Meeting notes"),
            ("https://z.europa.eu/annex.pdf", "Annex III"),
            (
                "https://x.europa.eu/europe-ev-charging-market-size-2024.pdf",
                "Europe EV charging infrastructure market size 2024",
            ),
        ],
        2,
    ),
}


class Search:
    def __init__(self, hits):
        self.hits = hits

    async def search(self, query, num_results, include_domains=()):
        return "s", [SearchResult(u, t, "2025-01-01", i) for i, (u, t) in enumerate(self.hits)]


for name, (pages, hits, max_sources) in SCENARIOS.items():

    async def web(request, pages=pages):
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return response(404)
        body = pages.get(url)
        if isinstance(body, int):
            return response(body)
        if body is None:
            return response(404)
        kind = "application/pdf" if body.startswith(b"%PDF") else "text/html; charset=utf-8"
        return response(200, body, **{"content-type": kind})

    tmp = Path(tempfile.mkdtemp())
    s = Settings(
        identity=IDENTITY,
        crawl=replace(CrawlSettings(), min_delay_seconds=0.0),
        fetch=replace(FetchSettings(), max_retries=0),
        rawstore=RawStoreSettings(root=str(tmp)),
    )
    req = ResearchRequest(
        mode=ResearchMode.INDUSTRY,
        subject="EV charging infrastructure",
        geography="Europe",
        budget=Budget(max_queries=2, max_sources=max_sources),
        sizing=SizingRequest(market_definition="EV charging in Europe", year=2024),
    )
    r = asyncio.run(
        run_research(
            req,
            s,
            search=Search(hits),
            transport=httpx.MockTransport(web),
            today=date(2026, 10, 20),
        )
    )
    with_ev = {e.source_id for e in r.evidence}
    tried = [x for x in r.sources if x.status.value != "skipped"]
    print(
        json.dumps(
            {
                "code": sys.argv[1],
                "scenario": name,
                "total": r.sizing.results[0].base.tam if r.sizing and r.sizing.results else None,
                "collected": [
                    x.url.split("/")[2] + ("*" if x.source_id in with_ev else "") for x in tried
                ],
                "useful": sum(x.source_id in with_ev for x in tried),
                "without_evidence": sum(x.source_id not in with_ev for x in tried),
            }
        )
    )
