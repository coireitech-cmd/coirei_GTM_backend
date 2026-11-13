"""Competitor discovery from the company's confirmed business profile (approved 2026-11-13).

Not companies in the same industry: companies that compete for the same customers and
the same spending, directly, indirectly or as an alternative way to solve the problem.
A ``discovery`` job (``pipeline._run_job``):

1. **Hypotheses.** The business profile (``business_profile.build_business_profile``;
   the user's answers win) gives the offerings, the problems they solve, the buyers and
   the positioning. A model call (``HYP_SCHEMA``) turns them into search hypotheses:
   the category buyers search for, their problems, segments, keywords and the
   alternatives they use instead (an agency, a spreadsheet, an in-house team).
2. **Search** (``build_queries``), in parallel, one family per kind of competitor:
   direct (the company's name and category), indirect (problem for segment), substitute
   (alternative vs category), emerging (recent pages only) and review sites and
   marketplaces (``REVIEW_SITES``). Only what search returns; links are never followed.
3. **Candidates.** A company's own home page is a candidate; a review-site or LinkedIn
   profile names one; a list, comparison or review page is read by the model
   (``LIST_SCHEMA``) for the companies it presents, each name required to appear on the
   page. Directories, media and the company itself are never candidates
   (``DIRECTORY_DOMAINS``), nor its customers and investors; a domain the user rejected
   stays out, one the user added is always in.
4. **Profile and classify.** The strongest candidates (``_prerank``) are read from a few
   of their own pages (home, pricing, about, product), fetched like every external page
   (``market.collect.Collector``: robots.txt, network guard, throttle, raw store). The
   model (``JUDGE_SCHEMA``) states what each sells, to whom, at what price, whether it
   serves the same buyers, problem and budget, and its class, every value citing the
   page lines it rests on (``profile.clean`` drops the rest).
5. **Rank** (``_score``) and store (``CompetitorCandidate``). The user reviews the list
   (``review_view``, ``save_review``): approve, reject, add or reclassify.

Phase 11's explicit-evidence rule (a competitor only when a page says so) still holds
for the crawl's own competitor discovery; here it is relaxed, as approved: each result
says whether a source named it (``named_by_source``) or the model inferred the overlap
(``inferred_overlap``), and the user's approval is the final gate.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .business_profile import build_business_profile
from .config import Settings
from .db.models import (
    COMPETITOR_CLASSES,
    CompanyVerification,
    CompetitorCandidate,
    CrawlEvent,
    CrawlJob,
    LlmPageResult,
    Organization,
)
from .domain import JobState
from .dossier import build_dossier
from .extract.llm import PageReader
from .extract.product import slugify
from .identity import external_id
from .jobs import JobStore
from .market.collect import Collector
from .market.models import Budget, RetrievalStatus
from .profile import _G, _I, _M, _P, _R, _S, _call, _enum, _part_system, _stored, _tag
from .search import PageContents, SearchError, competitor_queries, make_search
from .urls import InvalidUrl, registrable_domain, url_host

log = logging.getLogger(__name__)

HYP_VERSION = "discovery-hyp-2"
LIST_VERSION = "discovery-list-1"
JUDGE_VERSION = "discovery-judge-3"
STAGES = ("hypotheses", "search", "extract", "profile", "classify", "done")
# The user's review: company_verification rows of this section and field, keyed by domain.
REVIEW_SECTION, REVIEW_FIELD = "competitors", "discovered_competitor"

# Searched with includeDomains: where software is listed, reviewed and compared, and the
# marketplaces of the big platforms. Their pages name candidates; they are never one.
REVIEW_SITES = ("g2.com", "capterra.com", "getapp.com", "trustradius.com", "producthunt.com",
                "alternativeto.net", "linkedin.com", "apps.shopify.com",
                "appexchange.salesforce.com", "marketplace.atlassian.com",
                "ecosystem.hubspot.com")  # fmt: skip
# Sites that list, review, report on or host companies: never a competitor themselves
# (registrable domains; a marketplace on a vendor's domain, like apps.shopify.com, is not
# here, since its vendor may well be a competitor).
DIRECTORY_DOMAINS = ("g2.com", "capterra.com", "getapp.com", "trustradius.com",
                     "producthunt.com", "alternativeto.net", "softwareadvice.com",
                     "toolradar.com", "cbinsights.com", "saasworthy.com", "sourceforge.net",
                     "slashdot.org", "softwaresuggest.com", "selecthub.com", "gartner.com",
                     "forrester.com", "featuredcustomers.com", "stackshare.io",
                     "similarweb.com", "builtwith.com", "saashub.com", "crozdesk.com",
                     "wikipedia.org", "medium.com", "substack.com", "reddit.com", "quora.com",
                     "forbes.com", "techcrunch.com", "businessinsider.com", "venturebeat.com",
                     "zapier.com", "github.com", "news.ycombinator.com", "ycombinator.com",
                     "tracxn.com", "owler.com", "craft.co", "comparably.com",
                     "trustpilot.com", "bloomberg.com", "reuters.com",
                     "wordpress.com", "blogspot.com", "google.com", "apple.com")  # fmt: skip
# Sites never read as a company's own (their pages list or profile others), whose own
# product is still one a buyer may use instead (Sales Navigator, Zaps).
ALSO_PRODUCTS = ("linkedin.com", "zapier.com")
# Page paths of a company's own site worth reading for what it sells and to whom.
_KEY_PATH = re.compile(r"/(pricing|plans|about|company|product|products|platform|solutions|"
                       r"features|customers|use-cases)(/|$)", re.I)  # fmt: skip
# The parts of a review site's title after the product's name.
_TITLE_TAIL = re.compile(r"\s+(reviews?|pricing|alternatives|competitors|features|software|"
                         r"product information|ratings?)\b.*$|\s+\d{4}$", re.I)  # fmt: skip
_TITLE_SPLIT = re.compile(r"\s+[|\-–—:]\s+|:\s+|\s+\|\s*")


# -- what the company is ------------------------------------------------------------


@dataclass
class Subject:
    organization_id: int
    name: str
    domain: str
    offerings: list[dict[str, Any]]  # name, problems, use_cases, users
    facts: dict[str, list[str]]  # label -> values (industry, buyers, positioning...)
    unconfirmed: list[str]
    exclude: set[str] = field(default_factory=set)  # casefolded: customers, investors
    known: list[dict[str, Any]] = field(default_factory=list)  # competitors the crawl named


# BIP fields that describe who the company competes with, and their labels in prompts.
_FACTS = {"market_domain": "industry", "target_segments": "target market",
          "business_types": "business types served", "company_sizes": "company sizes served",
          "buyer_roles": "buyers", "target_industries": "target industries",
          "main_use_case": "use cases", "customer_type": "customer type",
          "product_positioning": "value proposition",
          "positioning_statement": "positioning", "differentiators": "differentiators",
          "pricing_summary": "pricing", "revenue_model": "pricing model"}  # fmt: skip
# What discovery rests on: shown as unconfirmed until the user confirmed it.
KEY_FIELDS = ("products_services", "market_domain", "target_segments", "buyer_roles",
              "product_positioning")  # fmt: skip


def texts(value: Any) -> list[str]:
    """The text in a profile value: a string, a list of them, or items of named parts."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        for key in ("name", "segment", "value", "statement"):
            if isinstance(value.get(key), str):
                return texts(value[key])
        return [t for v in value.values() if isinstance(v, str) for t in texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in texts(v)]
    return [str(value)]


def subject_of(bip: dict[str, Any], dossier: dict[str, Any]) -> Subject:
    """What discovery needs from the business profile and the dossier; pure."""
    fields = {f["key"]: f for s in bip["sections"] for f in s.get("fields", [])}
    items: list[dict[str, Any]] = next(
        (s["items"] for s in bip["sections"] if s["key"] == "products_services"), []
    )
    domain = bip.get("domain") or ""
    name = next(iter(texts(fields.get("company_name", {}).get("value"))), None) or \
        domain.split(".")[0].title()  # fmt: skip
    offerings = [
        {"name": n, **{k: texts((i.get("details") or {}).get(src))
                       for k, src in (("problems", "problems_solved"), ("use_cases", "use_cases"),
                                      ("users", "target_users"))}}
        for i in items if (n := next(iter(texts(i.get("value"))), None))
    ]  # fmt: skip
    facts = {label: v for key, label in _FACTS.items()
             if (v := texts(fields.get(key, {}).get("value")))}  # fmt: skip

    def confirmed(f: dict[str, Any] | None) -> bool:
        return f is not None and f.get("verified_by") == "user"

    unconfirmed = [k for k in KEY_FIELDS if k != "products_services"
                   and not confirmed(fields.get(k))]  # fmt: skip
    if not items or not all(confirmed(i) for i in items):
        unconfirmed.insert(0, "products_services")
    exclude = {t.casefold() for k in ("customer", "investor") for t in
               texts(fields.get(k, {}).get("value"))}  # fmt: skip
    known = [{"name": c["name"], "domain": c.get("domain"), "basis": c.get("evidence_basis")}
             for c in dossier.get("competitors", []) if c.get("name")]  # fmt: skip
    return Subject(bip["company_id"], name, domain, offerings, facts, unconfirmed, exclude,
                   known)  # fmt: skip


def subject_lines(subject: Subject) -> tuple[str, dict[str, dict[str, Any]]]:
    """The subject as numbered evidence lines for the hypotheses call, and their refs."""
    lines = [f"company: {subject.name} ({subject.domain})"]
    for o in subject.offerings:
        lines.append(f"offering: {o['name']}")
        lines += [f"{o['name']} {label}: {'; '.join(o[k])}" for k, label in
                  (("problems", "problems solved"), ("use_cases", "use cases"),
                   ("users", "for")) if o[k]]  # fmt: skip
    lines += [f"{label}: {'; '.join(values)}" for label, values in subject.facts.items()]
    lines += [f"named competitor: {c['name']}" for c in subject.known]
    return _numbered(lines)


def _numbered(lines: Sequence[str], refs: Sequence[dict[str, Any]] | None = None,
              max_chars: int = 14000) -> tuple[str, dict[str, dict[str, Any]]]:  # fmt: skip
    out: list[str] = []
    ids: dict[str, dict[str, Any]] = {}
    size = 0
    for n, text in enumerate(lines):
        line = f"[e{n + 1}] " + " ".join(text.split())
        if size + len(line) > max_chars:
            break
        out.append(line)
        ids[f"e{n + 1}"] = {**(refs[n] if refs else {}), "text": text}
        size += len(line) + 1
    return "\n".join(out), ids


# -- hypotheses and queries -----------------------------------------------------------

HYP_SCHEMA: dict[str, Any] = {
    "reasoning": _R,
    "categories": [_S], "problems": [_S], "segments": [_G], "keywords": [_S],
    "alternatives": [_S],
}  # fmt: skip
HYP_SYSTEM = _part_system(
    "You plan the search for one company's competitors: the companies its buyers could "
    "spend the same budget on instead.",
    HYP_SCHEMA,
    """Write "reasoning" first: two or three sentences, each ending with ids, on who buys
from the company and what they would otherwise buy. Then, each at most five, in the words
a buyer types into a search engine:
- categories: what buyers call this kind of product or service ("data enrichment
  software", "wedding photographer"), not the company's slogan.
- problems: what the buyer wants done, starting with a verb ("find verified emails for
  leads", "plan a wedding menu").
- segments: the buyers, specifically ("B2B sales teams", "real estate developers").
- keywords: other search terms for the same need.
- alternatives: other ways buyers get the job done that are not this kind of product: a
  type of agency or freelancer, an in-house team, spreadsheets, a different kind of tool;
  by the name of a well-known product where buyers use one ("Excel", "Upwork").""",
)


@dataclass(frozen=True)
class Query:
    family: str  # direct, indirect, substitute, emerging, review
    template: str
    text: str
    include: tuple[str, ...] = ()
    since: str | None = None  # an ISO date: only pages published after it


def hypotheses(subject: Subject, model: dict[str, Any] | None) -> dict[str, list[str]]:
    """The model's hypotheses (``HYP_SCHEMA``, cleaned) with the profile's own values first."""
    model = model or {}
    got = {k: texts([x.get("value") for x in model.get(k) or []]) for k in HYP_SCHEMA
           if k != "reasoning"}  # fmt: skip
    seeds = {"categories": subject.facts.get("industry", []),
             "problems": [p for o in subject.offerings for p in o["problems"]],
             "segments": [*subject.facts.get("target market", []),
                          *subject.facts.get("buyers", [])],
             "keywords": [], "alternatives": []}  # fmt: skip
    # The model's, in a buyer's words, before the profile's (often a site's headings).
    return {k: list(dict.fromkeys(v for v in [*got[k], *seeds[k]] if len(v) <= 80))[:6]
            for k in seeds}  # fmt: skip


def build_queries(subject: Subject, hyp: dict[str, list[str]], max_queries: int,
                  today: datetime, emerging_days: int) -> list[Query]:  # fmt: skip
    """The searches, one family per kind of competitor, interleaved so a budget cut keeps
    every family; each query once."""
    cats, probs, segs = hyp["categories"], hyp["problems"], hyp["segments"]
    alts, keys = hyp["alternatives"], hyp["keywords"]
    products = [o["name"] for o in subject.offerings if o["name"] != subject.name][:2]
    since = (today - timedelta(days=emerging_days)).date().isoformat()
    cat = cats[0] if cats else (keys[0] if keys else None)
    families: list[list[Query]] = [
        [Query("direct", f"direct:{t}", q)
         for t, q in competitor_queries([subject.name], products, cats[:2])],
        [Query("indirect", "indirect:{problem} for {segment}", f"{p} for {s}")
         for p in probs[:3] for s in segs[:2]]
        + [Query("indirect", "indirect:tools to {problem}", f"tools to {p}") for p in probs[:3]],
        [Query("substitute", "substitute:{alternative} vs {category}", f"{a} vs {cat}")
         for a in alts[:4] if cat]
        + [Query("substitute", "substitute:{alternative} for {problem}", f"{a} for {p}")
           for a in alts[:3] for p in probs[:1]],
        [Query("emerging", "emerging:new {category} startups", f"new {c} startups", since=since)
         for c in [*cats[:2], *keys[:1]]]
        + [Query("emerging", "emerging:AI {problem}", f"AI tool to {p}", since=since)
           for p in probs[:2]],
        [Query("review", "review:{category}", c, REVIEW_SITES) for c in [*cats[:3], *keys[:2]]]
        + [Query("review", "review:{name} alternatives", f"{subject.name} alternatives",
                 REVIEW_SITES)],
    ]  # fmt: skip
    out: list[Query] = []
    seen: set[str] = set()
    for row in range(max(map(len, families), default=0)):
        for fam in families:
            if row < len(fam) and (q := fam[row]).text.casefold() not in seen:
                seen.add(q.text.casefold())
                out.append(q)
    return out[:max_queries]


# -- candidates -----------------------------------------------------------------------


@dataclass
class Hit:
    url: str
    title: str | None
    query: Query
    position: int


@dataclass
class Candidate:
    name: str
    domain: str | None
    basis: str = "inferred_overlap"
    found_by: list[dict[str, Any]] = field(default_factory=list)
    linkedin: str | None = None  # a LinkedIn company page that names it (its website)

    @property
    def sources(self) -> set[str]:
        return {_domain(f["url"]) or "" for f in self.found_by if f.get("url")} - {""}

    @property
    def families(self) -> set[str]:
        return {f["family"] for f in self.found_by}

    def how(self, how: str) -> bool:
        return any(f.get("how") == how for f in self.found_by)


def named_alternatives(alternatives: Iterable[str]) -> list[str]:
    """The alternatives that name a product ("LinkedIn Sales Navigator", "Zapier"), not a
    way of working ("manual lead generation", "In-house SDR team")."""
    return [a for a in alternatives
            if len(a.split()) <= 4 and all(w[0].isupper() for w in a.split())]  # fmt: skip


def _domain(url: str) -> str | None:
    try:
        return registrable_domain(url_host(url))
    except InvalidUrl:
        return None


def _listed(domain: str | None, domains: Iterable[str]) -> bool:
    return domain is not None and any(domain == d or domain.endswith("." + d) for d in domains)


def title_name(title: str | None) -> str | None:
    """The product's name in a profile page's title ("Apollo.io Reviews 2026 | G2")."""
    if not title:
        return None
    name = _TITLE_TAIL.sub("", _TITLE_SPLIT.split(title.strip())[0]).strip(" -|:,")
    return name if 1 < len(name) <= 60 else None


_LISTING_PATH = re.compile(r"blog|best|top-|vs|versus|alternative|competitor|compar|"
                           r"list|guide|article|news", re.I)  # fmt: skip


def page_kind(url: str, blocked: Sequence[str]) -> Literal["home", "profile", "listing"]:
    """A company's own page (its home page, or a landing page one level down), a profile
    of one on a review site or LinkedIn, or a page that lists, compares or reviews several."""
    path = urlsplit(url).path.strip("/")
    if _LISTING_PATH.search(path) and not path.startswith("company/"):
        return "listing"  # g2.com/products/clay/competitors/alternatives is a list
    profile = external_id(url)
    if profile and profile.source_type in ("g2", "capterra", "producthunt", "linkedin"):
        return "profile"
    if _listed(_domain(url), blocked) or _listed(url_host(url), REVIEW_SITES):
        return "listing"
    return "home" if path.count("/") == 0 else "listing"


def candidates_from_hits(hits: Iterable[Hit], subject: Subject,
                         blocked: Sequence[str]) -> tuple[list[Candidate], list[Hit]]:  # fmt: skip
    """Candidates the results name by themselves (home pages, profiles), and the listing
    pages left to read; pure."""
    found: list[Candidate] = []
    listings: list[Hit] = []
    for h in hits:
        domain = _domain(h.url)
        if domain is None or domain == subject.domain:
            continue
        kind = page_kind(h.url, blocked)
        how = {"family": h.query.family, "query": h.query.text, "url": h.url,
               "title": h.title, "how": kind}  # fmt: skip
        if kind == "listing":
            listings.append(h)
        elif kind == "home":
            # Named after its domain: a page's title is mostly a slogan ("The AI GTM
            # System for..."); its home page names it once read (_own_pages).
            found.append(Candidate(domain.split(".")[0].title(), domain, found_by=[how]))
        elif kind == "profile" and (name := title_name(h.title)):
            c = Candidate(name, None, found_by=[how])
            if (p := external_id(h.url)) and p.source_type == "linkedin":
                c.linkedin = h.url
            found.append(c)
    return found, listings


def _key(name: str) -> str:
    return slugify(name).replace("-", "")


def merge(cands: Iterable[Candidate], subject: Subject, blocked: Sequence[str],
          rejected: set[str]) -> list[Candidate]:  # fmt: skip
    """One candidate per domain (or, until its domain is known, per name), without the
    company itself, directories, its customers and investors, and what the user rejected."""
    own = {_key(subject.name), _key(subject.domain.split(".")[0])}
    by: dict[str, Candidate] = {}
    for c in cands:
        if (_key(c.name) in own or c.name.casefold() in subject.exclude
                or c.domain == subject.domain or _listed(c.domain, blocked)
                or c.domain in rejected):  # fmt: skip
            continue
        key = c.domain or f"name:{_key(c.name)}"
        named = by.get(f"name:{_key(c.name)}") if c.domain else None
        if named is not None:  # known by name first, now by its domain too
            del by[f"name:{_key(c.name)}"]
            named.domain = c.domain
            by[key] = named
        have = by.get(key) or next((x for x in by.values() if _key(x.name) == _key(c.name)), None)
        if have is None:
            by[key] = c
            continue
        have.found_by += [f for f in c.found_by if f not in have.found_by]
        have.linkedin = have.linkedin or c.linkedin
        if c.basis == "named_by_source":
            have.basis = c.basis
        if len(c.name) < len(have.name) and c.domain:  # "Apollo.io" over "Apollo.io Sales"
            have.name = c.name
    return list(by.values())


_BASIS_WEIGHT = {"named_by_source": 1.0, "user_added": 1.0, "inferred_overlap": 0.5}


def _prerank(c: Candidate) -> tuple[float, int]:
    """Before any of its pages is read: independent sources that named it, by basis."""
    return (len(c.sources) * _BASIS_WEIGHT[c.basis] + 0.5 * len(c.families), -len(c.name))


T = TypeVar("T")


def fair(ranked: Sequence[T], families: Callable[[T], set[str]], n: int, each: int) -> list[T]:
    """The best ``n`` of ``ranked``, with each search family's best ``each`` among them:
    the many rival lists the direct searches find would otherwise crowd out what the
    substitute and emerging searches found."""
    chosen: list[T] = []
    per: Counter[str] = Counter()
    for x in ranked:
        if len(chosen) < n and any(per[f] < each for f in families(x)):
            chosen.append(x)
            per.update(families(x))
    taken = {id(x) for x in chosen}
    return chosen + [x for x in ranked if id(x) not in taken][: n - len(chosen)]


LIST_SCHEMA: dict[str, Any] = {
    "companies": [{"name": _M, "named_as": _tag("alternative", "listed", "other")}],
}
LIST_SYSTEM = _part_system(
    "You read one web page (a list of tools, a comparison, reviews) for the companies and "
    "products it presents as something a buyer could choose.",
    LIST_SCHEMA,
    """List each company or product the page presents as an option, at most 25, by its name
exactly as the page writes it. named_as: "alternative" when the page presents it as an
alternative to, or a competitor of, the company named before the evidence; "listed" when it
is one of several options for the same need; "other" otherwise. Leave out the page's
publisher and authors, and companies it names as customers, investors, integrations or
sources.""",
)


def listing_candidates(
    answer: dict[str, Any] | None,
    hit: Hit,
    page_text: str,
    links: Sequence[tuple[str, str]],
    subject: Subject,
) -> list[Candidate]:
    """The companies a listing page presents (``LIST_SCHEMA``, cleaned), each with the
    domain the page links to for it: the link whose text is its name, or whose domain's
    first label is its name's slug."""
    out = []
    names_subject = subject.name.casefold() in page_text.casefold()
    for item in (answer or {}).get("companies") or []:
        name = (item.get("name") or {}).get("value")
        if not isinstance(name, str) or item.get("named_as") == "other":
            continue
        # Not the publisher's own pages (its review of the product), nor the company.
        domain = link_domain(name, links, {subject.domain, _domain(hit.url) or ""})
        quote = next((c["quote"] for c in item["name"].get("citations", []) if "quote" in c), "")
        basis = ("named_by_source" if item.get("named_as") == "alternative" and names_subject
                 else "inferred_overlap")  # fmt: skip
        out.append(Candidate(name, domain, basis, [{
            "family": hit.query.family, "query": hit.query.text, "url": hit.url,
            "title": hit.title, "how": "listing", "quote": quote[:300]}]))  # fmt: skip
    return out


def link_domain(name: str, links: Sequence[tuple[str, str]], skip: set[str]) -> str | None:
    """The one domain a page links to for ``name`` (``links``: url, anchor text), other
    than those in ``skip``."""
    slug, named = _key(name), name.casefold()
    found = {d for url, text in links
             if (d := _domain(url)) and d not in skip
             and (text.strip().casefold() == named or d.split(".")[0] == slug)}  # fmt: skip
    return found.pop() if len(found) == 1 else None


# -- classification -------------------------------------------------------------------

_LEVEL = ("yes", "partly", "no")
JUDGE_SCHEMA: dict[str, Any] = {
    "reasoning": _R,
    # Not "offerings": profile.clean wants a name in each of those (profile._REQUIRED).
    "sells": [_S], "positioning": _S, "pricing": _P, "target_customers": [_S],
    "same_buyers": _enum(*_LEVEL), "same_problem": _enum(*_LEVEL),
    "same_budget": _enum(*_LEVEL), "founded": _I,
    "class": _enum(*COMPETITOR_CLASSES, "not_competitor"),
}  # fmt: skip


_OWN_PRICES = ("pricing", "pricing model")


def judge_system(subject: Subject) -> str:
    about = "; ".join(
        [
            *(o["name"] for o in subject.offerings),
            # Not its prices: the model wrote them back as the other company's.
            *(f"{k}: {', '.join(v)}" for k, v in subject.facts.items() if k not in _OWN_PRICES),
        ]
    )[:1500]
    return _part_system(
        f"You judge whether one company competes with {subject.name} ({subject.domain}) "
        f"for the same customers and the same spending. What {subject.name} is: {about}.",
        JUDGE_SCHEMA,
        f"""The evidence is the other company's own pages and the pages that named it.
Write "reasoning" first: two to four sentences, each ending with ids, on what it sells,
to whom, and how that meets {subject.name}'s buyers. Then:
- sells, positioning, pricing, target_customers: what this company's own pages (the lines
  that start with "page") say of it, cited from those lines; never what a list says of
  another company, never {subject.name}'s. Leave out what its pages do not say.
- same_buyers, same_problem, same_budget: yes, partly or no, against {subject.name}.
- founded: the year its own pages say it was founded or launched (not a copyright year);
  leave it out when they do not say.
- class: "direct" if it sells the same kind of product to the same buyers; "indirect" if
  a different kind of product solves the same problem for them; "substitute" if it is
  another way to get the job done (a service, an agency, a general tool used instead);
  "emerging" if it is young or early (new, beta, waitlist, recently founded) and goes
  after the same buyers; "not_competitor" if it is a directory, media, a customer, a
  partner whose product {subject.name} complements, or it serves other buyers.""",
    )


def score(judged: dict[str, Any], c: Candidate) -> float:
    """0..1: how much the candidate overlaps the company (buyers, problem, budget, as the
    model judged them), how it was found and by how many independent sources."""
    level = {"yes": 1.0, "partly": 0.5, "no": 0.0}

    def of(key: str) -> float:
        return level.get(str((judged.get(key) or {}).get("value")), 0.0)

    s = (0.3 * of("same_buyers") + 0.3 * of("same_problem") + 0.15 * of("same_budget")
         + 0.15 * _BASIS_WEIGHT[c.basis] + 0.1 * min(1.0, len(c.sources) / 3))  # fmt: skip
    return round(s, 3)


def judge_text(c: Candidate, pages: Sequence[tuple[str, str]],
               compact: bool = False) -> tuple[str, dict[str, Any]]:  # fmt: skip
    """The candidate's evidence lines: how it was found, then its own pages; ``compact``:
    fewer of each (a second try when the model gave no usable answer)."""
    lines: list[str] = []
    refs: list[dict[str, Any]] = []
    for f in [f for f in c.found_by if f.get("url")][: 2 if compact else 6]:
        # The search says what it was looked for as ("new data enrichment startups").
        searched = f" (search: {f['query']})" if f.get("query") else ""
        text = f"found on {f['url']}{searched}" + (
            f": {f['quote']}" if f.get("quote") else f" ({f['title']})" if f.get("title") else ""
        )
        lines.append(text)
        refs.append({"url": f["url"], "quote": f.get("quote") or f.get("title") or ""})
    for url, text in pages:
        for chunk in _chunks(text, 600)[: 3 if compact else 6]:
            lines.append(f"page {url}: {chunk}")
            refs.append({"url": url, "quote": chunk})
    return _numbered(lines, refs, 9000)


def _chunks(text: str, size: int) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > size and cur:
            out.append(cur)
            cur = ""
        cur = f"{cur} {w}".strip()
    return [*out, cur] if cur else out


# -- the job --------------------------------------------------------------------------


def _cached_call(engine: Engine, settings: Settings, reader: PageReader, version: str,
                 system: str, schema: dict[str, Any], text: str,
                 refs: dict[str, Any]) -> dict[str, Any] | None:  # fmt: skip
    """The cleaned answer for this exact prompt and evidence, stored once; None if the
    model gave no usable one (not stored, so a later run tries again)."""
    model = settings.llm.model
    digest = hashlib.sha256(f"{system}\n{text}".encode()).hexdigest()
    if (stored := _stored(engine, model, version, digest)) is not None:
        return stored.get("profile")
    # Many short calls (a page, a candidate): no thinking budget and a small answer; the
    # schemas ask for written reasoning first instead.
    llm = replace(settings.llm, profile_reasoning_effort="none", profile_max_tokens=3000)
    result = _call(reader, llm, system, schema, text, refs)
    if "error" in result:
        log.warning("discovery.llm_failed", extra={"prompt": version, "error": result["error"],
                                                   **result.get("dropped", {})})  # fmt: skip
        return None
    with engine.begin() as conn:
        conn.execute(pg_insert(LlmPageResult).values(
            text_sha256=digest, model=model, prompt_version=version, result=result,
        ).on_conflict_do_nothing())  # fmt: skip
    built: dict[str, Any] = result["profile"]
    return built


def reviews(engine: Engine, organization_id: int) -> dict[str, dict[str, Any]]:
    """The user's latest review per domain."""
    v = CompanyVerification
    with engine.connect() as conn:
        rows = conn.execute(
            select(v.item_key, v.status, v.value, v.actor, v.verified_at)
            .where(v.organization_id == organization_id, v.field == REVIEW_FIELD)
            .order_by(v.id)
        ).all()  # fmt: skip
    return {r.item_key: {"status": r.status, "value": r.value, "actor": r.actor,
                         "verified_at": r.verified_at.isoformat()} for r in rows}  # fmt: skip


async def discover(engine: Engine, settings: Settings, job: Any, owner: str, stage: Any,
                   stop: asyncio.Event | None = None, transport: Any = None,
                   search_transport: Any = None) -> dict[str, Any]:  # fmt: skip
    """Run one discovery job; its competitors are stored, the job's result returned."""
    d = settings.discovery
    out: dict[str, Any] = {"job_id": job.id, "organization_id": job.organization_id,
                           "status": "completed", "job_status": str(JobState.COMPLETED),
                           "stats": {}, "competitors": []}  # fmt: skip
    stats: dict[str, Any] = out["stats"]

    def stopped() -> bool:
        if stop is not None and stop.is_set():
            out["job_status"] = str(JobState.PAUSED)
            return True
        return False

    if not settings.llm.model:
        out["discovery"] = {"status": "disabled", "reason": "no llm.model"}
        return await _finish(engine, job, owner, stage, out)
    provider = make_search(settings.search, search_transport)
    if provider is None:
        out["discovery"] = {"status": "disabled", "reason": "no search API key"}
        return await _finish(engine, job, owner, stage, out)
    search = _LoggedSearch(provider, engine, job.id)
    reader = PageReader(replace(settings.llm, timeout=settings.llm.profile_timeout))
    collector = _LoggedCollector(settings, Budget(max_content_bytes=3_000_000),
                                 transport=transport)  # fmt: skip
    collector.log = (engine, job.id)
    blocked = (*DIRECTORY_DOMAINS, *settings.crawl.reference_domains)
    not_rivals = tuple(d for d in blocked if d not in ALSO_PRODUCTS)
    try:
        await stage("hypotheses")
        bip = await asyncio.to_thread(build_business_profile, engine, settings,
                                      job.organization_id)  # fmt: skip
        dossier = await asyncio.to_thread(build_dossier, engine, job.organization_id)
        subject = subject_of(bip, dossier)
        review = await asyncio.to_thread(reviews, engine, job.organization_id)
        text, refs = subject_lines(subject)
        model = await asyncio.to_thread(_cached_call, engine, settings, reader, HYP_VERSION,
                                        HYP_SYSTEM, HYP_SCHEMA, text, refs)  # fmt: skip
        hyp = hypotheses(subject, model)
        stats["hypotheses"] = hyp
        # Kept on the job (pipeline.job_result): what was searched and found, by stage.
        out["discovery"] = {"status": "ready", "unconfirmed": subject.unconfirmed,
                            "stats": stats}  # fmt: skip
        if stopped():
            return await _finish(engine, job, owner, stage, out)

        await stage("search")
        queries = build_queries(subject, hyp, d.max_queries, datetime.now(UTC), d.emerging_days)
        hits = await _search(search, queries, settings, subject.domain, stats)
        if not hits and stats["search_errors"]:
            # Search is down (network, quota): fail, so the last list found stays the
            # company's, rather than an empty one replacing it.
            raise SearchError(f"every search failed: {stats['search_errors'][0]}")
        if stopped():
            return await _finish(engine, job, owner, stage, out)

        await stage("extract")
        found, listings = candidates_from_hits(hits, subject, blocked)
        found += await _read_listings(engine, settings, reader, collector, listings, subject,
                                      d.max_listing_pages, stats)  # fmt: skip
        found += [Candidate(c["name"], c["domain"], "named_by_source",
                            [{"family": "direct", "query": None, "url": f"https://{subject.domain}/",
                              "title": None, "how": "named_on_site"}])
                  for c in subject.known]  # fmt: skip
        rejected = {k for k, r in review.items() if r["status"] == "rejected"}
        # What the profile says buyers use instead, by name: substitutes to judge.
        alts = merge([Candidate(a, None, found_by=[{
            "family": "substitute", "query": None, "url": None, "title": None,
            "how": "alternative"}]) for a in named_alternatives(hyp["alternatives"])],
            subject, not_rivals, rejected)  # fmt: skip
        alts = await _resolve(search, alts, subject, not_rivals, rejected, len(alts))
        cands = merge([*found, *alts], subject, not_rivals, rejected)
        cands = await _resolve(search, cands, subject, not_rivals, rejected,
                               d.max_candidates * 2)  # fmt: skip
        cands = merge(cands, subject, not_rivals, rejected)
        for domain, r in review.items():  # what the user added is always in
            value = r["value"] or {}
            if r["status"] == "edited" and value.get("added") and \
                    all(c.domain != domain for c in cands):  # fmt: skip
                cands.append(Candidate(value.get("name") or domain, domain, "user_added",
                                       [{"family": "user", "query": None, "url": None,
                                         "title": None, "how": "user_added"}]))  # fmt: skip
        resolved = [c for c in cands if c.domain]
        resolved.sort(key=_prerank, reverse=True)
        chosen = [c for c in resolved if c.basis == "user_added"]
        # The profile's own alternatives are few and never named by many pages: each
        # read, ahead of the rank.
        chosen += [c for c in resolved if c.basis != "user_added" and c.how("alternative")][:3]
        chosen += fair([c for c in resolved if all(c is not x for x in chosen)],
                       lambda c: c.families, d.max_candidates - len(chosen), each=2)  # fmt: skip
        stats.update(candidates=len(cands), resolved=len(resolved), profiled=len(chosen))
        if stopped():
            return await _finish(engine, job, owner, stage, out)

        await stage("profile")
        pages = await asyncio.gather(*(_own_pages(collector, c.domain or "", d.pages_per_candidate)
                                       for c in chosen))  # fmt: skip
        await stage("classify")
        system = judge_system(subject)
        new_since = (datetime.now(UTC) - timedelta(days=d.emerging_days)).year
        rows = []
        for c, (site, own) in zip(chosen, pages, strict=True):
            if site and c.domain and c.name == c.domain.split(".")[0].title():
                c.name = site  # named after its domain until its home page was read
            if stopped():
                return await _finish(engine, job, owner, stage, out)
            judged = None
            for compact in (False, True):  # once more, on less evidence, if no class came
                text, refs = judge_text(c, own, compact)
                judged = await asyncio.to_thread(_cached_call, engine, settings, reader,
                                                 JUDGE_VERSION, system, JUDGE_SCHEMA, text,
                                                 refs)  # fmt: skip
                if judged and judged.get("class"):
                    break
            row = _row(subject, job.id, c, judged, own, d.min_score, new_since)
            if row is not None:
                rows.append(row)
            else:  # why each profiled candidate was left out
                stats.setdefault("dropped", []).append({
                    "name": c.name, "domain": c.domain, "pages": len(own),
                    "class": ((judged or {}).get("class") or {}).get("value"),
                    "judged": judged is not None,
                    "score": score(judged or {}, c)})  # fmt: skip
        stats["kept"] = len(rows)
        await asyncio.to_thread(_store, engine, job.id, rows)
        out["competitors"] = [{k: r[k] for k in ("name", "domain", "class_", "score", "basis")}
                              for r in rows]  # fmt: skip
    finally:
        reader.close()
        await collector.aclose()
        await search.aclose()
    return await _finish(engine, job, owner, stage, out)


def _event(engine: Engine, job_id: int, event: str, outcome: str, family: str | None,
           detail: dict[str, Any]) -> None:  # fmt: skip
    """One search or page fetch of a discovery job, as a job event (``crawl_event``):
    what it did is kept, and the activity view shows it as it happens."""
    with engine.begin() as conn:
        conn.execute(insert(CrawlEvent).values(
            entity="job", job_id=job_id, event=event, to_state=outcome, actor="discovery",
            reason=family, detail=detail,
        ))  # fmt: skip


class _LoggedSearch:
    """The search provider, each query and page copy recorded (``_event``)."""

    def __init__(self, search: Any, engine: Engine, job_id: int) -> None:
        self._search, self._engine, self._job = search, engine, job_id

    async def search(self, query: str, n: int, include: tuple[str, ...] = (),
                     exclude: tuple[str, ...] = (), start_published: str | None = None,
                     family: str = "resolve") -> tuple[str, list[Any]]:  # fmt: skip
        started = time.perf_counter()
        detail: dict[str, Any] = {"query": query, "include": list(include)[:3],
                                  "since": start_published}  # fmt: skip
        try:
            provider, results = await self._search.search(
                query, n, include, exclude, start_published=start_published
            )
        except SearchError as exc:
            detail.update(ms=_ms(started), error=str(exc)[:300])
            await asyncio.to_thread(_event, self._engine, self._job, "discovery_search",
                                    "failed", family, detail)  # fmt: skip
            raise
        detail.update(ms=_ms(started), provider=provider, results=len(results))
        await asyncio.to_thread(_event, self._engine, self._job, "discovery_search", "ok",
                                family, detail)  # fmt: skip
        return provider, results

    async def contents(self, url: str) -> PageContents | None:
        started = time.perf_counter()
        try:
            page: PageContents | None = await self._search.contents(url)
        finally:
            await asyncio.to_thread(_event, self._engine, self._job, "discovery_fetch",
                                    "search_contents", "resolve",
                                    {"url": url, "ms": _ms(started)})  # fmt: skip
        return page

    async def aclose(self) -> None:
        await self._search.aclose()


class _LoggedCollector(Collector):
    """The page fetcher, each page recorded (``_event``) with its retrieval status."""

    log: tuple[Engine, int]

    async def collect(self, url: str, discovered_by: str, **kw: Any) -> Any:
        started = time.perf_counter()
        got = await super().collect(url, discovered_by, **kw)
        s = got.source
        await asyncio.to_thread(
            _event, *self.log, "discovery_fetch", str(s.status), discovered_by[:200],
            {"url": url, "ms": _ms(started), "detail": s.status_detail, "title": s.title},
        )  # fmt: skip
        return got


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


async def _finish(engine: Engine, job: Any, owner: str, stage: Any,
                  out: dict[str, Any]) -> dict[str, Any]:  # fmt: skip
    if out["job_status"] == JobState.COMPLETED:
        await stage("done")
        await asyncio.to_thread(JobStore(engine).set_stage, job.id, owner, JobState.FINALIZING)
    return out


async def _search(search: Any, queries: list[Query], settings: Settings, own: str,
                  stats: dict[str, Any]) -> list[Hit]:  # fmt: skip
    gate = asyncio.Semaphore(max(1, settings.external.concurrency))
    n = settings.search.results_per_query
    errors: list[str] = []

    async def one(q: Query) -> list[Hit]:
        async with gate:
            try:
                exclude = () if q.include else (own,)
                _, results = await search.search(
                    q.text, n, q.include, exclude, start_published=q.since, family=q.family
                )
            except SearchError as exc:
                errors.append(str(exc)[:200])
                return []
        return [Hit(r.url, r.title, q, r.position) for r in results]

    found = await asyncio.gather(*(one(q) for q in queries))
    stats["queries"] = {q.text: len(h) for q, h in zip(queries, found, strict=True)}
    stats["search_errors"] = errors[:5]
    return [h for hs in found for h in hs]


async def _read_listings(engine: Engine, settings: Settings, reader: PageReader,
                         collector: Collector, listings: list[Hit], subject: Subject,
                         limit: int, stats: dict[str, Any]) -> list[Candidate]:  # fmt: skip
    """Fetch the best listing pages (found by most queries, then best position; a few of
    each search family's) and read each for the companies it presents."""
    count: dict[str, int] = {}
    first: dict[str, Hit] = {}
    for h in listings:
        count[h.url] = count.get(h.url, 0) + 1
        if h.url not in first or h.position < first[h.url].position:
            first[h.url] = h
    ranked = sorted(first.values(), key=lambda h: (-count[h.url], h.position))
    todo = fair(ranked, lambda h: {h.query.family}, limit, each=3)
    got = await asyncio.gather(*(collector.collect(h.url, h.query.text) for h in todo))
    out: list[Candidate] = []
    read = 0
    for h, c in zip(todo, got, strict=True):
        if c.page is None or c.source.status is not RetrievalStatus.FETCHED:
            continue
        page_text = c.page.cleaned_text[:8000]
        chunks = _chunks(page_text, 500)
        text, refs = _numbered([f"page {h.url}: {x}" for x in chunks],
                               [{"url": h.url, "quote": x} for x in chunks])  # fmt: skip
        answer = await asyncio.to_thread(
            _cached_call, engine, settings, reader, LIST_VERSION, LIST_SYSTEM, LIST_SCHEMA,
            f"The company: {subject.name}\n{text}", refs,
        )  # fmt: skip
        read += 1
        links = [(link.url, link.text or "") for link in c.page.links]
        out += listing_candidates(answer, h, page_text, links, subject)
    stats["listing_pages"] = {"tried": len(todo), "read": read}
    return out


async def _resolve(
    search: Any,
    cands: list[Candidate],
    subject: Subject,
    blocked: Sequence[str],
    rejected: set[str],
    limit: int,
) -> list[Candidate]:
    """A domain for the strongest candidates known by name only: the website on the
    LinkedIn page that named it, else the one search result whose domain is its name."""
    todo = sorted((c for c in cands if not c.domain), key=_prerank, reverse=True)[:limit]

    async def one(c: Candidate) -> None:
        if c.linkedin and hasattr(search, "contents"):
            try:
                page: PageContents | None = await search.contents(c.linkedin)
            except SearchError:
                page = None
            m = re.search(r"Homepage:\s*((?:[a-z0-9-]+\.)+[a-z]{2,})", page.text if page else "",
                          re.I)  # fmt: skip
            if m:
                c.domain = registrable_domain(m.group(1).lower())
                return
        try:
            _, results = await search.search(f'"{c.name}"', 5, (), blocked[:20])
        except SearchError:
            return
        # Its name, or its maker's: "LinkedIn Sales Navigator" is on linkedin.com.
        slugs = {_key(c.name), _key(c.name.split()[0])}
        for r in results:
            d = _domain(r.url)
            if d and not _listed(d, blocked) and d.split(".")[0].replace("-", "") in slugs:
                c.domain = d
                return

    await asyncio.gather(*(one(c) for c in todo))
    return [
        c for c in cands if c.domain and c.domain not in rejected and c.domain != subject.domain
    ]


def site_name(og: Sequence[str], title: str | None, domain: str) -> str | None:
    """The name a home page gives its company: its og:site_name, else the part of its
    title that is the domain's name ("Sales platform - Apollo" on apollo.io: "Apollo");
    None when no part is. A name is a few words: "Origami gets customers for you." is not."""
    if og and og[0].strip() and len(og[0].split()) <= 4:
        return og[0].strip()
    label = _key(domain.split(".")[0])
    for part in _TITLE_SPLIT.split(title or ""):
        key = _key(part)
        if key and (key.startswith(label) or label.startswith(key)) and len(part.split()) <= 4:
            return part.strip()
    return None


async def _own_pages(collector: Collector, domain: str,
                     limit: int) -> tuple[str | None, list[tuple[str, str]]]:  # fmt: skip
    """The name the candidate's home page gives it, and (url, text) of that page and of
    the pages it links to that say what it sells and to whom (pricing, about, product),
    up to ``limit``."""
    home = await collector.collect(f"https://{domain}/", "discovery", vendor=True)
    if home.page is None or home.source.status is not RetrievalStatus.FETCHED:
        return None, []
    pages = [(home.page.url, home.page.cleaned_text)]
    site = site_name(home.page.meta.get("og:site_name") or [], home.page.title, domain)
    if site:
        pages[0] = (home.page.url, f"{site}. {home.page.cleaned_text}")
    links = list(dict.fromkeys(
        link.url.split("#")[0] for link in home.page.links
        if _domain(link.url) == domain and _KEY_PATH.search(urlsplit(link.url).path)
    ))[: limit - 1]  # fmt: skip
    for got in await asyncio.gather(*(collector.collect(u, "discovery", vendor=True)
                                      for u in links)):  # fmt: skip
        if got.page is not None and got.source.status is RetrievalStatus.FETCHED:
            pages.append((got.page.url, got.page.cleaned_text))
    return site, pages


PROFILE_FIELDS = ("sells", "positioning", "pricing", "target_customers")


# What a price says: an amount, or how it is charged ("Paid for once" is a slogan).
_PRICE = re.compile(r"[$€£¥₹]|\d|\b(free|trial|per|plans?|tiers?|seats?|credits?|usage|"
                    r"monthly|month|annual(ly)?|yearly|quote|contact sales|custom)\b",
                    re.I)  # fmt: skip
_NOT_SAID = re.compile(r"\bnot\s+(explicitly\s+|clearly\s+)?(stated|specified|mentioned|"
                       r"available|disclosed|provided|listed)\b", re.I)  # fmt: skip


def profile_value(key: str, value: Any, own: set[str]) -> Any:
    """``value`` with only the citations from the candidate's own pages; None if none is
    (the pages that found it, rival lists, describe other companies, and the model wrote
    their products and prices down as this one's), if it says the evidence does not say,
    or if it is a price that names no amount or way of charging."""
    if isinstance(value, list):
        return [x for v in value if (x := profile_value(key, v, own)) is not None] or None
    if not isinstance(value, dict):
        return None
    said = str(value.get("value") or "")
    if _NOT_SAID.search(said) or (key == "pricing" and not _PRICE.search(said)):
        return None
    cites = [c for c in value.get("citations") or [] if c.get("url") in own]
    return {**value, "citations": cites} if cites else None


def _row(subject: Subject, job_id: int, c: Candidate, judged: dict[str, Any] | None,
         pages: Sequence[tuple[str, str]], min_score: float,
         new_since: int) -> dict[str, Any] | None:  # fmt: skip
    """The stored row for a judged candidate; None when it is not a competitor (a
    candidate the user added is kept whatever the model said). One its own pages say was
    founded in ``new_since`` or later is ``emerging``, whatever it sells."""
    judged = judged or {}
    cls = (judged.get("class") or {}).get("value")
    added = c.basis == "user_added"
    s = score(judged, c)
    if not added and (cls not in COMPETITOR_CLASSES or s < min_score):
        return None
    reasoning = judged.get("reasoning") or {}
    own = {u for u, _ in pages}
    founded = profile_value("founded", judged.get("founded"), own)
    if founded and not any(  # "© 2026 Reachable" is not when it was founded
        re.search(rf"\b(found|launch|establish|since|start|incorporat)\w*\D{{0,30}}"
                  rf"{founded['value']}", c.get("quote", ""), re.I)
        for c in founded["citations"]
    ):  # fmt: skip
        founded = None
    if cls in ("direct", "indirect") and founded and founded["value"] >= new_since:
        cls = "emerging"
    return {
        "organization_id": subject.organization_id, "job_id": job_id, "name": c.name,
        "domain": c.domain, "class_": cls if cls in COMPETITOR_CLASSES else "direct",
        "score": s, "basis": c.basis,
        "reasoning": reasoning.get("value") if isinstance(reasoning, dict) else None,
        "profile": {k: v for k in PROFILE_FIELDS
                    if (v := profile_value(k, judged.get(k), own)) is not None}
                   | ({"founded": founded} if founded else {})
                   | {k: judged[k] for k in ("same_buyers", "same_problem", "same_budget",
                                             "class", "reasoning") if k in judged}
                   | {"pages_read": [u for u, _ in pages]},
        "found_by": c.found_by[:20],
    }  # fmt: skip


def _store(engine: Engine, job_id: int, rows: list[dict[str, Any]]) -> None:
    with engine.begin() as conn:
        conn.execute(delete(CompetitorCandidate).where(CompetitorCandidate.job_id == job_id))
        if rows:
            conn.execute(pg_insert(CompetitorCandidate).values(
                [{("class" if k == "class_" else k): v for k, v in r.items()} for r in rows]
            ))  # fmt: skip


# -- review ---------------------------------------------------------------------------


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(max_length=64)
    action: Literal["approve", "reject", "add", "reclassify"]
    name: str | None = Field(None, max_length=200)
    competitor_class: Literal["direct", "indirect", "substitute", "emerging"] | None = Field(
        None, alias="class"
    )
    actor: str | None = Field(None, max_length=200)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ReviewItem] = Field(max_length=200)


def review_domain(raw: str) -> str:
    """The registrable domain of what the user typed ("https://www.Apollo.io/x" ->
    apollo.io). Raises ValueError when it is not a domain."""
    text = raw.strip().lower()
    host = urlsplit(text if "//" in text else f"//{text}").hostname or ""
    if not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", host):
        raise ValueError(f"not a domain: {raw!r}")
    return registrable_domain(host)


def save_review(engine: Engine, organization_id: int, request: ReviewRequest) -> None:
    """Record the user's review: approve and reject a found competitor; add one (name
    required) or reclassify one (class required). Raises ValueError for bad input."""
    status = {"approve": "confirmed", "reject": "rejected", "add": "edited",
              "reclassify": "edited"}  # fmt: skip
    rows = []
    for i in request.items:
        domain = review_domain(i.domain)
        if i.action == "add" and not i.name:
            raise ValueError("add needs the competitor's name")
        if i.action == "reclassify" and not i.competitor_class:
            raise ValueError("reclassify needs a class")
        value = None
        if i.action in ("add", "reclassify"):
            value = {"name": i.name, "domain": domain, "class": i.competitor_class,
                     "added": i.action == "add"}  # fmt: skip
        rows.append({"organization_id": organization_id, "section": REVIEW_SECTION,
                     "field": REVIEW_FIELD, "item_key": domain, "status": status[i.action],
                     "value": value, "actor": i.actor})  # fmt: skip
    if rows:
        with engine.begin() as conn:
            conn.execute(pg_insert(CompanyVerification).values(rows))


def discovery_jobs(engine: Engine, organization_id: int) -> list[Any]:
    """The company's discovery jobs, newest first."""
    with engine.connect() as conn:
        jobs = conn.execute(
            select(CrawlJob.id, CrawlJob.status, CrawlJob.stage, CrawlJob.result,
                   CrawlJob.finished_at)
            .where(CrawlJob.organization_id == organization_id, CrawlJob.kind == "discovery")
            .order_by(CrawlJob.id.desc())
        ).all()  # fmt: skip
    return list(jobs)


def competitors_view(engine: Engine, organization_id: int) -> dict[str, Any]:
    """The latest completed discovery's competitors with the user's review."""
    jobs = discovery_jobs(engine, organization_id)
    done = next((j for j in jobs if j.status == JobState.COMPLETED), None)
    rows: list[Any] = []
    if done is not None:
        with Session(engine) as session:
            rows = list(session.execute(
                select(CompetitorCandidate).where(CompetitorCandidate.job_id == done.id)
                .order_by(CompetitorCandidate.score.desc(), CompetitorCandidate.id)
            ).scalars())  # fmt: skip
    found = [{"name": r.name, "domain": r.domain, "class": r.class_, "score": r.score,
              "basis": r.basis, "reasoning": r.reasoning, "profile": r.profile,
              "found_by": r.found_by} for r in rows]  # fmt: skip
    result = (done.result or {}) if done is not None else {}
    with engine.connect() as conn:
        domain = conn.execute(select(Organization.registrable_domain)
                              .where(Organization.id == organization_id)).scalar()  # fmt: skip
    return {
        "company_id": organization_id,
        "domain": domain,
        "job": ({"job_id": jobs[0].id, "status": jobs[0].status, "stage": jobs[0].stage}
                if jobs else None),
        "discovered_by_job": done.id if done is not None else None,
        "unconfirmed": (result.get("discovery") or {}).get("unconfirmed", []),
        **review_view(found, reviews(engine, organization_id)),
    }  # fmt: skip


def review_view(found: list[dict[str, Any]], review: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Found competitors with the user's review, grouped by class; pure. A reclassified
    one moves to its new class; one the user added is listed even if not found."""
    status = {"confirmed": "approved", "rejected": "rejected", "edited": "approved"}
    items = []
    for f in found:
        r = review.get(f["domain"])
        item = {**f, "review": status.get(r["status"], "suggested") if r else "suggested",
                "reviewed_at": r["verified_at"] if r else None}  # fmt: skip
        if r and r["status"] == "edited" and (r["value"] or {}).get("class"):
            item["class"] = r["value"]["class"]
        if r and r["status"] == "edited" and (r["value"] or {}).get("added"):
            item["review"] = "user_added"
        items.append(item)
    listed = {f["domain"] for f in found}
    for domain, r in review.items():
        value = r["value"] or {}
        if r["status"] == "edited" and value.get("added") and domain not in listed:
            items.append({"name": value.get("name") or domain, "domain": domain,
                          "class": value.get("class") or "direct", "score": None,
                          "basis": "user_added", "reasoning": None, "profile": {},
                          "found_by": [], "review": "user_added",
                          "reviewed_at": r["verified_at"]})  # fmt: skip
    return {"competitors": {cls: [i for i in items if i["class"] == cls]
                            for cls in COMPETITOR_CLASSES},
            "counts": {s: sum(i["review"] == s for i in items)
                       for s in ("suggested", "approved", "rejected", "user_added")}}  # fmt: skip
