#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerPlay pending-trade bill clock.  READ ONLY.

Constitution 4.7:  "...must be paid within 3 days of the trade or the trade will become null"
Constitution 5.6:  "...within 3 days of the time of the trade or it will be vetoed"
  ^ The SAME rule stated twice. Backlog R2 proposes changing BOTH to run from the time the
    bill is ISSUED rather than the time of the trade. This tool anchors on the trade time,
    which is the rule as written today. Change ANCHOR below if R2 passes.

Announces on state change only, never on every run:
  NEW      a pending trade appears
  WARNING  1 day left
  FINAL    last day
  EXPIRED  past 3 days and still pending
  RESOLVED it left the pending list

State: _tools/state/trade_clock.json
"""
import json, pathlib, sys, warnings, datetime, importlib.util
warnings.filterwarnings('ignore')
from fantraxapi import FantraxAPI

HERE = pathlib.Path(__file__).parent
STATE = HERE / "state" / "trade_clock.json"
def _league():
    """Season config, so the annual rollover is one edit in one file."""
    import json as _j, pathlib as _p
    f = _p.Path(__file__).parent / "config" / "league.json"
    if f.exists():
        d = _j.loads(f.read_text())
        return d["league_id"], d.get("season", ""), d.get("season_label", "")
    return "aizqwpvxmoc9uxas", "2627", "2026-27"   # fallback, current season

LEAGUE_ID, SEASON, SEASON_LABEL = _league()
WINDOW_DAYS = 3
ANCHOR = "accepted"     # switch to "billed" if R2 passes and a bill date becomes available


def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(d):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=1, default=str))


def parse_dt(s):
    for f in ("%a %b %d, %Y, %I:%M%p", "%Y-%m-%d %H:%M:%S", "%a %b %d, %Y"):
        try: return datetime.datetime.strptime(str(s).strip(), f)
        except Exception: pass
    return None


def describe(tr):
    ins, outs = [], []
    for m in tr.moves:
        nm = getattr(m, "name", None) or getattr(m, "short_name", None) or str(m)
        (ins if len(ins) <= len(outs) else outs).append(str(nm))
    return "%d assets" % len(tr.moves)


def main(dry=False):
    api = FantraxAPI(LEAGUE_ID)
    pend = api.pending_trades()
    st = load()
    now = datetime.datetime.now()
    events = []

    seen_ids = set()
    for tr in pend:
        tid = tr.trade_id
        seen_ids.add(tid)
        anchor = parse_dt(getattr(tr, ANCHOR, None)) or parse_dt(tr.proposed) or now
        deadline = anchor + datetime.timedelta(days=WINDOW_DAYS)
        left = (deadline - now).days
        prev = st.get(tid, {})
        stage = ("EXPIRED" if left < 0 else "FINAL" if left == 0
                 else "WARNING" if left == 1 else "OPEN")
        if not prev:
            events.append("**NEW pending trade** - %s proposed by %s. Fee due by **%s** (%d days)."
                          % (describe(tr), tr.proposed_by.name, deadline.strftime("%a %d %b"), max(left, 0)))
        elif prev.get("stage") != stage and stage != "OPEN":
            label = {"WARNING": "1 DAY LEFT", "FINAL": "FINAL DAY",
                     "EXPIRED": "PAST DEADLINE - subject to 4.7 / 5.6"}[stage]
            events.append("**%s** - trade by %s, %s. Fee deadline %s."
                          % (label, tr.proposed_by.name, describe(tr), deadline.strftime("%a %d %b")))
        st[tid] = {"stage": stage, "anchor": str(anchor), "deadline": str(deadline),
                   "team": tr.proposed_by.name, "first_seen": prev.get("first_seen", str(now))}

    for tid in [k for k in st if k not in seen_ids]:
        events.append("**RESOLVED** - trade by %s is no longer pending." % st[tid].get("team", "?"))
        del st[tid]

    if not events:
        print("trade clock: %d pending, no state change." % len(pend))
        if not dry: save(st)
        return 0

    msg = "**PowerPlay trade clock**\n" + "\n".join(events)
    if dry:
        print(msg); return 0
    spec = importlib.util.spec_from_file_location('m', HERE / 'pp_discord_post.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    rc = m.post(msg, channel="ops")
    if rc == 0: save(st)
    return rc


if __name__ == "__main__":
    sys.exit(main(dry="--dry" in sys.argv))
