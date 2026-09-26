#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerPlay LIFO cure-window watch. READ ONLY.

Constitution 2.4: when a roster is illegal, the GM has 7 DAYS from notification to
cure it. Only then is the most recently acquired player auto-dropped, and the next,
until the roster is legal.

So this is a WARNING instrument, not a post-mortem. Its purpose is that the
auto-drop never fires: the GM sees the three players at risk and fixes it himself.

SCOPE, per EB's ruling of 2026-09-25:
  LIFO applies to the MAIN roster only. Minors and prospects are NOT candidates.
  The exclusion is by ROSTER LOCATION, not by age. A young player who IS on the
  main roster is a legitimate LIFO target, and the GM's remedy is to trade him or
  drop someone else.

THE CURE CLOCK IS TRACKED, NOT GUESSED. First detection of a violation is recorded
in state and committed, so the 7-day deadline is measured from when the breach was
actually first seen rather than from whenever this happens to run.

WHAT IT DOES NOT COVER, and will not pretend to:
  Position maxima (12 F, 6 D, 3 G) are a 2.4 trigger. Not implemented here. A team
  in breach of ONLY a position maximum will not appear. Named rather than guessed.
"""
import warnings, json, sys, time, datetime, pathlib, collections
warnings.filterwarnings("ignore")
from fantraxapi import FantraxAPI

HERE = pathlib.Path(__file__).parent
STATE = HERE / "state" / "lifo_watch.json"
CURE_DAYS = 7
ACTIVE_MAX, MAJOR_MAX, MINOR_MAX, TOTAL_MAX = 20, 25, 74, 99
MAIN_STATUS = ("1", "2")          # active + reserve; proven to sum to salaryUsed
CANARIES = ["Macklin Celebrini", "Lane Hutson", "Brandt Clarke"]


def _league():
    f = HERE / "config" / "league.json"
    if f.exists():
        return json.loads(f.read_text())["league_id"]
    return "aizqwpvxmoc9uxas"


def _rows(api, view):
    out = []
    for page in range(1, 40):
        r = api._request("getTransactionDetailsHistory", view=view, pageNumber=page)
        tbl = r.get("table") or (r.get("tables") or [{}])[0]
        rs = tbl.get("rows", [])
        if not rs:
            break
        hdr = [c.get("key") or c.get("name") for c in tbl["header"]["cells"]]
        out += [(x, hdr) for x in rs]
    return out


def _dt(s):
    try:
        return datetime.datetime.strptime(s, "%a %b %d, %Y, %I:%M%p")
    except (ValueError, TypeError):
        return None


def acquisitions(api):
    """player -> (team, when) for the most recent acquisition we can see.

    Bounded by whatever window Fantrax returns. A player acquired before that
    window has no date and is treated as OLDER than anything dated, which is the
    safe direction for LIFO: it can never wrongly promote someone to most-recent.
    """
    acq = {}
    for row, hdr in _rows(api, "CLAIM_DROP"):
        sc = row.get("scorer") or {}
        cells = row.get("cells", [])
        if not sc.get("name") or not row.get("claimType"):
            continue
        ti = hdr.index("team") if "team" in hdr else 0
        di = hdr.index("date") if "date" in hdr else 1
        if len(cells) <= max(ti, di):
            continue
        when = _dt(str(cells[di].get("content", "")))
        if when:
            acq[sc["name"].strip()] = (str(cells[ti].get("content", "")).strip(), when, "claim")

    # trades: the date cell carries a rowspan, so only the first row of a set has it
    tr = _rows(api, "TRADE")
    first = {}
    for row, hdr in tr:
        di = hdr.index("date") if "date" in hdr else 2
        cells = row.get("cells", [])
        if len(cells) > di:
            w = _dt(str(cells[di].get("content", "")))
            if w and row.get("txSetId") not in first:
                first[row["txSetId"]] = w
    for row, hdr in tr:
        sc = row.get("scorer") or {}
        cells = row.get("cells", [])
        toi = hdr.index("to") if "to" in hdr else 1
        if not sc.get("name") or len(cells) <= toi:
            continue
        when = first.get(row.get("txSetId"))
        to = str(cells[toi].get("content", "")).strip()
        name = sc["name"].strip()
        if when and (name not in acq or acq[name][1] < when):
            acq[name] = (to, when, "trade")
    return acq


def main():
    api = FantraxAPI(_league())
    # EB 2026-09-25: 3-letter codes, from Fantrax itself, not hardcoded
    abbr = {t.name: (t.short or t.name) for t in api.teams}
    roster, counts, cap = {}, {}, {}
    for t in api.teams:
        raw = api._request("getTeamRosterInfo", teamId=t.team_id)
        info = {i["key"]: i for i in raw["miscData"]["salaryInfo"]["info"]}
        cap[t.name] = (float(info["salaryUsed"]["value"]),
                       float(info["salaryCap"]["value"]),
                       float(info["salaryFloor"]["value"]))
        main_players, active, minors, total = [], 0, 0, 0
        for tbl in raw["tables"]:
            for r in tbl.get("rows", []):
                sc = r.get("scorer") or {}
                st = r.get("statusId")
                if not sc.get("name"):
                    continue
                total += 1
                if st == "1":
                    active += 1
                if st == "9":
                    minors += 1
                if st in MAIN_STATUS:
                    main_players.append(sc["name"].strip())
        roster[t.name] = main_players
        counts[t.name] = (active, len(main_players), minors, total)
        time.sleep(0.12)

    got = {p for v in roster.values() for p in v}
    missing = [c for c in CANARIES if c not in got]
    if missing:
        sys.exit("CANARY FAILED: read %d main-roster players but not %s" % (len(got), missing))

    breaches = {}
    for t, (active, major, minors, total) in counts.items():
        f = []
        if active > ACTIVE_MAX: f.append("active %d>%d" % (active, ACTIVE_MAX))
        if major > MAJOR_MAX:   f.append("major %d>%d" % (major, MAJOR_MAX))
        if minors > MINOR_MAX:  f.append("minors %d>%d" % (minors, MINOR_MAX))
        if total > TOTAL_MAX:   f.append("total %d>%d" % (total, TOTAL_MAX))
        used, ceil, floor = cap[t]
        if used > ceil:  f.append("cap $%.0f over ceiling" % (used - ceil))
        if used < floor: f.append("cap $%.0f under floor" % (floor - used))
        if f:
            breaches[t] = f

    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    today = datetime.date.today()
    for t in list(st):
        if t not in breaches:
            del st[t]                      # cured: the clock stops and resets
    for t in breaches:
        st.setdefault(t, today.isoformat())
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")

    acq = acquisitions(api)
    sub = []
    for t in sorted(breaches):
        names = [p for p in roster[t] if p in acq and acq[p][0] == t]
        names.sort(key=lambda p: acq[p][1], reverse=True)
        sub.append("%s|%s|%s" % (t, ",".join(sorted(breaches[t])), ",".join(names[:3])))
    import hashlib as _h
    print("#DIGEST " + _h.sha256("\n".join(sub).encode()).hexdigest()[:16])

    out = ["**PowerPlay LIFO cure-window watch** - %s" % today,
           "Constitution 2.4: 7 days from notification to cure, then the most recently "
           "acquired MAIN-roster player is auto-dropped, and the next, until legal."]
    if not breaches:
        out.append("")
        out.append(">>> No team is in breach. Nothing is on the clock.")
        print("\n".join(out))
        return 0

    out.append("")
    out.append(">>> %d team%s on the clock" % (len(breaches), "" if len(breaches) == 1 else "s"))
    for t in sorted(breaches, key=lambda x: st[x]):
        since = datetime.date.fromisoformat(st[t])
        due = since + datetime.timedelta(days=CURE_DAYS)
        left = (due - today).days
        out.append("")
        out.append("__%s  %s__ - %s" % (abbr.get(t, t), t, ", ".join(breaches[t])))
        out.append("  first seen %s, cure by **%s** (%s)"
                   % (since, due, "%d days left" % left if left > 0 else
                      "DUE TODAY" if left == 0 else "OVERDUE by %d days" % -left))
        dated = [(p,) + acq[p][1:] for p in roster[t] if p in acq and acq[p][0] == t]
        dated.sort(key=lambda x: x[1], reverse=True)
        if dated:
            out.append("  at risk, newest first:")
            for i, (p, when, how) in enumerate(dated[:3], 1):
                out.append("   %d. %s  (%s %s)" % (i, p, how, when.strftime("%b %d")))
            if len(dated) < 3:
                out.append("   _only %d main-roster acquisition%s visible in the transaction "
                           "window; anything older cannot be dated_"
                           % (len(dated), "" if len(dated) == 1 else "s"))
        else:
            out.append("  _no main-roster acquisition is visible in the transaction window, "
                       "so LIFO order cannot be determined from the API_")
    print("\n".join(out))
    return 1


if __name__ == "__main__":
    sys.exit(main())
