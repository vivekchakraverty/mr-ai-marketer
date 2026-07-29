"""Suppression enforcement + daily-cap send gating — the safety guarantees."""

from vendor.leadgen.tasks import common


def _lead_deal(db, campaign_id, email="a@b.com"):
    lead = db.upsert_lead(campaign_id, domain="b.com", company="B", email=email, profile_text="x")
    deal = db.create_deal(campaign_id, lead["id"])
    return lead, deal


def test_suppression_enforced(lg_db):
    lg_db.add_suppression("Blocked@Example.com", "test")
    assert lg_db.is_suppressed("blocked@example.com") is True  # case-insensitive
    assert lg_db.is_suppressed("someone@else.com") is False


def test_deliver_refuses_suppressed_without_sending(lg_db):
    c = lg_db.create_campaign("c", "p")
    lead, deal = _lead_deal(lg_db, c["id"], email="blocked@b.com")
    lg_db.add_suppression("blocked@b.com", "opted out")
    draft = lg_db.add_draft(deal["id"], "opener", "Hi", "Body")

    sent, detail = common.deliver_draft(draft["id"])
    assert sent is False
    assert "suppress" in detail.lower()
    # The deal is closed out and the draft discarded — nothing left in the queue.
    assert lg_db.get_deal(deal["id"])["state"] == lg_db.STATE_FAILED
    assert lg_db.get_draft(draft["id"])["status"] == "discarded"


def test_send_headroom_tracks_daily_cap(lg_db):
    c = lg_db.create_campaign("c", "p", daily_cap=3)
    campaign = lg_db.get_campaign(c["id"])
    assert common.send_headroom(campaign) == 3

    lead, deal = _lead_deal(lg_db, c["id"])
    lg_db.add_chat_message(deal["id"], "out", "s", "b", message_id="<m1@x>")
    lg_db.add_chat_message(deal["id"], "out", "s", "b", message_id="<m2@x>")
    assert common.send_headroom(campaign) == 1  # 3 cap - 2 sent today


def test_find_headroom_excludes_in_flight(lg_db):
    c = lg_db.create_campaign("c", "p", daily_cap=5)
    campaign = lg_db.get_campaign(c["id"])
    # Two deals already holding capacity (ready to email) shrink find headroom.
    for _ in range(2):
        lead = lg_db.upsert_lead(c["id"], domain=f"{_}x.com", company="X", profile_text="x")
        deal = lg_db.create_deal(c["id"], lead["id"])
        lg_db.update_deal(deal["id"], state=lg_db.STATE_READY_TO_EMAIL)
    assert common.find_headroom(campaign) == 3  # 5 - 0 sent - 2 in flight


def test_auto_send_respects_active_hours(lg_db, monkeypatch):
    c = lg_db.create_campaign("c", "p")
    campaign = lg_db.get_campaign(c["id"])
    monkeypatch.setattr(common, "within_active_hours", lambda camp, now=None: False)
    # Even with a pending draft, nothing sends outside the window.
    lead, deal = _lead_deal(lg_db, c["id"])
    lg_db.add_draft(deal["id"], "opener", "Hi", "Body")
    assert common.deliver_pending(campaign, "opener") is False
