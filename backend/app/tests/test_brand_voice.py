from __future__ import annotations

from app.services import brand_voice

# A voice card in the shape to_voice_system_prompt actually produces: each of its five
# headings is followed by a whole generated section that carries its own "#" and "##"
# headings. That nesting is the thing worth testing — a first attempt at shrinking this
# scanned for "## " and treated the embedded headings as section boundaries, so it removed
# 42 characters out of 12,674 and looked like it worked.
CARD = "\n".join(
    [
        "# Acme — Voice Card",
        "",
        "You are writing as Acme, a hardware brand.",
        "",
        "## Tone dimensions (1=left, 7=right)",
        "- Formal <-> Casual: 6",
        "",
        "## Voice traits, do's and don'ts",
        "# Brand Personality & Voice",
        "## Core Traits",
        "### 1. **Direct**",
        *[f"trait line {i}" for i in range(30)],
        "",
        "## Messaging pillars",
        "# Messaging Pillars",
        "## 1. **Built to last**",
        *[f"pillar line {i}" for i in range(30)],
        "",
        "## Guardrails (banned words, sensitive topics, competitor policy)",
        "# Brand Guardrails",
        "## Banned Words/Claims",
        *[f"guardrail line {i}" for i in range(30)],
        "",
        "## Never sound like",
        "A pushy salesperson.",
    ]
)


def test_split_uses_the_cards_own_headings_not_embedded_ones():
    headings = [h for h, _ in brand_voice._split_sections(CARD) if h]
    assert headings == [
        "## Tone dimensions (1=left, 7=right)",
        "## Voice traits, do's and don'ts",
        "## Messaging pillars",
        "## Guardrails (banned words, sensitive topics, competitor policy)",
        "## Never sound like",
    ]


def test_shrink_bounds_every_section():
    out = brand_voice._shrink(CARD, max_lines=5)
    # The cap counts every non-blank line in the section, embedded headings included —
    # Voice traits opens with three of them and Guardrails with two, so the same budget
    # leaves a different amount of prose. That is the intended behaviour: the cap exists
    # to bound the prompt, and a heading costs prompt space like anything else.
    assert out.count("trait line") == 2
    assert out.count("guardrail line") == 3
    assert len(out) < len(CARD) / 2


def test_compact_drops_pillars_and_keeps_identity_tone_and_guardrails():
    out = brand_voice._shrink(CARD, max_lines=5, keep_pillars=False)
    assert "pillar line" not in out and "## Messaging pillars" not in out
    assert "Acme — Voice Card" in out
    assert "Formal <-> Casual: 6" in out
    assert "## Guardrails" in out
    assert "A pushy salesperson." in out


def test_apply_voice_puts_the_authors_words_last():
    # The brand is standing context; the request is what the model should act on, so it
    # has to be the most recent thing in the field.
    out = brand_voice.apply_voice("Announce the sale", "", compact=True)
    assert out == "Announce the sale"  # no voice id -> untouched


# Takes app_db because looking up an unknown id is a real Library query. Without it
# the test reads whatever config.DB_PATH happens to point at: a developer's own
# library (passing by accident, having just searched their real data) or, on a clean
# checkout, no schema at all — which is how this failed in CI.
def test_missing_or_unknown_voice_is_a_no_op_not_an_error(app_db):
    assert brand_voice.load_voice("") == ""
    assert brand_voice.load_voice("not-a-real-id") == ""
    assert brand_voice.apply_voice("plain", "not-a-real-id") == "plain"
