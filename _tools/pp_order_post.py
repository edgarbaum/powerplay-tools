#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Post a claim order to its channel. READ ONLY against Fantrax; reads local state.

Two INDEPENDENT orders, per EB's ruling of 2026-09-20: free agent bidding and the
in-season waiver wire do not touch each other, so they are two lists that drift
apart and each has its own channel.

  --fa       #26-27-fa-claim-order      Fantrax calls it the Bid Tie-Breaker Order
  --waiver   #26-27-waiver-claim-order  Fantrax calls it the Waiver Wire Order

POSTS ONLY WHEN THE ORDER HAS CHANGED. A standing list re-posted daily is noise,
and noise is how a channel stops being read. State is a fingerprint of the order
committed alongside it, so the check survives across runs and machines.
`--force` overrides, for the first post into a new channel.

The point of the channel is that the order stops living in two people's heads.
git log on _tools/state/orders.json is the audit trail a disputing GM gets shown.
"""
import json, sys, hashlib, pathlib, datetime, importlib.util, warnings

HERE = pathlib.Path(__file__).parent
ORDERS = HERE / "state" / "orders.json"
POSTED = HERE / "state" / "orders_posted.json"


def codes():
    """Fantrax's own 3-letter code per team, pulled live.

    EB 2026-09-25: use the codes, not the full names. Thirty-two full team names
    force a two-column layout that wraps; the codes give four columns that read as
    an actual table. Taken from the API rather than hardcoded so a rename cannot
    silently desync, with the full name as fallback if the call fails.
    """
    try:
        warnings.filterwarnings("ignore")
        from fantraxapi import FantraxAPI
        cfg = HERE / "config" / "league.json"
        lid = json.loads(cfg.read_text())["league_id"] if cfg.exists() else "aizqwpvxmoc9uxas"
        return {t.name: (t.short or t.name) for t in FantraxAPI(lid).teams}
    except Exception as e:
        print("could not fetch team codes (%s); falling back to full names" % str(e)[:50])
        return {}


def main():
    args = sys.argv[1:]
    which = "fa" if "--fa" in args else "waiver" if "--waiver" in args else None
    if which is None:
        sys.exit("say which: --fa or --waiver")
    dry, force = "--dry" in args, "--force" in args

    if not ORDERS.exists():
        sys.exit("no %s; nothing to post" % ORDERS)
    d = json.loads(ORDERS.read_text())
    key = "fa_order" if which == "fa" else "waiver_order"
    blk = d[key]
    order = blk["order"]
    if len(order) != len(set(order)):
        sys.exit("REFUSING TO POST: the %s order has duplicate teams" % which)

    spec = importlib.util.spec_from_file_location("m", HERE / "pp_discord_post.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

    # Key the fingerprint by the RESOLVED channel, not by which order it is.
    # Otherwise a post diverted to #pp-bot-test by PP_CHANNEL_OVERRIDE marks the
    # order as already posted, and the real channel silently never gets it. That
    # is exactly what staging is for, and exactly how staging can lie to you.
    dest = mod.resolve_channel(which)
    fp = hashlib.sha256("\n".join(order).encode()).hexdigest()[:12]
    seen = json.loads(POSTED.read_text()) if POSTED.exists() else {}
    seen_key = "%s@%s" % (which, dest)
    if seen.get(seen_key) == fp and not force:
        print("%s order unchanged at %s (%s); nothing posted" % (which, dest, fp))
        return 0

    # a code fence, because Discord renders proportional text outside one and
    # columns will not line up in a proportional font
    abbr = codes()
    lab = [abbr.get(t, t) for t in order]
    ncol = 4 if all(len(x) <= 4 for x in lab) else 2
    n = len(lab)
    per = (n + ncol - 1) // ncol
    w = max(len(x) for x in lab)
    rows = []
    for i in range(per):
        cells = []
        for c in range(ncol):
            j = c * per + i
            if j < n:
                cells.append("%2d  %-*s" % (j + 1, w, lab[j]))
        rows.append("   ".join(cells).rstrip())

    msg = ["**%s** - %s" % (blk["fantrax_name"], d.get("as_of", datetime.date.today())),
           blk["state"], "```"]
    msg += rows
    msg.append("```")
    msg.append("_Budgets apply._" if blk.get("uses_budget") else
               "_No budget: this order is claim priority only._")
    text = "\n".join(msg)
    if len(text) > 1850:
        text = text[:1840] + "\n_...truncated_"

    rc = mod.post(text, dry=dry, channel=which)
    if rc == 0 and not dry:
        seen[seen_key] = fp
        POSTED.write_text(json.dumps(seen, indent=1, sort_keys=True) + "\n")
        print("recorded fingerprint %s" % fp)
    return rc


if __name__ == "__main__":
    sys.exit(main())
