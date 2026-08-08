"""Renders the latest run (and recent history) as a self-contained HTML page.

Self-contained on purpose: no CDN, no fonts, no scripts. The report has to be readable from
a file:// URL on a machine that may be having the very network problems it is reporting on.
"""
from __future__ import annotations

import html
from datetime import datetime

from . import config

_STATUS_COLOR = {"ok": "#2fa366", "warn": "#c98a1b", "fail": "#c2402f", "skip": "#8a7b68"}
_STATUS_LABEL = {"ok": "OK", "warn": "DEGRADED", "fail": "DOWN", "skip": "SKIPPED"}
_CATEGORY_TITLE = {"infra": "Infrastructure", "space": "Hugging Face Spaces", "module": "In-app modules"}


def _rows(results: list[dict]) -> str:
    out = []
    for r in results:
        colour = _STATUS_COLOR.get(r["status"], "#8a7b68")
        out.append(
            f'<tr><td class="s"><span class="dot" style="background:{colour}"></span>'
            f'<span style="color:{colour}">{_STATUS_LABEL.get(r["status"], r["status"])}</span></td>'
            f'<td class="n">{html.escape(r["name"])}</td>'
            f'<td class="d">{html.escape(r.get("detail") or "")}</td>'
            f'<td class="t">{r.get("ms", 0)} ms</td></tr>'
        )
    return "\n".join(out)


def _history_strip(entries: list[dict]) -> str:
    cells = []
    for e in reversed(entries[-24:]):
        s = e.get("summary", {})
        colour = _STATUS_COLOR["fail"] if s.get("fail") else (
            _STATUS_COLOR["warn"] if s.get("warn") else _STATUS_COLOR["ok"])
        when = e.get("at", "")[:16].replace("T", " ")
        title = f'{when} · {e.get("mode","")} · {s.get("ok",0)} ok / {s.get("warn",0)} degraded / {s.get("fail",0)} down'
        cells.append(f'<span class="hbar" style="background:{colour}" title="{html.escape(title)}"></span>')
    return "".join(cells)


def render(entry: dict, history: list[dict]) -> str:
    summary = entry.get("summary", {})
    results = entry.get("results", [])
    overall = "fail" if summary.get("fail") else ("warn" if summary.get("warn") else "ok")
    banner = _STATUS_COLOR[overall]
    headline = {
        "ok": "Everything is answering",
        "warn": "Running degraded",
        "fail": "Something is down",
    }[overall]

    sections = []
    for category in ("infra", "space", "module"):
        rows = [r for r in results if r["category"] == category]
        if not rows:
            continue
        rows.sort(key=lambda r: ({"fail": 0, "warn": 1, "skip": 2, "ok": 3}[r["status"]], r["name"]))
        sections.append(
            f'<h2>{_CATEGORY_TITLE[category]}</h2><table>{_rows(rows)}</table>'
        )

    return f"""<!doctype html>
<meta charset="utf-8">
<title>Mr. AI Marketer — health</title>
<style>
  body {{ margin:0; padding:34px; background:#fff6ea; color:#2b2420;
         font:14px/1.5 "Segoe UI",system-ui,sans-serif; }}
  .wrap {{ max-width:900px; margin:0 auto; }}
  .banner {{ background:{banner}; color:#fffbf3; border-radius:16px; padding:18px 22px; margin-bottom:8px; }}
  .banner h1 {{ margin:0 0 4px; font-size:22px; }}
  .banner p {{ margin:0; opacity:.9; font-size:13px; }}
  .hist {{ display:flex; gap:3px; margin:14px 0 26px; }}
  .hbar {{ width:14px; height:26px; border-radius:3px; display:inline-block; }}
  h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:#8a7b68;
        margin:26px 0 8px; }}
  table {{ width:100%; border-collapse:collapse; background:#fffbf3;
           border:1px solid #ead9c2; border-radius:12px; overflow:hidden; }}
  td {{ padding:9px 12px; border-top:1px solid #f0e4d2; vertical-align:top; }}
  tr:first-child td {{ border-top:none; }}
  .s {{ width:112px; font-weight:600; font-size:12px; white-space:nowrap; }}
  .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:7px; }}
  .n {{ font-weight:600; width:250px; }}
  .d {{ color:#6b5f4e; font-size:13px; overflow-wrap:anywhere; }}
  .t {{ color:#b0a488; text-align:right; width:80px; font-variant-numeric:tabular-nums; }}
  footer {{ margin-top:26px; color:#8a7b68; font-size:12px; }}
</style>
<div class="wrap">
  <div class="banner">
    <h1>{headline}</h1>
    <p>{summary.get('ok',0)} ok · {summary.get('warn',0)} degraded · {summary.get('fail',0)} down
       · {summary.get('skip',0)} skipped — {entry.get('mode','health')} run at
       {html.escape(entry.get('at','')).replace('T',' ')}</p>
  </div>
  <div class="hist">{_history_strip(history)}</div>
  {''.join(sections)}
  <footer>Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} · newest run on the right of the strip</footer>
</div>
"""


def write(entry: dict, history: list[dict]) -> str:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    config.REPORT_PATH.write_text(render(entry, history), encoding="utf-8")
    return str(config.REPORT_PATH)
