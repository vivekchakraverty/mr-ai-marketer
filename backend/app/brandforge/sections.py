"""Document structure, brand catalog constants, and the student prompt.

SECTION_SPECS and PHASES are copied verbatim from the BrandForge Space
(src/generate.py + src/config.py). STUDENT_SYSTEM and build_student_messages
are copied from the fine-tune's training-time student prompt
(finetune/teacher_generate.py) — the model was trained on exactly this shape
with retrieval discarded, so inference must reproduce it identically.
"""
from __future__ import annotations

import json

# --- Brand catalog (src/config.py) -----------------------------------------
BRAND_CATEGORIES: list[str] = [
    "Technology/SaaS/IT services",
    "E-commerce/D2C",
    "Food & beverage",
    "Fashion/beauty/lifestyle",
    "Health/wellness",
    "Finance/professional services",
    "Education/edtech",
    "Entertainment/gaming/media",
    "Real estate/construction/local services",
    "Travel/hospitality",
]

CHANNELS: list[str] = [
    "website",
    "LinkedIn",
    "Instagram",
    "X",
    "YouTube",
    "Facebook",
    "offline",
]

BUSINESS_MODELS: list[str] = ["B2B", "B2C", "Both"]

# The five personality sliders (1=left … 7=right), surfaced to the UI so labels
# stay in one place.
PERSONALITY_SLIDERS: list[dict[str, str]] = [
    {"key": "formal_casual", "left": "Formal", "right": "Casual"},
    {"key": "serious_playful", "left": "Serious", "right": "Playful"},
    {"key": "authoritative_friendly", "left": "Authoritative", "right": "Friendly"},
    {"key": "classic_innovative", "left": "Classic", "right": "Innovative"},
    {"key": "corporate_rebellious", "left": "Corporate", "right": "Rebellious"},
]

# The 12 document sections, grouped into the 5 phases of a branding engagement.
PHASES: dict[str, list[str]] = {
    "Strategy": [
        "Brand Core",
        "Positioning Statement",
        "Audience Personas",
        "Competitive Frame",
        "Archetype Expression",
    ],
    "Verbal Identity": [
        "Brand Personality & Voice",
        "Messaging Pillars",
        "Tagline Territory",
    ],
    "Visual Identity": [
        "Visual Direction Brief",
    ],
    "Experience": [
        "Channel Voice Adaptation",
        "Brand Guardrails",
    ],
    "Growth": [
        "90-Day Activation Checklist",
    ],
}

SECTION_NAMES: list[str] = [name for names in PHASES.values() for name in names]

PHASE_OF: dict[str, str] = {name: phase for phase, names in PHASES.items() for name in names}

# --- Section instructions (src/generate.py SECTION_SPECS) ------------------
SECTION_SPECS: dict[str, str] = {
    "Brand Core": (
        "Write the Brand Core section: purpose, mission, vision, and 4-6 core values. "
        "For EACH value, give a short 'this means we' line and a contrasting 'this means we never' line. "
        "All statements must be specific to this brand, not generic platitudes."
    ),
    "Positioning Statement": (
        "Write the Positioning Statement using April Dunford's structure: competitive alternatives -> "
        "unique attributes -> value themes -> target segment -> market category. Then add a single-sentence "
        "classic-formula version ('For [target] who [need], [brand] is the [category] that [differentiator]')."
    ),
    "Archetype Expression": (
        "The client has already chosen their brand archetype (see brand_archetype and secondary_archetype in the "
        "intake — do NOT propose a different one). Write the Archetype Expression section: briefly explain the "
        "emotional promise of the chosen archetype, how the secondary archetype (if any) shades it, and then give "
        "3 concrete 'how it shows up' examples specific to this brand — one each for voice, visual cues, and "
        "messaging. Flag any tension between the chosen archetype and the personality sliders, with advice on "
        "resolving it."
    ),
    "Audience Personas": (
        "Write 2-3 audience personas. For each: jobs-to-be-done, pains, gains, watering holes (where they spend "
        "attention), and an objection map (their top objections and how the brand should answer each)."
    ),
    "Competitive Frame": (
        "Write the Competitive Frame: position against each named competitor, state a whitespace claim, and "
        "list points of parity vs points of difference."
    ),
    "Brand Personality & Voice": (
        "Define 3-4 voice traits. For each: do's, don'ts, and an in-voice vs off-voice rewrite of the SAME "
        "example message. Also set the tone range on Nielsen Norman's four dimensions (funny-serious, "
        "formal-casual, respectful-irreverent, enthusiastic-matter-of-fact)."
    ),
    "Messaging Pillars": (
        "Write 3-5 messaging pillars. For each: a core claim, 2-3 proof points, an example headline, and an "
        "example social hook, all freshly written for this brand. Proof points must be drawn strictly from "
        "facts already in the intake (differentiation hypothesis, founding story, existing assets, stated "
        "goals) — where the intake lacks proof for a claim, write 'TBD — needs founder input' for that proof "
        "point instead of inventing statistics, certifications, or customer results."
    ),
    "Tagline Territory": (
        "Write 5-8 tagline candidates spanning genuinely distinct strategic territories (not synonyms of each "
        "other). Annotate each with which territory/strategy it represents and why it fits."
    ),
    "Visual Direction Brief": (
        "Write a Visual Direction Brief: overall mood direction; 2-3 palette directions each with a color "
        "psychology rationale; typography personality; imagery do's and don'ts. Respect any existing assets "
        "described in the intake instead of contradicting them. END the section with the recommended palette "
        "as a fenced code block tagged `palette` containing ONLY a JSON array of 4-6 swatches, e.g.\n"
        "```palette\n"
        '[{"hex": "#B87333", "name": "Roasted Copper", "rationale": "warmth and craft"}]\n'
        "```\n"
        "Every hex value must be a 6-digit hex color. No text after the block."
    ),
    "Channel Voice Adaptation": (
        "For EACH selected channel, specify: length norms, formality shift from the base voice, emoji policy, "
        "hashtag policy, and CTA style. Render this as a markdown table with one row per channel."
    ),
    "Brand Guardrails": (
        "Write Brand Guardrails: banned words/claims, sensitive topics to avoid or handle carefully, "
        "competitor-mention policy, and a one-paragraph crisis-tone note."
    ),
    "90-Day Activation Checklist": (
        "Write a 90-Day Activation Checklist: a prioritized rollout plan explicitly mapped to the stated "
        "12-month business goal, broken into 0-30 / 31-60 / 61-90 day horizons."
    ),
}

# --- Student prompt (finetune/teacher_generate.py) -------------------------
# The exact prompt the fine-tuned model was trained on: task instructions +
# intake JSON, NO retrieved chunks, NO prior sections.
STUDENT_SYSTEM = (
    "You are a senior brand strategist writing one deliverable for a client's Brand Document. "
    "Be concrete, specific, and free of marketing filler. Output clean markdown for a single "
    "deliverable (do not repeat the whole document). Never invent brand facts beyond what the "
    "intake provides — if information needed for a claim is missing, write 'TBD — needs founder "
    "input' instead of inventing it."
)


def build_student_messages(brief: dict, name: str, instructions: str) -> list[dict]:
    """Reconstruct the training-time student prompt for one deliverable.

    ``brief`` is the intake as a plain dict (BrandIntake.model_dump()), serialized
    the same way the training pairs were (indent=2, ensure_ascii=False)."""
    intake_json = json.dumps(brief, indent=2, ensure_ascii=False)
    user = (
        f"DELIVERABLE: {name}\n\n"
        f"INSTRUCTIONS:\n{instructions}\n\n"
        f"BRAND INTAKE (ground truth — do not contradict, do not invent beyond this):\n{intake_json}\n\n"
        f'Write only the "{name}" deliverable now, in markdown.'
    )
    return [
        {"role": "system", "content": STUDENT_SYSTEM},
        {"role": "user", "content": user},
    ]
