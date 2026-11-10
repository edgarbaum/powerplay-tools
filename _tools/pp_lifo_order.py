#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Next player to be dropped. READ ONLY. Weekly.

Constitution 2.4: an illegal roster that is not cured is trimmed by auto-dropping the
most recently acquired MAIN-roster player (statusId 1 Active or 2 Reserve; Minors and IR
are never LIFO targets), then the next. This lists, for every team, the three players
who would go first: last in, first out.

ACQUISITION DATES come from pp_lifo_check.acquisitions: getTransactionDetailsHistory
views CLAIM_DROP and TRADE, the latest way a player reached his CURRENT team. The window
Fantrax returns is bounded, so a player with no dated acquisition (drafted, or acquired
before the window) is treated as OLDER than every dated one. That is the safe direction:
he can never be wrongly promoted ahead of someone with a date. A team with fewer than
three dated players shows fewer than three.

CANARIES (must pass or the run refuses)
  reader    Celebrini, Hutson and Clarke are on a main roster.
  detector  a synthetic acquisition dated tomorrow injected for one dated team's oldest
            listed player must put him FIRST for that team, and must change nothing else.

EXIT CODES: #DIGEST is the first line of stdout. 3 = a post, 0 = no dated acquisition at
all, anything else non-zero = a crash or a refused canary.

  usage: pp_lifo_order.py [--post-out FILE]
"""
import warnings, sys, time, datetime, hashlib, pathlib, re
warnings.filterwarnings("ignore")
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pp_lifo_check as lc

FINDINGS = 3
N = 3
WIDTH = 50
CANARIES = lc.CANARIES
SUFFIX = {"jr.", "sr.", "ii", "iii", "iv", "jr", "sr"}


def short(name):
    toks = name.split()
    while len(toks) > 1 and toks[-1].lower() in SUFFIX:
        toks.pop()
    return toks[-1][:8]


def top(roster, acq, team_name):
    """roster{team: [names]}; -> [(name, when)] newest first, only players whose dated
    acquisition was INTO this team."""
    dated = [(p, acq[p][1]) for p in roster[team_name] if p in acq and acq[p][0] == team_name]
    dated.sort(key=lambda x: (x[1], x[0]), reverse=True)
    return dated[:N]


def read_main(api):
    roster, abbr = {}, {}
    for t in api.teams:
        raw = None
        for attempt in range(3):
            try:
                raw = api._request("getTeamRosterInfo", teamId=t.team_id)
                break
            except Exception:                                  # noqa: BLE001
                time.sleep(3 * (attempt + 1))
        if raw is None:
            raise RuntimeError("Fantrax roster call failed 3 times for %s" % t.name)
        abbr[t.name] = t.short or t.name
        roster[t.name] = [r["scorer"]["name"].strip() for tbl in raw["tables"] for r in tbl.get("rows", [])
                          if (r.get("scorer") or {}).get("name") and str(r.get("statusId")) in lc.MAIN_STATUS]
        time.sleep(0.12)
    return roster, abbr


def main():
    args = sys.argv[1:]
    post_out = args[args.index("--post-out") + 1] if "--post-out" in args else None
    from fantraxapi import FantraxAPI
    api = FantraxAPI(lc._league())
    if len(api.teams) != 32:
        sys.exit("Fantrax returned %d teams, expected 32. Refusing to report." % len(api.teams))
    roster, abbr = read_main(api)
    got = {p for v in roster.values() for p in v}
    miss = [c for c in CANARIES if c not in got]
    if miss:
        sys.exit("CANARY FAILED: read %d main-roster players but not %s" % (len(got), miss))
    acq = lc.acquisitions(api)
    tops = {t: top(roster, acq, t) for t in roster}

    # detector canary
    probe_team = next((t for t in sorted(tops) if tops[t]), None)
    if probe_team is None:
        print("#DIGEST none")
        print("No dated acquisition into any main roster: UNKNOWN, not clean. Nothing to post.")
        return 0
    cand = [p for p in roster[probe_team] if not (p in acq and acq[p][0] == probe_team)] or roster[probe_team]
    probe = cand[-1]
    acq2 = dict(acq)
    acq2[probe] = (probe_team, datetime.datetime.now() + datetime.timedelta(days=1), "claim")
    t2 = {t: top(roster, acq2, t) for t in roster}
    if not t2[probe_team] or t2[probe_team][0][0] != probe or any(t2[t] != tops[t] for t in roster if t != probe_team):
        sys.exit("DETECTOR CANARY FAILED: injecting %s as newest did not put him first for %s only. Refusing."
                 % (probe, probe_team))

    today = datetime.date.today()
    digest = hashlib.sha256("\n".join("%s|%s" % (t, ",".join(p for p, _ in tops[t])) for t in sorted(tops))
                            .encode()).hexdigest()[:16]
    print("#DIGEST " + digest)
    print("LIFO order %s: %d teams, %d with 3 dated, %d with 1-2, %d with none. detector canary passed on %s."
          % (today, len(tops), sum(1 for v in tops.values() if len(v) == 3),
             sum(1 for v in tops.values() if 1 <= len(v) <= 2), sum(1 for v in tops.values() if not v), probe))
    post = ["#DIGEST " + digest, "NEXT TO BE DROPPED (LIFO) %s" % today.strftime("%m/%d"),
            "Rule 2.4. Main roster, newest in first."]
    for t in sorted(tops, key=lambda x: abbr[x]):
        ln = abbr[t] + " " + " ".join("%s %s" % (short(p), w.strftime("%m/%d")) for p, w in tops[t])
        post.append(ln[:WIDTH].rstrip())
        print("%s  %s" % (abbr[t], "; ".join("%s (%s)" % (p, w.strftime("%b %d")) for p, w in tops[t]) or "no dated acquisition"))
    post.append("No date = drafted or pre-window.")
    if post_out:
        pathlib.Path(post_out).write_text("\n".join(post) + "\n")
    return FINDINGS


if __name__ == "__main__":
    sys.exit(main())
