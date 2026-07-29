"""Email pattern finder: ranking, known-pattern boosting, generic fallback."""

from vendor.leadgen.email import finder


def test_ranked_candidates_for_named_contact():
    cands = finder.candidates("Jane Smith", "acme.com")
    assert cands[0] == "jane.smith@acme.com"  # most common pattern first
    assert "jsmith@acme.com" in cands
    assert all(c.endswith("@acme.com") for c in cands)
    assert len(set(cands)) == len(cands)  # no duplicates


def test_known_pattern_is_floated_to_front():
    # Domain already confirmed to use flast@; that pattern should now rank first.
    cands = finder.candidates("Jane Smith", "acme.com", known_localparts=["jsmith"])
    assert cands[0] == "jsmith@acme.com"


def test_generic_fallback_without_a_name():
    cands = finder.candidates(None, "acme.com")
    assert cands[0] == "info@acme.com"
    assert "contact@acme.com" in cands


def test_no_domain_yields_nothing():
    assert finder.candidates("Jane Smith", "") == []


def test_single_name_still_produces_candidates():
    cands = finder.candidates("Madonna", "label.com")
    assert "madonna@label.com" in cands
