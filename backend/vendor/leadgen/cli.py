"""Standalone CLI for diagnostics and offline driving (same spirit as socialpost's config
CLI). Run from `backend/` with the venv:  python -m vendor.leadgen.cli <command>

Note: the `email` step needs the app's Email Writer, which is injected only when the agent
runs inside the backend; from the bare CLI, discovery/qualification/email-finding work but
opener drafting is skipped (logged) unless a writer is injected.
"""

from __future__ import annotations

import argparse
import json
import logging

from . import config, daemon, db


def _cmd_status(args: argparse.Namespace) -> None:
    print(f"LLM backend : {config.current('LLM_BACKEND')} ({config.current('LLM_MODEL')})")
    print(f"Reacher     : {config.reacher_url()}")
    print(f"SearXNG     : {config.searxng_url()}")
    print(f"Discovery   : {', '.join(config.discovery_backends())}")
    campaigns = db.list_campaigns()
    print(f"\nCampaigns ({len(campaigns)}):")
    for c in campaigns:
        counts = db.deal_state_counts(c["id"])
        flags = []
        if c["active"]:
            flags.append("active")
        if c["auto_send"]:
            flags.append("auto-send")
        print(f"  {c['id'][:8]}  {c['name']:24} [{', '.join(flags) or 'paused'}]  {counts}")


def _cmd_add_campaign(args: argparse.Namespace) -> None:
    c = db.create_campaign(
        name=args.name,
        product_description=args.product,
        objective=args.objective or "",
        country=args.country or "",
        daily_cap=args.daily_cap,
        auto_send=args.auto_send,
    )
    if args.activate:
        db.update_campaign(c["id"], active=1)
    print(f"Created campaign {c['id']} ({'active' if args.activate else 'paused'}).")


def _cmd_run_once(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.campaign:
        steps = daemon.run_campaign_until_idle(args.campaign, max_steps=args.max_steps)
        print(f"Drove campaign {args.campaign[:8]} for {steps} step(s).")
    else:
        worked = daemon.tick()
        print(f"Ticked all active campaigns; {worked} did work.")


def _cmd_export(args: argparse.Namespace) -> None:
    deals = db.list_deals_with_leads(args.campaign, limit=100000)
    out = json.dumps(deals, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"Wrote {len(deals)} deals to {args.out}")
    else:
        print(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lead Gen Agent CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show config + campaign summary.").set_defaults(func=_cmd_status)

    p_add = sub.add_parser("add-campaign", help="Create a campaign.")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--product", required=True, help="Product/service description.")
    p_add.add_argument("--objective", default="")
    p_add.add_argument("--country", default="")
    p_add.add_argument("--daily-cap", type=int, default=20)
    p_add.add_argument("--auto-send", action="store_true")
    p_add.add_argument("--activate", action="store_true", help="Start the campaign immediately.")
    p_add.set_defaults(func=_cmd_add_campaign)

    p_run = sub.add_parser("run-once", help="Run one tick (or drive one campaign to idle).")
    p_run.add_argument("--campaign", help="Drive this campaign until idle.")
    p_run.add_argument("--max-steps", type=int, default=200)
    p_run.set_defaults(func=_cmd_run_once)

    p_exp = sub.add_parser("export", help="Export a campaign's deals as JSON.")
    p_exp.add_argument("--campaign", required=True)
    p_exp.add_argument("--out", help="Write to this file instead of stdout.")
    p_exp.set_defaults(func=_cmd_export)

    args = parser.parse_args()
    db.init_db()
    args.func(args)


if __name__ == "__main__":
    main()
