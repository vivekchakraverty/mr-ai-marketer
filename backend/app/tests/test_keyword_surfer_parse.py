"""The standalone collector's parser tests, ported alongside the parser itself.

These are the vectors that pinned the JavaScript original's behaviour. Keeping them means
the port can be shown to agree with the tool it came from, rather than merely looking like
it does.
"""
from __future__ import annotations

from app.services.keyword_surfer_parse import (
    parse_compact_number,
    parse_currency,
    parse_percentage,
    parse_snapshot,
)


def test_parses_compact_search_volume_formats():
    assert parse_compact_number("12,100") == 12_100
    assert parse_compact_number("1.2K/mo") == 1_200
    assert parse_compact_number("2.5M searches") == 2_500_000
    # A percentage and a price are numbers too; reading either as a volume would produce a
    # plausible-looking row that is simply wrong.
    assert parse_compact_number("87%") is None
    assert parse_compact_number("$2.40") is None


def test_parses_exact_and_suggestion_rows_from_a_rendered_snapshot():
    parsed = parse_snapshot(
        {
            "rootFound": True,
            "rootSelector": "aside#surfer-panel",
            "rootText": "Keyword Surfer United States Search volume 12,100 Keyword ideas",
            "mainKeywordMetrics": ["12,100", "3.53"],
            "countryLabels": ["United States"],
            "markerCount": 4,
            "rows": [
                {"texts": ["email marketing", "12,100", "$3.20"]},
                {"texts": ["email campaign ideas", "2.4K/mo", "$1.10", "72%"]},
                {"texts": ["newsletter examples", "8,100", "$0.80", "54%"]},
            ],
        },
        "email marketing",
    )

    assert parsed["loaded"] is True
    assert parsed["volume"] == 12_100
    assert parsed["cpc"] == 3.2
    assert parsed["countryLabel"] == "United States"
    # Sorted by volume, descending.
    assert [row["keyword"] for row in parsed["suggestions"]] == [
        "newsletter examples",
        "email campaign ideas",
    ]
    assert parsed["suggestions"][1]["similarity"] == 72


def test_reads_exact_volume_and_cpc_from_the_surfer_enhanced_search_bar():
    """The queried term often isn't a panel row at all — its figures sit in the search bar."""
    parsed = parse_snapshot(
        {
            "rootFound": True,
            "rootText": "Keyword ideas Keyword Overlap Volume",
            "mainKeywordMetrics": ["12,100", "3.53"],
            "rows": [{"texts": ["ai games", "100%", "12,100"]}],
        },
        "ai game",
    )

    assert parsed["volume"] == 12_100
    assert parsed["cpc"] == 3.53
    assert parsed["cpcDisplay"] == "3.53"


def test_uses_the_search_volume_label_when_the_exact_keyword_is_not_a_row():
    parsed = parse_snapshot(
        {
            "rootFound": True,
            "rootText": "Keyword Surfer Search volume: 880 /mo Keyword ideas",
            "rows": [{"texts": ["technical seo checklist", "320", "65%"]}],
        },
        "seo audit checklist",
    )

    assert parsed["volume"] == 880
    assert len(parsed["suggestions"]) == 1
    assert parsed["loaded"] is True


def test_does_not_claim_data_loaded_without_a_surfer_root():
    """No panel means no data — never report an empty page as a successful zero."""
    parsed = parse_snapshot({"rootFound": False, "rootText": "", "rows": []}, "keyword")

    assert parsed["loaded"] is False
    assert parsed["volume"] is None


# --- beyond the original's vectors -------------------------------------------------

def test_european_thousands_grouping():
    """1.234 is a thousand-and-something in de/es/it, not 1.234 of anything."""
    assert parse_compact_number("1.234") == 1234
    assert parse_compact_number("12.345.678") == 12_345_678


def test_currency_needs_a_symbol_or_code():
    assert parse_currency("$3.20")["amount"] == 3.2
    assert parse_currency("3.20 USD")["amount"] == 3.2
    assert parse_currency("3.20") is None  # a bare number is a volume, not a price
    assert parse_percentage("72%") == 72


def test_rows_without_any_metric_are_discarded():
    """Navigation and headings live in the panel too; only metric-bearing rows are data."""
    parsed = parse_snapshot(
        {
            "rootFound": True,
            "rootText": "Keyword ideas",
            "rows": [
                {"texts": ["Select all", "Export"]},
                {"texts": ["real keyword", "1,000"]},
            ],
        },
        "seed",
    )
    assert [row["keyword"] for row in parsed["suggestions"]] == ["real keyword"]


def test_restores_cpc_from_surfer_cache_when_current_ideas_table_omits_it():
    """Surfer 6.3 renders Keyword/Overlap/Volume but keeps CPC in its loaded record."""
    parsed = parse_snapshot(
        {
            "rootFound": True,
            "rootText": "Keyword ideas Keyword Overlap Volume",
            "rows": [
                {"texts": ["define allegory", "50%", "60,500"]},
                {"texts": ["meaning of allegory", "50%", "60,500"]},
            ],
            "cachedKeywordMetrics": [
                {"keyword": "define allegory", "volume": 60_500, "cpc": 1.84, "country": "us"},
                {"keyword": "meaning of allegory", "volume": 60_500, "cpc": 0, "country": "us"},
                # An old cached row must not appear unless it is present in this DOM snapshot.
                {"keyword": "unrelated old search", "volume": 9_900, "cpc": 9.99, "country": "us"},
            ],
        },
        "allegorical sci-fi novel",
    )

    assert [row["keyword"] for row in parsed["suggestions"]] == [
        "define allegory",
        "meaning of allegory",
    ]
    assert parsed["suggestions"][0]["cpc"] == 1.84
    assert parsed["suggestions"][0]["cpcDisplay"] == "$1.84"
    assert parsed["suggestions"][1]["cpc"] == 0
    assert parsed["suggestions"][1]["cpcDisplay"] == "$0.00"


def test_restores_exact_keyword_metrics_from_surfer_cache():
    parsed = parse_snapshot(
        {
            "rootFound": True,
            "rootText": "Keyword ideas Keyword Overlap Volume",
            "rows": [{"texts": ["related idea", "42%", "1,000"]}],
            "cachedKeywordMetrics": [
                {"keyword": "seed keyword", "volume": 720, "cpc": 2.4, "country": "us"},
            ],
        },
        "seed keyword",
    )

    assert parsed["volume"] == 720
    assert parsed["cpc"] == 2.4
    assert parsed["cpcDisplay"] == "$2.40"
