"""app/db.py's mail_messages/mail_events CRUD: dedupe on repeated events,
aggregation counts for the Email tab's list/stats endpoints, and the
max_space_event_id() sync cursor."""


def test_add_mail_message_defaults(app_db):
    row = app_db.add_mail_message("composer", ["a@example.com"], "Subj")
    assert row["status"] == "pending"
    assert row["message_id"] is None
    assert row["to_addrs"] == '["a@example.com"]'


def test_update_mail_message_merges_fields(app_db):
    row = app_db.add_mail_message("composer", ["a@example.com"], "Subj")
    updated = app_db.update_mail_message(row["id"], status="sent", message_id="<x@y>")
    assert updated["status"] == "sent"
    assert updated["message_id"] == "<x@y>"
    assert updated["subject"] == "Subj"  # untouched fields survive


def test_get_mail_message_by_message_id(app_db):
    row = app_db.add_mail_message("composer", ["a@example.com"], "Subj")
    app_db.update_mail_message(row["id"], message_id="<abc@x>")
    found = app_db.get_mail_message_by_message_id("<abc@x>")
    assert found is not None
    assert found["id"] == row["id"]
    assert app_db.get_mail_message_by_message_id("<nope@x>") is None
    assert app_db.get_mail_message_by_message_id("") is None


def test_add_mail_event_dedupes_on_key(app_db):
    row = app_db.add_mail_message("composer", ["a@example.com"], "Subj")
    first = app_db.add_mail_event(row["id"], "open", "space:1")
    second = app_db.add_mail_event(row["id"], "open", "space:1")  # same dedupe_key
    assert first is not None
    assert second is None  # silently ignored, not a duplicate row

    stats = app_db.mail_tracking_stats()
    assert stats["opened"] == 1  # not double-counted


def test_max_space_event_id_tracks_the_cursor(app_db):
    row = app_db.add_mail_message("composer", ["a@example.com"], "Subj")
    assert app_db.max_space_event_id() == 0
    app_db.add_mail_event(row["id"], "open", "space:5", space_event_id=5)
    assert app_db.max_space_event_id() == 5
    app_db.add_mail_event(row["id"], "click", "space:3", space_event_id=3)
    assert app_db.max_space_event_id() == 5  # still the max, not the latest insert
    app_db.add_mail_event(row["id"], "click", "space:9", space_event_id=9)
    assert app_db.max_space_event_id() == 9


def test_bounce_events_do_not_carry_a_space_event_id(app_db):
    """Locally-detected bounces (IMAP/synchronous rejection) never came from the
    Space, so they must not perturb the sync cursor."""
    row = app_db.add_mail_message("composer", ["a@example.com"], "Subj")
    app_db.add_mail_event(row["id"], "bounce", "reject:1:a@example.com")
    assert app_db.max_space_event_id() == 0


def test_list_mail_messages_aggregates_counts_per_message(app_db):
    m1 = app_db.add_mail_message("composer", ["a@example.com"], "First")
    m2 = app_db.add_mail_message("leadgen", ["b@example.com"], "Second", leadgen_deal_id="deal-1")
    app_db.add_mail_event(m1["id"], "open", "space:1")
    app_db.add_mail_event(m1["id"], "open", "space:2")  # two opens on the same message
    app_db.add_mail_event(m1["id"], "click", "space:3")
    app_db.add_mail_event(m2["id"], "bounce", "reject:1:b")

    rows = {r["id"]: r for r in app_db.list_mail_messages()}
    assert rows[m1["id"]]["opens"] == 2
    assert rows[m1["id"]]["clicks"] == 1
    assert rows[m1["id"]]["bounces"] == 0
    assert rows[m2["id"]]["opens"] == 0
    assert rows[m2["id"]]["bounces"] == 1


def test_list_mail_messages_filters_by_source(app_db):
    app_db.add_mail_message("composer", ["a@example.com"], "First")
    app_db.add_mail_message("leadgen", ["b@example.com"], "Second")
    assert len(app_db.list_mail_messages()) == 2
    assert len(app_db.list_mail_messages(source="composer")) == 1
    assert len(app_db.list_mail_messages(source="leadgen")) == 1


def test_mail_tracking_stats_counts_distinct_messages_not_events(app_db):
    """A message opened 5 times must count once toward 'opened', matching how
    open rate is conventionally computed — not once per pixel hit."""
    m1 = app_db.add_mail_message("composer", ["a@example.com"], "First")
    for i in range(5):
        app_db.add_mail_event(m1["id"], "open", f"space:{i}")

    stats = app_db.mail_tracking_stats()
    assert stats["sent"] == 1
    assert stats["opened"] == 1
    assert stats["openRate"] == 1.0


def test_mail_tracking_stats_rate_math_with_mixed_messages(app_db):
    m1 = app_db.add_mail_message("composer", ["a@example.com"], "First")
    app_db.add_mail_message("composer", ["b@example.com"], "Second")  # never opened/clicked
    app_db.add_mail_event(m1["id"], "open", "space:1")

    stats = app_db.mail_tracking_stats()
    assert stats["sent"] == 2
    assert stats["opened"] == 1
    assert stats["openRate"] == 0.5
    assert stats["clickRate"] == 0.0
    assert stats["bounceRate"] == 0.0


def test_mail_tracking_stats_empty_db_has_zero_rates_not_division_error(app_db):
    stats = app_db.mail_tracking_stats()
    assert stats == {
        "sent": 0,
        "opened": 0,
        "clicked": 0,
        "bounced": 0,
        "openRate": 0.0,
        "clickRate": 0.0,
        "bounceRate": 0.0,
    }
