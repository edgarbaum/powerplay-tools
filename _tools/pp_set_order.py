#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Set a claim order from a list of team codes, with validation.

WHY THIS IS MANUAL, deliberately. Fantrax exposes no claim-order endpoint
(getWaiverWireOrder, getClaimOrder, getTeamRankings, getLeagueRankings all fail),
so the order cannot be read. It COULD be computed from the claim results, since
the rule is winner-to-the-back, but the 2026-09-20 run needed an illegal-bid
penalty and a group-cap ruling that no algorithm would have guessed. Publishing a
computed order to 32 GMs with nothing authoritative to check it against is how you
publish a wrong one. So a human reads Fantrax and this validates what they typed.

VALIDATION IS THE POINT. It refuses unless the list is exactly the 32 known teams,
each once. A wrong order in front of 32 GMs is the failure this exists to prevent,
and a typo is far more likely than a rules dispute.

  pp_set_order.py --fa "TOR,PHI,MTL,..."      32 codes, comma or space separated
  pp_set_order.py --waiver --show             print the current order and stop
"""
import json, sys, pathlib, datetime, warnings

HERE = pathlib.Path(__file__).parent
ORDERS = HERE / "state" / "orders.json"


def main():
    args = sys.argv[1:]
    which = "fa" if "--fa" in args else "waiver" if "--waiver" in args else None
    if which is None:
        sys.exit("say which: --fa or --waiver")
    key = "fa_order" if which == "fa" else "waiver_order"
    d = json.loads(ORDERS.read_text())

    warnings.filterwarnings("ignore")
    from fantraxapi import FantraxAPI
    cfg = HERE / "config" / "league.json"
    lid = json.loads(cfg.read_text())["league_id"] if cfg.exists() else "aizqwpvxmoc9uxas"
    teams = list(FantraxAPI(lid).teams)
    by_code = {(t.short or t.name).upper(): t.name for t in teams}
    cur = d[key]["order"]

    if "--show" in args:
        print("current %s order (%d teams):" % (which, len(cur)))
        print(",".join(by_code and next(c for c, n in by_code.items() if n == t) for t in cur))
        return 0

    raw = [a for a in args if not a.startswith("--")]
    if not raw:
        sys.exit("give the order as 32 team codes, comma or space separated")
    codes = [c.strip().upper() for c in " ".join(raw).replace(",", " ").split() if c.strip()]

    # refuse loudly rather than publish something wrong
    bad = [c for c in codes if c not in by_code]
    dupes = sorted({c for c in codes if codes.count(c) > 1})
    missing = sorted(set(by_code) - set(codes))
    if bad or dupes or len(codes) != len(by_code):
        print("REFUSING to write the order.")
        if len(codes) != len(by_code):
            print("  got %d entries, expected %d" % (len(codes), len(by_code)))
        if bad:     print("  not a team code: %s" % ", ".join(bad))
        if dupes:   print("  listed twice: %s" % ", ".join(dupes))
        if missing: print("  never listed: %s" % ", ".join(missing))
        return 2

    new = [by_code[c] for c in codes]
    if new == cur:
        print("%s order unchanged; nothing written" % which)
        return 0

    moved = [(i + 1, c) for i, c in enumerate(codes)
             if by_code[c] != cur[i]]
    d[key]["order"] = new
    d[key]["state"] = "Updated %s from Fantrax." % datetime.date.today()
    d["as_of"] = str(datetime.date.today())
    ORDERS.write_text(json.dumps(d, indent=1) + "\n")
    print("wrote %s order; %d of %d positions changed" % (which, len(moved), len(new)))
    for pos, c in moved[:10]:
        print("   %2d  %-4s (was %s)" % (pos, c,
              next(k for k, v in by_code.items() if v == cur[pos - 1])))
    if len(moved) > 10:
        print("   ... and %d more" % (len(moved) - 10))
    return 0


if __name__ == "__main__":
    sys.exit(main())
