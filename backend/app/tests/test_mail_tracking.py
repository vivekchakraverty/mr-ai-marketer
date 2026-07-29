"""build_tracked_html's escaping/linkify order, and the prepare_send/
finalize_send round trip against a real (temp) DB. sync_from_space() itself
talks to the live tracking Space — covered by manual/live verification, not
here, to keep this suite network-free."""

from app.services import mail_tracking


def test_url_with_query_params_survives_the_redirect_intact():
    """Regression test for a real bug caught during development: escaping the
    body *before* locating URLs turns a '&' in any query string into '&amp;',
    which then gets baked into the redirect's u= param and corrupts the
    destination for every link with more than one query parameter."""
    body = "Deal here: https://example.com/page?a=1&b=2&c=3"
    html = mail_tracking.build_tracked_html(body, "TOK1")
    assert "u=https%3A%2F%2Fexample.com%2Fpage%3Fa%3D1%26b%3D2%26c%3D3" in html
    assert "&amp;b" not in html.split('u=')[1].split('"')[0]


def test_surrounding_text_is_escaped_exactly_once():
    body = "Hi <there> & welcome!"
    html = mail_tracking.build_tracked_html(body, "TOK2")
    assert "Hi &lt;there&gt; &amp; welcome!" in html
    assert "&amp;amp;" not in html  # not double-escaped


def test_newlines_become_br():
    html = mail_tracking.build_tracked_html("line one\nline two", "TOK3")
    assert "line one<br>" in html
    assert "line two" in html


def test_exactly_one_pixel_present():
    html = mail_tracking.build_tracked_html("no links here", "TOK4")
    assert html.count(f'{mail_tracking.SPACE_BASE_URL}/o/TOK4.gif') == 1


def test_multiple_links_all_rewritten():
    body = "First https://a.example/1 then https://b.example/2?x=y"
    html = mail_tracking.build_tracked_html(body, "TOK5")
    assert f"{mail_tracking.SPACE_BASE_URL}/c/TOK5?u=https%3A%2F%2Fa.example%2F1" in html
    assert f"{mail_tracking.SPACE_BASE_URL}/c/TOK5?u=https%3A%2F%2Fb.example%2F2%3Fx%3Dy" in html


def test_prepare_send_and_finalize_round_trip(app_db):
    mail_message_id, html = mail_tracking.prepare_send(
        "composer", ["a@example.com", "b@example.com"], "Subj", "hello https://x.com/y?p=1&q=2", cc_addrs=["c@example.com"]
    )
    row = app_db.get_mail_message(mail_message_id)
    assert row["status"] == "pending"
    assert row["source"] == "composer"
    assert row["subject"] == "Subj"

    mail_tracking.finalize_send(mail_message_id, "<real@x>", "sent")
    row2 = app_db.get_mail_message(mail_message_id)
    assert row2["status"] == "sent"
    assert row2["message_id"] == "<real@x>"
    assert row2["sent_at"] is not None


def test_finalize_send_records_error_on_failure(app_db):
    mail_message_id, _ = mail_tracking.prepare_send("composer", ["a@example.com"], "Subj", "body")
    mail_tracking.finalize_send(mail_message_id, None, "failed", "connection reset")
    row = app_db.get_mail_message(mail_message_id)
    assert row["status"] == "failed"
    assert row["error"] == "connection reset"
    assert row["sent_at"] is None


def test_record_bounce_writes_event(app_db):
    mail_message_id, _ = mail_tracking.prepare_send("leadgen", ["bad@nowhere.example"], "Subj", "body", leadgen_deal_id="deal-1")
    mail_tracking.finalize_send(mail_message_id, "<sent@x>", "sent")
    mail_tracking.record_bounce(mail_message_id, "bad@nowhere.example", "550 no such user", dedupe_key="reject:1:bad")
    stats = app_db.mail_tracking_stats()
    assert stats["bounced"] == 1


def test_prepare_for_leadgen_adapter_sets_source_and_deal_id(app_db):
    mail_message_id, html = mail_tracking.prepare_for_leadgen("lead@example.com", "Hi", "body text", "deal-42")
    row = app_db.get_mail_message(mail_message_id)
    assert row["source"] == "leadgen"
    assert row["leadgen_deal_id"] == "deal-42"


def test_finalize_for_leadgen_noop_when_mail_message_id_none(app_db):
    mail_tracking.finalize_for_leadgen(None, "<x@y>", "sent")  # must not raise


def test_record_bounce_for_leadgen_discards_unresolvable_message(app_db):
    """A bounce payload that can't be tied back to a message we sent (unknown
    Message-ID) must be silently discarded, not raise."""
    mail_tracking.record_bounce_for_leadgen({"dsn_original_message_id": "<unknown@nowhere>", "dsn_final_recipient": "x@y.com"})
    stats = app_db.mail_tracking_stats()
    assert stats["bounced"] == 0


def test_record_bounce_for_leadgen_resolves_and_records(app_db):
    mail_message_id, _ = mail_tracking.prepare_for_leadgen("lead@example.com", "Hi", "body", "deal-1")
    mail_tracking.finalize_for_leadgen(mail_message_id, "<sent-1@us>", "sent")
    mail_tracking.record_bounce_for_leadgen(
        {
            "dsn_original_message_id": "<sent-1@us>",
            "dsn_final_recipient": "rfc822; lead@example.com",
            "dsn_diagnostic": "smtp; 550 5.1.1",
            "message_id": "<bounce-1@theirmx>",
        }
    )
    stats = app_db.mail_tracking_stats(source="leadgen")
    assert stats["bounced"] == 1
