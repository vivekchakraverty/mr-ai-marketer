"""Bounce classification (email/tracking.py::is_bounce) and the fail-soft
tracking DI (set_tracking_hooks / prepare / finalize / handle_possible_bounce),
plus the two call sites that actually use them: follow_up._ingest_replies
(intercepting bounces before reply-matching) and tasks.common.deliver_draft
(the one send path)."""

import pytest

from vendor.leadgen.email import inbox, sender, tracking
from vendor.leadgen.tasks import common, follow_up

# A fully-formed DSN-shaped reply, as email/inbox.py's fetch_recent() would
# produce it after the dsn_* field extraction.
_DSN_REPLY = {
    "from": "Mail Delivery Subsystem <mailer-daemon@theirmx.example>",
    "subject": "Undelivered Mail Returned to Sender",
    "message_id": "<bounce-1@theirmx.example>",
    "in_reply_to": "",
    "references": "",
    "body": "delivery failed",
    "is_report": True,
    "dsn_action": "failed",
    "dsn_final_recipient": "rfc822; bad@nowhere.example",
    "dsn_diagnostic": "smtp; 550 5.1.1 No such user",
    "dsn_original_message_id": "<sent-1@us.example>",
}


@pytest.fixture(autouse=True)
def _reset_tracking_hooks():
    """set_tracking_hooks mutates module-level globals — clear them before and
    after every test so one test's fakes can't leak into another."""
    tracking.set_tracking_hooks(prepare=None, finalize=None, bounce=None)
    tracking._prepare = None
    tracking._finalize = None
    tracking._bounce = None
    yield
    tracking._prepare = None
    tracking._finalize = None
    tracking._bounce = None


# --- is_bounce classification -----------------------------------------------


def test_is_bounce_true_for_well_formed_dsn():
    is_b, _ = tracking.is_bounce(_DSN_REPLY)
    assert is_b is True


def test_is_bounce_false_for_delayed_dsn():
    """'delayed' is a transient retry notice, not a bounce — only 'failed' counts."""
    delayed = {**_DSN_REPLY, "dsn_action": "delayed"}
    is_b, _ = tracking.is_bounce(delayed)
    assert is_b is False


def test_is_bounce_true_for_mailer_daemon_heuristic_without_report_flag():
    """A loosely-formatted bounce from an MTA that doesn't send a spec-compliant
    multipart/report — falls back to the From-address heuristic."""
    reply = {"from": "postmaster@theirmx.example", "subject": "failure notice", "is_report": False, "dsn_action": ""}
    is_b, _ = tracking.is_bounce(reply)
    assert is_b is True


def test_is_bounce_true_for_subject_heuristic():
    reply = {"from": "noreply@theirmx.example", "subject": "Mail delivery failed", "is_report": False, "dsn_action": ""}
    is_b, _ = tracking.is_bounce(reply)
    assert is_b is True


def test_is_bounce_false_for_normal_reply():
    reply = {"from": "jane@realcompany.example", "subject": "Re: hello", "is_report": False, "dsn_action": ""}
    is_b, _ = tracking.is_bounce(reply)
    assert is_b is False


def test_is_bounce_false_for_out_of_office():
    reply = {"from": "jane@realcompany.example", "subject": "Automatic reply: Out of office", "is_report": False, "dsn_action": ""}
    is_b, _ = tracking.is_bounce(reply)
    assert is_b is False


# --- fail-soft DI ------------------------------------------------------------


def test_prepare_returns_none_when_unwired():
    assert tracking.prepare("a@b.com", "subj", "body", "deal-1") is None


def test_prepare_returns_none_when_hook_raises():
    tracking.set_tracking_hooks(prepare=lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert tracking.prepare("a@b.com", "subj", "body", "deal-1") is None


def test_prepare_passes_through_hook_result():
    tracking.set_tracking_hooks(prepare=lambda to, subj, body, deal_id: ("mmid-1", f"<html>{body}</html>"))
    result = tracking.prepare("a@b.com", "subj", "hello", "deal-1")
    assert result == ("mmid-1", "<html>hello</html>")


def test_finalize_is_noop_when_unwired_or_id_is_none():
    tracking.finalize(None, "msgid", "sent")  # mail_message_id None -> no-op even with a hook
    calls = []
    tracking.set_tracking_hooks(finalize=lambda *a: calls.append(a))
    tracking.finalize(None, "msgid", "sent")
    assert calls == []


def test_finalize_calls_hook_when_wired():
    calls = []
    tracking.set_tracking_hooks(finalize=lambda mmid, msgid, status, error=None: calls.append((mmid, msgid, status, error)))
    tracking.finalize("mmid-1", "<real@x>", "sent")
    assert calls == [("mmid-1", "<real@x>", "sent", None)]


def test_finalize_swallows_raising_hook():
    tracking.set_tracking_hooks(finalize=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    tracking.finalize("mmid-1", "<real@x>", "sent")  # must not raise


def test_handle_possible_bounce_true_without_hook_wired():
    assert tracking.handle_possible_bounce(_DSN_REPLY) is True


def test_handle_possible_bounce_false_for_normal_reply():
    reply = {"from": "jane@realcompany.example", "subject": "Re: hello", "is_report": False, "dsn_action": ""}
    assert tracking.handle_possible_bounce(reply) is False


def test_handle_possible_bounce_calls_hook_with_merged_payload():
    captured = []
    tracking.set_tracking_hooks(bounce=lambda payload: captured.append(payload))
    handled = tracking.handle_possible_bounce(_DSN_REPLY)
    assert handled is True
    assert len(captured) == 1
    assert captured[0]["dsn_original_message_id"] == "<sent-1@us.example>"


def test_handle_possible_bounce_swallows_raising_hook():
    tracking.set_tracking_hooks(bounce=lambda payload: (_ for _ in ()).throw(RuntimeError("boom")))
    assert tracking.handle_possible_bounce(_DSN_REPLY) is True  # still correctly identified


# --- follow_up._ingest_replies intercepts bounces before reply-matching -----


def test_ingest_replies_intercepts_bounce_and_does_not_create_chat_message(lg_db, monkeypatch):
    c = lg_db.create_campaign("c", "p")
    lead = lg_db.upsert_lead(c["id"], domain="nowhere.example", company="Nowhere Co", email="bad@nowhere.example", profile_text="x")
    deal = lg_db.create_deal(c["id"], lead["id"])
    lg_db.add_chat_message(deal["id"], "out", "Hi", "body", message_id="<sent-1@us.example>")

    monkeypatch.setattr(inbox, "fetch_recent", lambda limit=30: [_DSN_REPLY])
    captured = []
    tracking.set_tracking_hooks(bounce=lambda payload: captured.append(payload))

    acted = follow_up._ingest_replies(c["id"])
    assert acted is False  # a bounce is not "acted on" the way a real reply is
    assert len(captured) == 1
    # No chat message was recorded for the bounce and the deal wasn't touched —
    # the existing reply-matching path was correctly skipped, not exercised.
    thread = lg_db.thread_for_deal(deal["id"])
    assert len(thread) == 1  # only the original outbound message
    assert lg_db.get_deal(deal["id"])["state"] != lg_db.STATE_REPLIED


def test_unsubscribe_reply_still_works_unaffected_by_bounce_check(lg_db, monkeypatch):
    """Regression guard: a genuine human reply must still be ingested normally —
    the new bounce interception must not swallow real replies."""
    c = lg_db.create_campaign("c", "p")
    lead = lg_db.upsert_lead(c["id"], domain="b.com", company="B", email="them@b.com", profile_text="x")
    deal = lg_db.create_deal(c["id"], lead["id"])
    lg_db.add_chat_message(deal["id"], "out", "Hi", "body", message_id="<sent-1@us>")

    monkeypatch.setattr(
        inbox,
        "fetch_recent",
        lambda limit=30: [
            {
                "from": "them@b.com",
                "subject": "please stop",
                "message_id": "<reply-1@them>",
                "in_reply_to": "<sent-1@us>",
                "references": "<sent-1@us>",
                "body": "Please unsubscribe me from this list.",
                "is_report": False,
                "dsn_action": "",
            }
        ],
    )

    acted = follow_up._ingest_replies(c["id"])
    assert acted is True
    assert lg_db.is_suppressed("them@b.com") is True
    assert lg_db.get_deal(deal["id"])["state"] == lg_db.STATE_COMPLETED


# --- tasks.common.deliver_draft: tracking wired into the one send path -----


def test_deliver_draft_sends_fine_with_no_tracking_hooks_wired(lg_db, monkeypatch):
    """Fail-soft top to bottom: an entirely unwired tracking module must never
    block or break a real send."""
    c = lg_db.create_campaign("c", "p")
    lead = lg_db.upsert_lead(c["id"], domain="b.com", company="B", email="them@b.com", profile_text="x")
    deal = lg_db.create_deal(c["id"], lead["id"])
    draft = lg_db.add_draft(deal["id"], "opener", "Hello", "hi there")

    captured_html = []
    monkeypatch.setattr(
        sender, "send", lambda to, subj, body, in_reply_to=None, references=None, html_body=None: (
            captured_html.append(html_body) or "<sent@x>"
        )
    )

    sent, detail = common.deliver_draft(draft["id"])
    assert sent is True
    assert captured_html == [None]  # no tracking wired -> no HTML alternative built


def test_deliver_draft_threads_tracking_html_body_through_on_success(lg_db, monkeypatch):
    c = lg_db.create_campaign("c", "p")
    lead = lg_db.upsert_lead(c["id"], domain="b.com", company="B", email="them@b.com", profile_text="x")
    deal = lg_db.create_deal(c["id"], lead["id"])
    draft = lg_db.add_draft(deal["id"], "opener", "Hello", "hi there")

    finalized = []
    tracking.set_tracking_hooks(
        prepare=lambda to, subj, body, deal_id: ("mmid-1", "<html>hi there</html>"),
        finalize=lambda mmid, msgid, status, error=None: finalized.append((mmid, msgid, status, error)),
    )
    captured_html = []
    monkeypatch.setattr(
        sender, "send", lambda to, subj, body, in_reply_to=None, references=None, html_body=None: (
            captured_html.append(html_body) or "<sent@x>"
        )
    )

    sent, detail = common.deliver_draft(draft["id"])
    assert sent is True
    assert captured_html == ["<html>hi there</html>"]
    assert finalized == [("mmid-1", "<sent@x>", "sent", None)]


def test_deliver_draft_finalizes_as_bounced_on_refused_recipient(lg_db, monkeypatch):
    c = lg_db.create_campaign("c", "p")
    lead = lg_db.upsert_lead(c["id"], domain="b.com", company="B", email="bad@nowhere.example", profile_text="x")
    deal = lg_db.create_deal(c["id"], lead["id"])
    draft = lg_db.add_draft(deal["id"], "opener", "Hello", "hi there")

    finalized = []
    tracking.set_tracking_hooks(
        prepare=lambda to, subj, body, deal_id: ("mmid-1", "<html>hi there</html>"),
        finalize=lambda mmid, msgid, status, error=None: finalized.append((mmid, msgid, status, error)),
    )

    def _refused(to, subj, body, in_reply_to=None, references=None, html_body=None):
        raise sender.SendError("550 no such user", refused={"bad@nowhere.example": (550, b"no such user")})

    monkeypatch.setattr(sender, "send", _refused)

    sent, detail = common.deliver_draft(draft["id"])
    assert sent is False
    assert finalized == [("mmid-1", None, "bounced", "550 no such user")]


def test_deliver_draft_finalizes_as_failed_on_generic_send_error(lg_db, monkeypatch):
    c = lg_db.create_campaign("c", "p")
    lead = lg_db.upsert_lead(c["id"], domain="b.com", company="B", email="them@b.com", profile_text="x")
    deal = lg_db.create_deal(c["id"], lead["id"])
    draft = lg_db.add_draft(deal["id"], "opener", "Hello", "hi there")

    finalized = []
    tracking.set_tracking_hooks(
        prepare=lambda to, subj, body, deal_id: ("mmid-1", "<html>hi there</html>"),
        finalize=lambda mmid, msgid, status, error=None: finalized.append((mmid, msgid, status, error)),
    )

    def _generic_failure(to, subj, body, in_reply_to=None, references=None, html_body=None):
        raise sender.SendError("connection reset")  # no .refused -> generic failure, not a bounce

    monkeypatch.setattr(sender, "send", _generic_failure)

    sent, detail = common.deliver_draft(draft["id"])
    assert sent is False
    assert finalized == [("mmid-1", None, "failed", "connection reset")]
