"""Competitor discovery from the business profile (discovery.py, approved 2026-11-13)."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine

from gtm_crawler import discovery
from gtm_crawler.config import LlmSettings, SearchSettings, Secret
from gtm_crawler.discovery import (
    DIRECTORY_DOMAINS,
    REVIEW_SITES,
    Candidate,
    Hit,
    Query,
    Subject,
    build_queries,
    candidates_from_hits,
    hypotheses,
    listing_candidates,
    merge,
    page_kind,
    review_domain,
    review_view,
    score,
    subject_of,
    title_name,
)
from gtm_crawler.extract.llm import PageReader
from gtm_crawler.jobs import JobStore
from gtm_crawler.worker import run_worker

from .test_crawl import FakeSite, html, settings_for
from .test_onboarding_api import crawled

NOW = datetime(2026, 11, 13, tzinfo=UTC)


def subject(**kw: Any) -> Subject:
    base: dict[str, Any] = dict(
        organization_id=1, name="Acme", domain="acme.example",
        offerings=[{"name": "Acme", "problems": ["track sales deals"], "use_cases": [],
                    "users": []}],
        facts={"industry": ["CRM software"], "target market": ["small sales teams"]},
        unconfirmed=[], exclude={"bigco"},
    )  # fmt: skip
    return Subject(**{**base, **kw})


HYP = {"categories": ["CRM software", "sales pipeline tool"], "problems": ["track sales deals"],
       "segments": ["small sales teams"], "keywords": ["deal tracking"],
       "alternatives": ["spreadsheets", "sales agency"]}  # fmt: skip


def test_queries_cover_every_kind_of_competitor_within_the_budget() -> None:
    queries = build_queries(subject(), HYP, 40, NOW, 540)
    families = [q.family for q in queries]
    assert set(families) == {"direct", "indirect", "substitute", "emerging", "review"}
    # Interleaved: a tight budget still asks one query of each family.
    assert {q.family for q in build_queries(subject(), HYP, 5, NOW, 540)} == set(families)
    texts = [q.text.casefold() for q in queries]
    assert len(texts) == len(set(texts))
    assert '"acme" alternatives' in texts and "track sales deals for small sales teams" in texts
    assert "spreadsheets vs crm software" in texts
    emerging = [q for q in queries if q.family == "emerging"]
    assert emerging and all(q.since == "2025-05-22" for q in emerging)
    assert all(q.include == REVIEW_SITES for q in queries if q.family == "review")


def test_hypotheses_put_the_profile_first_and_keep_short_terms() -> None:
    model = {"categories": [{"value": "CRM software"}, {"value": "x" * 90}],
             "alternatives": [{"value": "spreadsheets"}]}  # fmt: skip
    hyp = hypotheses(subject(), model)
    assert hyp["categories"] == ["CRM software"]  # once, and not the 90-character one
    assert hyp["problems"] == ["track sales deals"] and hyp["alternatives"] == ["spreadsheets"]


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://globex.example/", "home"),
        ("https://globex.example/crm", "home"),
        ("https://globex.example/blog/best-crm-tools", "listing"),
        ("https://www.g2.com/products/globex/reviews", "profile"),
        ("https://www.g2.com/products/acme/competitors/alternatives", "listing"),
        ("https://www.linkedin.com/company/globex", "profile"),
        ("https://toolradar.com/tools/globex", "listing"),
        ("https://apps.shopify.com/globex", "listing"),
    ],
)
def test_page_kind(url: str, kind: str) -> None:
    assert page_kind(url, DIRECTORY_DOMAINS) == kind


def test_title_name() -> None:
    assert title_name("Apollo.io Reviews 2026: Details, Pricing, & Features | G2") == "Apollo.io"
    assert title_name("Apollo.io Pricing, Alternatives & More 2026 | Capterra") == "Apollo.io"
    assert title_name("Cognism | LinkedIn") == "Cognism"
    assert title_name(None) is None


def test_site_name() -> None:
    assert discovery.site_name(["Apollo.io"], "x", "apollo.io") == "Apollo.io"
    assert discovery.site_name([], "The AI GTM System | Apollo", "apollo.io") == "Apollo"
    assert discovery.site_name([], "The AI GTM System for teams", "apollo.io") is None
    # A slogan that starts with the name is not the name.
    assert discovery.site_name([], "Origami gets customers for you.", "origami.chat") is None


def hit(url: str, title: str | None = None, family: str = "direct") -> Hit:
    return Hit(url, title, Query(family, family, f"q {family}"), 0)


def test_candidates_leave_out_the_company_directories_customers_and_rejected() -> None:
    hits = [hit("https://globex.example/", "Globex | CRM for teams"),
            hit("https://acme.example/"),  # the company itself
            hit("https://www.g2.com/products/initech/reviews", "Initech Reviews 2026 | G2"),
            hit("https://bigco.example/", "BigCo"),  # a customer
            hit("https://hooli.example/", "Hooli", "indirect"),
            hit("https://reviews.example/best-crm", "Best CRM")]  # fmt: skip
    found, listings = candidates_from_hits(hits, subject(), DIRECTORY_DOMAINS)
    assert [h.url for h in listings] == ["https://reviews.example/best-crm"]
    found += [Candidate("Initech", "initech.example", "named_by_source"),
              Candidate("Toolradar", "toolradar.com")]  # fmt: skip
    merged = merge(found, subject(), DIRECTORY_DOMAINS, rejected={"hooli.example"})
    by = {c.name: c for c in merged}
    assert set(by) == {"Globex", "Initech"}
    # Known by name from the profile, then by domain from a listing: one candidate.
    assert by["Initech"].domain == "initech.example" and by["Initech"].basis == "named_by_source"


def test_a_listing_names_its_companies_with_the_domain_it_links_to() -> None:
    def cited(value: str) -> dict[str, Any]:
        return {"value": value, "citations": [{"url": "u", "quote": f"1. {value}"}]}

    answer = {"companies": [{"name": cited("Globex"), "named_as": "alternative"},
                            {"name": cited("Initech"), "named_as": "listed"},
                            {"name": cited("Bob"), "named_as": "other"}]}  # fmt: skip
    links = [("https://globex.example/", "Visit"), ("https://www.initech.com/crm", "Initech")]
    page = "Top Acme alternatives: Globex, Initech"
    got = listing_candidates(answer, hit("https://reviews.example/x"), page, links, subject())
    assert [(c.name, c.domain, c.basis) for c in got] == [
        ("Globex", "globex.example", "named_by_source"),
        ("Initech", "initech.com", "inferred_overlap"),
    ]
    # A page that never names the company does not name an alternative to it.
    got = listing_candidates(answer, hit("u"), "Globex, Initech", links, subject())
    assert got[0].basis == "inferred_overlap"


def test_the_judge_is_not_told_the_company_prices() -> None:
    # The model wrote them back as the other company's.
    s = subject(facts={"industry": ["CRM software"], "pricing": ["$99 a seat"]})
    system = discovery.judge_system(s)
    assert "CRM software" in system and "$99 a seat" not in system


def test_named_alternatives_are_products_not_ways_of_working() -> None:
    got = discovery.named_alternatives(
        ["LinkedIn Sales Navigator", "manual lead generation", "Zapier", "In-house SDR team",
         "Google Sheets"])  # fmt: skip
    assert got == ["LinkedIn Sales Navigator", "Zapier", "Google Sheets"]


def test_fair_keeps_each_familys_best() -> None:
    # Ten direct finds outrank the one substitute; it is still among the four kept.
    ranked = [(f"d{i}", {"direct"}) for i in range(10)] + [("s", {"substitute"})]
    got = discovery.fair(ranked, lambda x: x[1], 4, each=2)
    assert [x[0] for x in got] == ["d0", "d1", "s", "d2"]
    assert discovery.fair(ranked, lambda x: x[1], 20, each=2) == [
        *ranked[:2], ranked[10], *ranked[2:10]]  # fmt: skip


def test_a_young_competitor_is_emerging() -> None:
    page = "https://g.example/about"
    c = Candidate("G", "g.example", "named_by_source", [{"url": "https://a.example/x"}])

    def row(year: int, url: str = page, quote: str = "Founded in {} by two engineers") -> str:
        judged = {
            "class": {"value": "direct"},
            "founded": {"value": year, "citations": [{"url": url, "quote": quote.format(year)}]},
            **{k: {"value": "yes"} for k in ("same_buyers", "same_problem", "same_budget")},
        }
        got = discovery._row(subject(), 1, c, judged, [(page, "")], 0.35, new_since=2025)
        assert got is not None
        return str(got["class_"])

    assert row(2025) == "emerging"
    assert row(2019) == "direct"
    # A year a list gave, not its own pages: not evidence it is young.
    assert row(2025, "https://list.example/x") == "direct"
    # A copyright year is not a founding year.
    assert row(2026, quote="Contact © {} G. All rights reserved.") == "direct"


def test_profile_values_cite_only_the_candidates_own_pages() -> None:
    # A rival list's line about another vendor was written down as this one's price.
    own = {"https://g.example/pricing"}
    mine = {"url": "https://g.example/pricing", "quote": "$49/mo"}
    list_line = {"url": "https://list.example/x", "quote": "Apollo from $29/mo"}
    pv = discovery.profile_value
    assert pv("pricing", {"value": "$29/mo", "citations": [list_line]}, own) is None
    assert pv("pricing", [
        {"value": "$29/mo", "citations": [list_line]},
        {"value": "$49/mo", "citations": [list_line, mine]}], own,
    ) == [{"value": "$49/mo", "citations": [mine]}]  # fmt: skip
    # Not a price, or the model saying the page does not say.
    for said in ("Paid for once", "Pricing details are not explicitly mentioned"):
        assert pv("pricing", {"value": said, "citations": [mine]}, own) is None
    assert pv("pricing", {"value": "Free trial", "citations": [mine]}, own) is not None
    assert pv("sells", {"value": "Data enrichment", "citations": [mine]}, own) is not None


def test_score_rewards_overlap_and_independent_sources() -> None:
    def judged(level: str) -> dict[str, Any]:
        return {k: {"value": level} for k in ("same_buyers", "same_problem", "same_budget")}

    one = Candidate("G", "g.example", found_by=[{"url": "https://a.example/x"}])
    named = Candidate("G", "g.example", "named_by_source", [
        {"url": f"https://{s}.example/x"} for s in "abc"])  # fmt: skip
    assert score(judged("yes"), named) == 1.0
    assert score(judged("yes"), one) > score(judged("partly"), one) > score(judged("no"), one)
    assert score({}, one) < 0.35  # nothing judged: below the default bar


def test_subject_from_the_business_profile() -> None:
    def f(key: str, value: Any, user: bool = False) -> dict[str, Any]:
        return {"key": key, "value": value, "verified_by": "user" if user else None}

    bip = {"company_id": 7, "domain": "acme.example", "sections": [
        {"key": "company_profile", "fields": [f("company_name", "Acme", True),
                                              f("market_domain", "CRM software", True)]},
        {"key": "products_services", "items": [
            {"value": "Acme CRM", "verified_by": "user",
             "details": {"problems_solved": ["lost deals"], "target_users": ["sales reps"]}}]},
        {"key": "target_customers", "fields": [
            f("target_industries", [{"name": "SaaS", "why": "..."}])]},
        {"key": "customer_evidence", "fields": [f("customer", ["BigCo"])]},
    ]}  # fmt: skip
    dossier = {"competitors": [{"name": "Globex", "domain": None, "evidence_basis": ["x"]}]}
    s = subject_of(bip, dossier)
    assert s.name == "Acme" and s.offerings[0]["problems"] == ["lost deals"]
    assert s.facts["industry"] == ["CRM software"] and s.facts["target industries"] == ["SaaS"]
    assert s.exclude == {"bigco"} and s.known[0]["name"] == "Globex"
    # Products and industry confirmed; the rest discovery rests on is not.
    assert s.unconfirmed == ["target_segments", "buyer_roles", "product_positioning"]


def test_review_view_applies_the_latest_review() -> None:
    found = [
        {"name": "Globex", "domain": "globex.example", "class": "direct", "score": 0.9},
        {"name": "Hooli", "domain": "hooli.example", "class": "direct", "score": 0.5},
    ]
    review: dict[str, dict[str, Any]] = {
              "hooli.example": {"status": "edited", "value": {"class": "substitute"},
                                "verified_at": "t"},
              "globex.example": {"status": "rejected", "value": None, "verified_at": "t"},
              "vandelay.example": {"status": "edited", "verified_at": "t", "value": {
                  "name": "Vandelay", "class": "indirect", "added": True}}}  # fmt: skip
    view = review_view(found, review)
    c = view["competitors"]
    assert [i["review"] for i in c["direct"]] == ["rejected"]
    assert c["substitute"][0]["name"] == "Hooli" and c["substitute"][0]["review"] == "approved"
    assert c["indirect"][0]["review"] == "user_added"
    assert view["counts"] == {"suggested": 0, "approved": 1, "rejected": 1, "user_added": 1}


@pytest.mark.parametrize(
    ("raw", "domain"),
    [("globex.example", "globex.example"), ("https://www.Globex.com/x", "globex.com")],
)
def test_review_domain(raw: str, domain: str) -> None:
    assert review_domain(raw) == domain


def test_review_domain_refuses_what_is_not_one() -> None:
    with pytest.raises(ValueError, match="not a domain"):
        review_domain("Globex Inc")


# -- the job, end to end -------------------------------------------------------------

LISTING = "https://reviews.example/acme-alternatives"
ROUTES: dict[str, Any] = {
    # The fake site gives no name: the company is named after its domain.
    LISTING: ("text/html", html("Top Disc-Acme alternatives", (
        "<h1>Top Disc-Acme alternatives</h1><p>1. Globex is the best Disc-Acme alternative.</p>"
        '<p>2. Initech, a CRM for teams.</p><a href="https://globex.example/">Globex</a>'
        '<a href="https://initech.example/">Initech</a>'))),
    "https://globex.example/": ("text/html", html("Globex | CRM for small sales teams", (
        "<p>Globex is a CRM that small sales teams use to track deals.</p>"
        '<a href="https://globex.example/pricing">Pricing</a>'))),
    "https://globex.example/pricing": ("text/html", html("Pricing", "<p>Globex costs $20 a "
                                                                   "month per seat.</p>")),
    "https://initech.example/": ("text/html", html("Initech", "<p>Initech helps sales teams "
                                                             "forecast deals.</p>")),
    "https://hooli.example/": ("text/html", html("Hooli", "<p>Hooli sells search ads.</p>")),
    "https://vandelay.example/": ("text/html", html("Vandelay", "<p>Vandelay imports "
                                                               "latex.</p>")),
}  # fmt: skip


def exa(request: httpx.Request) -> httpx.Response:
    query = json.loads(request.content)["query"]
    if "alternatives" in query and "includeDomains" not in json.loads(request.content):
        urls = [(LISTING, "Top Acme alternatives"), ("https://hooli.example/", "Hooli")]
    else:
        urls = [("https://toolradar.com/tools/acme", "Acme on Toolradar")]
    return httpx.Response(200, json={"results": [{"url": u, "title": t} for u, t in urls]})


def model(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    system, user = body["messages"][0]["content"], body["messages"][1]["content"]
    first = next(line[1:].split("]")[0] for line in user.splitlines() if line.startswith("[e"))
    ev = [first]
    # The first line on the candidate's own pages (the judge keeps no other for "sells").
    page = next((line[1:].split("]")[0] for line in user.splitlines()
                 if line.startswith("[e") and "] page " in line), first)  # fmt: skip
    if system == discovery.HYP_SYSTEM:
        answer: dict[str, Any] = {"categories": [{"value": "CRM software", "evidence": ev}]}
    elif system == discovery.LIST_SYSTEM:
        answer = {"companies": [{"name": {"value": "Globex", "evidence": ev},
                                 "named_as": "alternative"},
                                {"name": {"value": "Initech", "evidence": ev},
                                 "named_as": "listed"}]}  # fmt: skip
    else:
        level, cls = (
            ("yes", "direct")
            if "Globex is a CRM" in user
            else ("partly", "indirect")
            if "Initech" in user
            else ("yes", "direct")
            if "Vandelay" in user
            else ("no", "not_competitor")
        )
        answer = {"reasoning": {"value": "It sells to the same teams. [" + first + "]"},
                  "sells": [{"value": "CRM", "evidence": [page]}],
                  **{k: {"value": level, "evidence": ev}
                     for k in ("same_buyers", "same_problem", "same_budget")},
                  "class": {"value": cls, "evidence": ev}}  # fmt: skip
    content = json.dumps(answer)
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.mark.db
def test_discovery_finds_classifies_and_takes_the_review(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = settings_for(tmp_path)
    api, company = crawled(engine, base, "disc-acme")
    settings = replace(
        base,
        llm=LlmSettings(base_url="http://localhost:11434/v1", model="qwen3:8b"),
        search=SearchSettings(api_key=Secret("k"), retry_backoff_seconds=0.001),
    )
    monkeypatch.setattr(discovery, "PageReader",
                        lambda s: PageReader(s, httpx.MockTransport(model)))  # fmt: skip

    def run() -> dict[str, Any]:
        queued = api.post(f"/v1/companies/{company}/competitors/discover")
        assert queued.status_code == 202, queued.text
        assert queued.json()["kind"] == "discovery"
        site = httpx.MockTransport(FakeSite(ROUTES))
        assert asyncio.run(run_worker(settings, engine, once=True, transport=site,
                                      search_transport=httpx.MockTransport(exa))) == 1  # fmt: skip
        assert api.get(f"/v1/crawls/{queued.json()['crawl_id']}").json()["status"] == "COMPLETED"
        # Every search and page is kept as a job event (the activity view reads them).
        events = JobStore(engine).events(queued.json()["crawl_id"])
        fetched = {e.detail["url"]: e.to_state for e in events if e.event == "discovery_fetch"}
        assert fetched[LISTING] == "fetched"
        assert any(e.event == "discovery_search" and e.reason == "direct" for e in events)
        got = api.get(f"/v1/companies/{company}/competitors")
        assert got.status_code == 200
        return dict(got.json())

    view = run()
    c = view["competitors"]
    assert [(i["name"], i["basis"]) for i in c["direct"]] == [("Globex", "named_by_source")]
    assert [i["name"] for i in c["indirect"]] == ["Initech"]
    assert c["direct"][0]["profile"]["sells"][0]["value"] == "CRM"
    assert c["direct"][0]["profile"]["pages_read"] == [
        "https://globex.example/",
        "https://globex.example/pricing",
    ]
    everyone = [i["domain"] for cls in c.values() for i in cls]
    assert "hooli.example" not in everyone and "toolradar.com" not in everyone  # not a rival
    assert "products_services" in view["unconfirmed"]

    review = api.put(f"/v1/companies/{company}/competitors", json={"items": [
        {"domain": "globex.example", "action": "reject"},
        {"domain": "https://vandelay.example/", "action": "add", "name": "Vandelay",
         "class": "substitute"},
        {"domain": "initech.example", "action": "reclassify", "class": "substitute"}]})  # fmt: skip
    assert review.status_code == 200, review.text
    assert review.json()["counts"] == {"suggested": 0, "approved": 1, "rejected": 1,
                                       "user_added": 1}  # fmt: skip
    bad = api.put(f"/v1/companies/{company}/competitors",
                  json={"items": [{"domain": "x.example", "action": "add"}]})  # fmt: skip
    assert bad.status_code == 422

    # Again: what the user rejected stays out, what they added is in and read.
    c = run()["competitors"]
    everyone = [i["domain"] for cls in c.values() for i in cls]
    assert "globex.example" not in everyone
    vandelay = next(i for cls in c.values() for i in cls if i["domain"] == "vandelay.example")
    assert vandelay["basis"] == "user_added" and vandelay["review"] == "user_added"
    assert vandelay["class"] == "substitute"  # the user's class over the model's

    # Search down (no network): the job fails and the last list stays.
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    queued = api.post(f"/v1/companies/{company}/competitors/discover").json()
    asyncio.run(run_worker(settings, engine, once=True, transport=httpx.MockTransport(offline),
                           search_transport=httpx.MockTransport(offline)))  # fmt: skip
    assert api.get(f"/v1/crawls/{queued['crawl_id']}").json()["status"] == "FAILED"
    after = api.get(f"/v1/companies/{company}/competitors").json()
    assert after["competitors"] == c and after["job"]["status"] == "failed"
