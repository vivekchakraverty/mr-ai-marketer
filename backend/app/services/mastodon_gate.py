"""The instance-rules gate — one enforcement point, shared by every Mastodon path.

Mastodon has no central terms of service. Each server publishes its own rules,
several require generative-AI use to be disclosed, and several ban commercial
promotion outright. So nothing in this app writes to a server, or reads other
people's posts off one into a corpus, until the user has been shown that server's
live rules and accepted them — and the acceptance is recorded as a hash of the
rule text, so an edit upstream re-closes the gate instead of silently carrying
consent forward to wording nobody has read.

This lives here, rather than in the router that needed it first, because two
features now depend on it (the Mastodon Post Creator and Engage) and a gate with
two implementations is a gate with two sets of bugs. It deliberately raises
MastodonError subclasses rather than HTTPException: the service layer has no
business picking status codes, and each router maps these to the code that fits
its own contract.
"""

from __future__ import annotations

from .. import db
from . import mastodon as masto
from .mastodon import MastodonError


class PolicyNotAccepted(MastodonError):
    """The instance's rules have not been accepted, or have changed since they were.

    Separate from MastodonError so a router can answer it with a conflict — this
    is not an upstream failure, it is a step the user has not taken yet.
    """


def load_policy(instance: str) -> masto.InstancePolicy:
    """Fetch an instance's rules and About page. Raises MastodonError if unreachable."""
    return masto.fetch_policy(instance)


def acceptance(host: str) -> dict | None:
    """The stored acknowledgement for a host, or None."""
    return db.get_mastodon_ack(host)


def is_accepted(policy: masto.InstancePolicy) -> bool:
    """Whether the stored acknowledgement matches the policy as it stands now."""
    ack = db.get_mastodon_ack(policy.info.host)
    return bool(ack and ack["policy_hash"] == policy.fingerprint)


def record(policy: masto.InstancePolicy) -> dict:
    """Store acceptance of this exact policy text, verbatim alongside its hash."""
    return db.record_mastodon_ack(
        policy.info.host,
        policy.fingerprint,
        {
            "instance": policy.info.host,
            "title": policy.info.title,
            "rules": [
                {"id": r.id, "text": r.text, "hint": r.hint} for r in policy.rules
            ],
            "extendedDescription": policy.extended_description,
        },
    )


def require_accepted(instance: str) -> masto.InstancePolicy:
    """Load the policy and refuse unless it is currently accepted.

    This is the gate. It re-fetches rather than trusting the stored hash, so a
    rule change upstream takes effect on the next action and not whenever someone
    happens to reopen the rules screen.
    """
    policy = load_policy(instance)
    ack = db.get_mastodon_ack(policy.info.host)
    if not ack:
        raise PolicyNotAccepted(
            f"Read {policy.info.host}'s rules and accept them before acting on it."
        )
    if ack["policy_hash"] != policy.fingerprint:
        raise PolicyNotAccepted(
            f"{policy.info.host} has changed its rules since you accepted them. "
            f"Review the new version before doing anything else there."
        )
    return policy


def policy_notes(policy: masto.InstancePolicy) -> list[str]:
    """What this instance asks of an AI-assisted or automated account, in plain terms.

    Every note is triggered by text the instance actually published — no generic
    best-practice padding. If a server says nothing about automation, we say
    nothing about it either, rather than inventing a requirement it never asked
    for. Both the Post Creator's compliance panel and Engage's terms panel read
    from here, so the two screens can never disagree about what a server wants.
    """
    haystack = " ".join(
        [r.text + " " + r.hint for r in policy.rules] + [policy.extended_description]
    ).lower()

    notes: list[str] = []
    if "generative ai" in haystack or "generative-ai" in haystack or "ai must be" in haystack:
        notes.append(
            f"{policy.info.host} requires generative-AI use to be disclosed — keep the "
            f"disclosure line, or write your own before posting."
        )
    if "solely post ai" in haystack or "only ai" in haystack:
        notes.append(
            "This instance forbids accounts that post *only* AI-generated content. "
            "This tool has to sit alongside your own posting, not replace it."
        )
    if any(t in haystack for t in ("advertis", "promot", "commercial", "spam", "seo")):
        notes.append(
            "This instance restricts commercial promotion. Posts that read as "
            "advertising are a rules problem no disclosure fixes."
        )
    if "bot" in haystack or "automat" in haystack:
        notes.append(
            "Automated posting here is subject to rules — check whether yours needs "
            "the bot flag set on your profile, or unlisted visibility."
        )
    if any(
        t in haystack
        for t in ("not for personal use", "corporate account", "unofficial corporate")
    ):
        notes.append(
            f"{policy.info.host} restricts non-personal (company, agency, project, bot) "
            f"accounts — several are invite-only and unapproved ones get suspended. "
            f"Confirm your account type is permitted before posting as a brand."
        )
    return notes
