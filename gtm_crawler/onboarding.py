"""The onboarding view (Integration P1): what was found about the company, for the
user to verify, and the competitors discovered.

``onboarding_view`` regroups the dossier (``dossier.build_dossier``) under the six
headings the onboarding flow verifies: company info, value proposition, market
segment, target market, business model, products and services. Nothing is
invented: a field with no observation is ``value: None`` with its state. A value
only the company states (an UNSUPPORTED claim) is shown as found, with that state:
at onboarding the user is the company and confirms or corrects it.

The user's answers (``company_verification``) are user input, never crawler
evidence: they are shown beside each item and do not change any assessment.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .db.models import CompanyVerification, CrawlJob
from .dossier import COMPETITOR_FIELDS, build_dossier

MAX_CITATIONS = 5
# (key, title, ((field, label), ...)). Product fields are gathered across products.
SECTIONS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "company_info",
        "Company info",
        (
            ("company_name", "Company name"),
            ("legal_name", "Legal name"),
            ("website", "Website"),
            ("company_description", "Description"),
            ("headquarters", "Headquarters"),
            ("company_location", "Location"),
            ("founded_year", "Founded"),
            ("company_size", "Company size"),
            ("employee_count", "Employees"),
        ),
    ),
    ("value_proposition", "Value proposition", (("product_positioning", "Value proposition"),)),
    (
        "market_segment",
        "Market segment",
        (("market_domain", "Market / industry"), ("product_category", "Product category")),
    ),
    (
        "target_market",
        "Target market",
        (
            ("customer", "Named customers"),
            ("main_use_case", "Main use cases"),
            ("customer_concentration", "Customer concentration"),
        ),
    ),
    (
        "business_model",
        "Business model",
        (("business_model", "Business model"), ("plan_name", "Plans"), ("price", "Prices")),
    ),
    ("products_services", "All products / services", (("product", "Product"),)),
)
SECTION_FIELDS = {key: {f for f, _ in fields} for key, _, fields in SECTIONS}
_STATE_RANK = (
    "CORROBORATED",
    "SUPPORTED",
    "UNSUPPORTED",
    "UNVERIFIED",
    "STALE",
    "CONFLICTING",
    "UNKNOWN",
    "NOT_APPLICABLE",
)


class VerificationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: str
    field: str
    item_key: str = Field("", max_length=64)
    status: Literal["confirmed", "edited", "rejected"]
    value: Any = None


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[VerificationItem] = Field(max_length=500)


def _entries(v: Any) -> list[dict[str, Any]]:
    return [v] if isinstance(v, dict) else list(v or [])


def _text(value: Any) -> Any:
    """A plain value for display: a structured value (a price) becomes one string."""
    if isinstance(value, dict):
        parts = [str(v) for k, v in value.items() if v not in (None, "") and k != "raw"]
        return " ".join(parts) or None
    return value


def _found(e: dict[str, Any]) -> Any:
    value = e.get("value", e.get("claimed_value"))
    if value is None and e.get("state") == "CONFLICTING":
        # No one value: show what each source says, for the user to choose.
        value = list(dict.fromkeys(c["value_verbatim"] for c in e.get("citations", []))) or None
    return _text(value)


def _item(
    field: str, label: str, entries: list[dict[str, Any]], item_key: str = "", scalar: bool = False
) -> dict[str, Any]:
    found = [e for e in entries if _found(e) is not None]
    flat = [v for e in found for v in (_found(e) if isinstance(_found(e), list) else [_found(e)])]
    values = list(dict.fromkeys(v if isinstance(v, int | float) else str(v) for v in flat))
    states = [e.get("state", "UNKNOWN") for e in (found or entries)]
    state = min(
        states, key=lambda s: _STATE_RANK.index(s) if s in _STATE_RANK else 99, default="UNKNOWN"
    )
    confidences = [e["confidence"] for e in found if e.get("confidence") is not None]
    citations = [
        {
            "url": c["url"],
            "quote": c["quote"],
            "source_type": c["source_type"],
            "tier": c["source_tier"],
        }
        for e in found
        for c in e.get("citations", [])
    ][:MAX_CITATIONS]
    return {
        "field": field,
        "label": label,
        "item_key": item_key,
        # A conflict stays a list of the candidates, even for a one-value field.
        "value": (values[0] if scalar and state != "CONFLICTING" else values) if values else None,
        "state": state,
        "confidence": max(confidences) if confidences else None,
        "citations": citations,
        "verification": None,
    }


def _product_item(product: dict[str, Any]) -> dict[str, Any]:
    fields = product["fields"]
    item = _item(
        "product",
        "Product",
        [{"value": product["name"], "state": "SUPPORTED"}],
        str(product["product_id"]),
        scalar=True,
    )
    for f in ("product_description", "product_positioning", "key_feature", "price"):
        item["citations"] += _item(f, f, _entries(fields.get(f)))["citations"]
    item["citations"] = item["citations"][:MAX_CITATIONS]

    def values(field: str) -> list[Any]:
        return _item(field, field, _entries(fields.get(field)))["value"] or []

    item["details"] = {
        "description": next(iter(values("product_description")), None),
        "category": next(iter(values("product_category")), None),
        "key_features": values("key_feature"),
        "prices": values("price"),
        "plans": values("plan_name"),
    }
    return item


def onboarding_view(
    engine: Engine, organization_id: int, profile: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The onboarding sections; with ``profile`` (``profile.stored_profile``), items the
    evidence left empty are filled from the model's profile, marked as inferred."""
    dossier = build_dossier(engine, organization_id)
    company = dossier["company"]["fields"]
    products = dossier["products"]

    def everywhere(field: str) -> list[dict[str, Any]]:
        """A field's entries for the company and for each of its products."""
        return [
            *_entries(company.get(field)),
            *(e for p in products for e in _entries(p["fields"].get(field))),
        ]

    sections: list[dict[str, Any]] = []
    for key, title, fields in SECTIONS:
        items: list[dict[str, Any]] = []
        if key == "products_services":
            items = [_product_item(p) for p in products]
            if not items:
                items = [_item("product", "Product", [])]
        elif key == "value_proposition":
            loose = _entries(company.get("product_positioning"))
            items = [
                _item(
                    "product_positioning",
                    f"Value proposition: {p['name']}",
                    _entries(p["fields"].get("product_positioning")),
                    str(p["product_id"]),
                )
                for p in products
                if any(
                    _found(e) is not None for e in _entries(p["fields"].get("product_positioning"))
                )
            ]
            if loose or not items:
                items.insert(0, _item("product_positioning", "Value proposition", loose))
        else:
            items = [_item(f, label, everywhere(f), scalar=f in _SCALAR) for f, label in fields]
        sections.append({"key": key, "title": title, "items": items})

    if profile and profile.get("profile"):
        fill_from_profile(sections, profile["profile"])
    answers = verification(engine, organization_id)
    by_key = {(a["field"], a["item_key"]): a for a in answers}
    for section in sections:
        for item in section["items"]:
            item["verification"] = by_key.get((item["field"], item["item_key"]))
    answered = {a["section"] for a in answers}

    with engine.connect() as conn:
        job = conn.execute(
            select(CrawlJob.id, CrawlJob.status, CrawlJob.root_url)
            .where(CrawlJob.organization_id == organization_id)
            .order_by(CrawlJob.id.desc())
            .limit(1)
        ).first()
    return {
        "company_id": organization_id,
        "domain": dossier["company"]["domain"],
        "website": job.root_url if job else None,
        "crawl": {"crawl_id": job.id, "status": job.status.upper()} if job else None,
        "sections": sections,
        "competitors": [
            {
                **{
                    k: c[k]
                    for k in ("organization_id", "name", "domain", "evidence_basis", "assessed")
                },
                "fields": {
                    f: _item(f, f, _entries(c["fields"].get(f)), scalar=f not in _LIST)["value"]
                    for f in COMPETITOR_FIELDS
                },
            }
            for c in dossier["competitors"]
        ],
        "candidate_competitors": [
            {"name": c["name"], "evidence_basis": c["evidence_basis"]}
            for c in dossier["candidate_competitors"]
        ],
        "verified": all(key in answered for key, _, _ in SECTIONS),
        "profile": profile,
    }


_SCALAR = frozenset(
    {
        "company_name",
        "legal_name",
        "website",
        "company_description",
        "headquarters",
        "founded_year",
        "company_size",
        "employee_count",
        "business_model",
        "customer_concentration",
    }
)


# Competitor fields shown as lists; the others as their first (best) value.
_LIST = frozenset({"key_feature", "investor", "funding_round"})

# Onboarding fields the profile (profile.py) fills when the evidence alone left them
# empty: field -> (profile section, key). The business model is its own case.
PROFILE_FIELDS = {
    "company_description": ("company", "description"),
    "headquarters": ("company", "headquarters"),
    "company_location": ("company", "locations"),
    "founded_year": ("company", "founded_year"),
    "company_size": ("company", "size"),
    "product_positioning": ("value_proposition", "statement"),
    "market_domain": ("market", "category"),
    "product_category": ("market", "sub_category"),
    "main_use_case": ("target_customers", "use_cases"),
    "customer": ("target_customers", "named_customers"),
    "price": ("business_model", "price_range"),
}
_BUSINESS_MODEL_PARTS = ("customer_type", "revenue_model", "delivery", "sales_motion")


def _leaves(node: Any) -> list[dict[str, Any]]:
    return [n for n in (node if isinstance(node, list) else [node]) if isinstance(n, dict)]


def _inferred(item: dict[str, Any], leaves: list[dict[str, Any]], scalar: bool,
              reasoning: dict[str, Any] | None = None) -> None:  # fmt: skip
    """Fill an empty onboarding ``item`` from profile ``leaves`` (model-inferred)."""
    values = [leaf["value"] for leaf in leaves]
    if reasoning:
        leaves = [*leaves, reasoning]
    confidences = [c for leaf in leaves if (c := leaf.get("evidence_confidence")) is not None]
    item.update({
        "value": values[0] if scalar else values,
        "state": "MODEL_INFERRED",
        "confidence": max(confidences) if confidences else None,
        "citations": [{"url": c["url"], "quote": c["quote"], "source_type": "model_cited",
                       "tier": None}
                      for leaf in leaves for c in leaf["citations"] if "url" in c][:MAX_CITATIONS],
    })  # fmt: skip
    if reasoning:
        item["reasoning"] = reasoning["value"]


def fill_from_profile(sections: list[dict[str, Any]], profile: dict[str, Any]) -> None:
    """Fill the items the evidence left empty from the model's profile, marked
    ``MODEL_INFERRED`` with the evidence it cited. Found values are never replaced, except
    the company's value proposition (a tagline) by the model's statement."""
    value_prop = profile.get("value_proposition") or {}
    model = profile.get("business_model") or {}
    for section in sections:
        items = section["items"]
        if section["key"] == "products_services" and not any(i["value"] for i in items):
            offerings = [o for o in profile.get("offerings") or [] if "name" in o]
            section["items"] = items = []
            for o in offerings:
                item: dict[str, Any] = {"field": "product", "label": "Product",
                                        "item_key": f"llm:{o['name']['value']}",
                                        "verification": None}  # fmt: skip
                _inferred(item, [o["name"]], scalar=True)
                item["details"] = {
                    "type": (o.get("type") or {}).get("value"),
                    "description": (o.get("description") or {}).get("value"),
                    "key_features": [f["value"] for f in _leaves(o.get("key_features"))],
                    "prices": [o["price"]["value"]] if o.get("price") else [],
                }
                items.append(item)
            continue
        for item in items:
            # The company's own value proposition is the model's statement when it has
            # one: a found product_positioning there is the home page's tagline (h1),
            # which says what the company calls itself, not what it does for whom.
            tagline = item["field"] == "product_positioning" and value_prop.get("statement")
            if (item["value"] is not None and not tagline) or item["item_key"]:
                continue
            field = item["field"]
            if field == "business_model":
                parts = [model[k] for k in _BUSINESS_MODEL_PARTS if k in model]
                if parts:
                    _inferred(item, parts, scalar=False, reasoning=model.get("reasoning"))
                continue
            if field not in PROFILE_FIELDS:
                continue
            group, key = PROFILE_FIELDS[field]
            leaves = _leaves((profile.get(group) or {}).get(key))
            if leaves:
                reasoning = value_prop.get("reasoning") if field == "product_positioning" else None
                _inferred(item, leaves, scalar=field in _SCALAR, reasoning=reasoning)


def verification(engine: Engine, organization_id: int) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        v = CompanyVerification
        rows = conn.execute(
            select(v.section, v.field, v.item_key, v.status, v.value, v.verified_at)
            .where(v.organization_id == organization_id)
            .order_by(v.id)
        ).all()
    return [
        {
            "section": r.section,
            "field": r.field,
            "item_key": r.item_key,
            "status": r.status,
            "value": r.value,
            "verified_at": r.verified_at.isoformat(),
        }
        for r in rows
    ]


def save_verification(
    engine: Engine, organization_id: int, request: VerificationRequest
) -> list[dict[str, Any]]:
    """Record the user's answers (a later answer for the same item replaces it).

    Raises ValueError for a section or field the onboarding view does not have."""
    for item in request.items:
        if item.field not in SECTION_FIELDS.get(item.section, ()):
            raise ValueError(f"no field {item.field!r} in section {item.section!r}")
    # One row per item: the last answer for an item in the request wins.
    latest = {(i.field, i.item_key): i for i in request.items}
    if latest:
        rows = [{"organization_id": organization_id, **i.model_dump()} for i in latest.values()]
        stmt = pg_insert(CompanyVerification).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["organization_id", "field", "item_key"],
            set_={
                "section": stmt.excluded.section,
                "status": stmt.excluded.status,
                "value": stmt.excluded.value,
                "verified_at": stmt.excluded.verified_at,
            },
        )
        with engine.begin() as conn:
            conn.execute(stmt)
    return verification(engine, organization_id)
