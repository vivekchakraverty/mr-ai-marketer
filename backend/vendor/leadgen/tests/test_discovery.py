"""Discovery: clause helpers, Overpass query building, SearXNG query build (no network), and
the ICP query-selector's anti-monotone pruning."""

from vendor.leadgen.discovery import bluesky, icp, overpass, searxng
from vendor.leadgen.discovery.base import clause_key, clause_terms, domain_of


def test_clause_helpers():
    a = {"category": "clinic", "location": "Austin"}
    b = {"location": "Austin", "category": "clinic"}
    assert clause_key(a) == clause_key(b)  # order-independent
    assert clause_terms(a) == "clinic Austin"


def test_domain_of():
    assert domain_of("https://www.Example.com/x") == "example.com"
    assert domain_of("clinic10.com") == "clinic10.com"
    assert domain_of("") == ""


def test_overpass_requires_location():
    assert overpass.build_query({"category": "clinic"}) is None
    q = overpass.build_query({"category": "dental clinic", "location": "Austin", "keyword": "cosmetic"})
    assert q is not None
    assert "area[" in q and "out center" in q
    assert "dental" in q.lower()


def test_searxng_query_string_includes_terms():
    q = searxng._query_string({"category": "coffee roaster", "location": "Portland"})
    assert "coffee roaster" in q and "Portland" in q


def test_anti_monotone_pruning():
    # A recorded size-1 empty {location: Oman} kills any candidate mentioning Oman.
    empties = [{"location": "Oman"}]
    assert icp._pruned_by_empty({"category": "clinic", "location": "Oman"}, empties) is True
    assert icp._pruned_by_empty({"category": "clinic", "location": "Austin"}, empties) is False


def test_bluesky_backend_toggles_per_campaign(monkeypatch):
    monkeypatch.setenv("DISCOVERY_BACKENDS", "overpass,searxng")
    import vendor.leadgen.config as cfg

    cfg.load_schema.cache_clear()
    assert icp.enabled_backends({"use_bluesky": 0}) == ["overpass", "searxng"]
    assert "bluesky" in icp.enabled_backends({"use_bluesky": 1})


def test_backend_can_handle():
    # Overpass needs a place; searxng/bluesky search by keyword anywhere.
    assert icp.backend_can_handle("overpass", {"category": "x"}) is False
    assert icp.backend_can_handle("overpass", {"category": "x", "location": "Austin"}) is True
    assert icp.backend_can_handle("searxng", {"keyword": "godot"}) is True
    assert icp.backend_can_handle("bluesky", {"keyword": "godot"}) is True


def test_bluesky_skips_platform_domains():
    # A personal domain is emailable; platform links are not.
    assert bluesky._personal_domain("https://janedev.com/games") == "janedev.com"
    assert bluesky._personal_domain("https://janedev.itch.io") == ""
    assert bluesky._personal_domain("https://twitter.com/janedev") == ""


def test_bluesky_search_empty_without_credentials(monkeypatch):
    monkeypatch.delenv("BLUESKY_HANDLE", raising=False)
    monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)
    assert bluesky.search({"keyword": "godot plugin"}) == []


def test_candidate_sets_dedup_and_seed():
    pool = {
        "seed": {"category": "clinic", "location": "Austin", "keyword": "cosmetic"},
        "categories": ["clinic"],
        "locations": ["Austin", "Dallas"],
        "keywords": ["cosmetic"],
    }
    cands = icp._candidate_clause_sets(pool)
    keys = [clause_key(c) for c in cands]
    assert len(keys) == len(set(keys))  # no duplicate clause sets
    assert any(c.get("location") == "Dallas" for c in cands)
