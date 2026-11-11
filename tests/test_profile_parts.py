"""Phase 4: the model fills every public profile field, in parts a small model can answer:
offering details, brand and messaging, ideal-customer hypotheses, funding and traction."""

from typing import Any

from gtm_crawler import profile

from .test_business_profile import bip, cite, dossier, field
from .test_profile import REFS, leaf, stats

LEAF = {
    "evidence_confidence": 0.6,
    "citations": [{"observation_id": 9, "url": "https://acme.example/", "quote": "q"}],
}


def cited(value: Any) -> dict[str, Any]:
    return {"value": value, **LEAF}


def test_the_profile_is_made_of_six_parts() -> None:
    assert [(v, k) for v, _, _, k in profile.PARTS] == [
        ("profile-8", None),
        ("value-prop-2", "value_proposition"),
        ("facts-1", "facts"),
        ("offerings-2", "offering_details"),
        ("brand-1", "brand"),
        ("icp-1", "icp"),
    ]


def test_offering_details_keep_only_their_choices() -> None:
    s = stats()
    out = profile.clean(profile.OFFERINGS_SCHEMA, {"offerings": [{
        "name": leaf("Data Science", "e2"),
        "benefits": [leaf("Job-ready skills", "e2")],
        "pricing_model": leaf("custom_quote", "e2"),
        "maturity": leaf("alpha", "e2"),  # not a choice
        "target_users": [leaf("Graduates", "e2")],
    }]}, REFS, s)  # fmt: skip
    offering = out["offerings"][0]
    assert set(offering) == {"name", "benefits", "pricing_model", "target_users"}
    assert s["invalid"] == 1


def test_brand_and_icp_schemas_have_the_spec_fields() -> None:
    assert set(profile.BRAND_SCHEMA) >= {
        "mission", "vision", "values", "promise", "positioning_statement", "tagline",
        "key_messages", "selling_points", "claims", "calls_to_action", "tone_of_voice",
        "personality", "consistency", "reasoning",
    }  # fmt: skip
    assert set(profile.ICP_SCHEMA) == {"reasoning", "stated_target_market", "hypotheses"}
    # Funding and traction are their own small call: a bigger main schema made the
    # small model drop or misplace whole sections (a SaaS site lost its business model).
    assert not set(profile.SCHEMA) & {"funding", "traction"}
    assert "ownership" not in profile.SCHEMA["company"]
    assert set(profile.FACTS_SCHEMA) == {"ownership", "total_funding", "funding_rounds",
                                         "investors", "valuation", "customer_count",
                                         "annual_revenue", "arr"}  # fmt: skip
    assert "plans" in profile.SCHEMA["business_model"]
    assert "case_studies" in profile.SCHEMA["target_customers"]


def test_a_customer_count_is_any_figure_the_evidence_has() -> None:
    def count(value: Any) -> Any:
        out = profile.clean(profile.FACTS_SCHEMA, {"customer_count": leaf(value, "e1")},
                            REFS, stats())  # fmt: skip
        return out and out["customer_count"]["value"]

    # A year-sized range check once dropped every real count.
    assert count(15000) == 15000
    assert count("15,000+") == "15,000+"
    assert count("many") is None
    assert count(0) is None
    assert count(99) is None  # not in the cited line


def test_a_competitors_domain_is_a_host_name() -> None:
    out = profile.clean(profile.SCHEMA, {"competitors": [
        {"name": leaf("Rival", "e2"), "domain": leaf("Bakery", "e2")},
        {"name": leaf("Other", "e2"), "domain": leaf("www.other.co.uk", "e2")},
    ]}, REFS, stats())  # fmt: skip
    assert [(c["name"]["value"], "domain" in c) for c in out["competitors"]] == [
        ("Rival", False), ("Other", True)]  # fmt: skip


def test_a_section_the_model_nested_in_another_is_put_back() -> None:
    s = stats()
    out = profile.clean(profile.SCHEMA, profile.lift(profile.SCHEMA, {
        "company": {"name": leaf("Acme", "e2")},
        "business_model": {
            "customer_type": leaf("b2b", "e2"),
            # The model closed business_model too late: these belong at the top.
            "offerings": [{"name": leaf("Data Science", "e2")}],
            "market": {"category": leaf("Training", "e2")},
        },
    }), REFS, s)  # fmt: skip
    assert out["business_model"]["customer_type"]["value"] == "b2b"
    assert out["offerings"][0]["name"]["value"] == "Data Science"
    assert out["market"]["category"]["value"] == "Training"
    # A key the section itself has stays where it is.
    nested = {"business_model": {"plans": [leaf("Pro", "e2")]}, "plans": "junk"}
    assert profile.lift(profile.SCHEMA, nested)["business_model"]["plans"]


def test_every_part_fills_the_profile() -> None:
    product = {"product_id": 4, "name": "Arm", "fields": {
        "product_description": {"state": "SUPPORTED", "value": "A robot arm",
                                "citations": [cite(9, "2026-11-01T00:00:00+00:00")]}}}  # fmt: skip
    model = {
        "brand": {"mission": cited("Robots for every warehouse"),
                  "tone_of_voice": cited("plain and confident"),
                  "calls_to_action": [cited("Book a demo")]},
        "icp": {"hypotheses": [{"segment": cited("Mid-size 3PL warehouses"),
                                "why": cited("Case studies are all 3PLs")}]},
        "offering_details": {"offerings": [{"name": cited("arm"),
                                            "benefits": [cited("Fewer picking errors")],
                                            "maturity": cited("beta")}]},
        "facts": {"investors": [cited("Northwind Ventures")], "customer_count": cited(120)},
        "value_proposition": {"proof": [{"kind": {"value": "award"},
                                         "claim": cited("Best robot 2025")}]},
    }  # fmt: skip
    view = bip(dossier({}, [product]), model)
    assert field(view, "mission")["value"] == "Robots for every warehouse"
    assert field(view, "tone_of_voice")["status"] == "Inferred"
    assert field(view, "calls_to_action")["value"] == ["Book a demo"]
    assert field(view, "investor")["value"] == ["Northwind Ventures"]
    assert field(view, "customer_count")["value"] == 120
    # A composite item keeps its parts.
    assert field(view, "customer_proof")["value"] == [{"kind": "award",
                                                       "claim": "Best robot 2025"}]  # fmt: skip
    icp = field(view, "icp_hypotheses")
    assert icp["value"] == [{"segment": "Mid-size 3PL warehouses",
                             "why": "Case studies are all 3PLs"}]  # fmt: skip
    assert icp["status"] == "Inferred"
    # A hypothesis stays an unresolved assumption until the user confirms it.
    open_items = {m["key"]: m for m in view["sections"][12]["items"]}
    assert open_items["icp_hypotheses"]["reason"].startswith("hypothesis")
    # Offering details join their offering by name, whatever its case.
    arm = view["sections"][1]["items"][0]
    assert arm["details"]["benefits"] == ["Fewer picking errors"]
    assert arm["details"]["maturity"] == "beta"
