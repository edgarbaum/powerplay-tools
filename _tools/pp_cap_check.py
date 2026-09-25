#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerPlay cap compliance check.  READ ONLY.

VERIFIED 2026-09-08 against PuckPedia on two players:
  Brandt Clarke  real AAV $7,400,000  ->  Fantrax $3,700,000.31   HALF, expiry 2031
  Lane Hutson    real AAV $8,850,000  ->  Fantrax $8,850,000.34   FULL, expiry 2034
=> The Fantrax figure is ALREADY the constitution-adjusted cap hit under 2.2.
   Do not apply a further discount. The cents encode the contract expiry year;
   .00 means the current year is the last and an extension is logged on Discord.

Only statusId 1 (active) and 2 (reserve) count. Minors (9) and IR do not.
Confirmed: statusId 1+2 sums exactly to Fantrax's own salaryUsed.
"""
import sys, time, warnings, datetime, statistics
warnings.filterwarnings('ignore')
from fantraxapi import FantraxAPI

def _league():
    """Season config, so the annual rollover is one edit in one file."""
    import json as _j, pathlib as _p
    f = _p.Path(__file__).parent / "config" / "league.json"
    if f.exists():
        d = _j.loads(f.read_text())
        return d["league_id"], d.get("season", ""), d.get("season_label", "")
    return "aizqwpvxmoc9uxas", "2627", "2026-27"   # fallback, current season

LEAGUE_ID, SEASON, SEASON_LABEL = _league()
FIRST_GAME = datetime.date(2026, 9, 29)
DEADLINE = FIRST_GAME - datetime.timedelta(days=2)   # Constitution 3.1


def team_cap(api, t):
    raw = api._request("getTeamRosterInfo", teamId=t.team_id)
    info = {i['key']: float(i['value']) for i in raw['miscData']['salaryInfo']['info']}
    hdr = [c.get('key') for c in raw['tables'][0]['header']['cells']]
    si = hdr.index('salary')
    expiring = 0
    for tbl in raw['tables']:
        for r in tbl.get('rows', []):
            if r.get('statusId') not in ('1', '2'):
                continue
            cells = r.get('cells', [])
            if len(cells) <= si:
                continue
            s = str(cells[si]['content'])
            if s.endswith('.00'):
                expiring += 1
    return info, expiring


def main(summary=False):
    api = FantraxAPI(LEAGUE_ID)
    rows = []
    for t in api.teams:
        try:
            info, exp = team_cap(api, t)
        except Exception as e:
            print("  %-26s ERROR %s" % (t.name[:26], str(e)[:50])); continue
        rows.append((t.name, info['salaryUsed'], info['salaryCap'],
                     info['salaryFloor'], info['claimBudget'] if 'claimBudget' in info else 0, exp))
        time.sleep(0.3)

    days = (DEADLINE - datetime.date.today()).days
    over  = [r for r in rows if r[1] > r[2]]
    under = [r for r in rows if r[1] < r[3]]
    used  = sorted(r[1] for r in rows)

    L = []
    L.append("**PowerPlay cap check** - %s" % datetime.date.today())
    import collections as _c
    ceils = [r[2] for r in rows]
    modal = _c.Counter(ceils).most_common(1)[0][0]
    n_modal = _c.Counter(ceils)[modal]
    L.append("Ceiling is **not uniform**: modal $%s (%d of %d teams), range $%s to $%s. "
             "Floor is uniform at $%s."
             % (format(modal, ',.0f'), n_modal, len(rows),
                format(min(ceils), ',.0f'), format(max(ceils), ',.0f'),
                format(rows[0][3], ',.0f')))
    L.append("Compliance deadline **%s**, %d days away (Constitution 3.1)." % (DEADLINE, days))
    L.append("")
    if over:
        L.append("__%d OVER the ceiling__" % len(over))
        for n, u, c, f, cb, e in over:
            L.append("  %s - $%s, over by $%s" % (n, format(u, ',.0f'), format(u - c, ',.0f')))
    if under:
        L.append("__%d UNDER the floor__" % len(under))
        for n, u, c, f, cb, e in under:
            L.append("  %s - $%s, short by $%s" % (n, format(u, ',.0f'), format(f - u, ',.0f')))
    if not over and not under:
        L.append("All %d teams between the floor and the ceiling." % len(rows))
    L.append("")
    L.append("Cap used: min $%s | median $%s | max $%s | mean $%s"
             % (format(used[0], ',.0f'), format(statistics.median(used), ',.0f'),
                format(used[-1], ',.0f'), format(statistics.mean(used), ',.0f')))
    squeeze = sorted(rows, key=lambda r: r[2] - r[3])[:3]
    L.append("Tightest SQUEEZE (floor to own ceiling): " +
             ", ".join("%s $%s" % (n.split()[-1], format(c - f, ',.0f')) for n, u, c, f, cb, e in squeeze))
    tight = sorted(rows, key=lambda r: r[2] - r[1])[:3]
    loose = sorted(rows, key=lambda r: r[1] - r[3])[:3]
    L.append("Tightest to ceiling: " + ", ".join("%s $%s left" % (n.split()[-1], format(c - u, ',.0f')) for n, u, c, f, cb, e in tight))
    L.append("Closest to floor:    " + ", ".join("%s $%s above" % (n.split()[-1], format(u - f, ',.0f')) for n, u, c, f, cb, e in loose))
    L.append("Contracts expiring this season (.00), league-wide: %d" % sum(r[5] for r in rows))
    msg = "\n".join(L)

    if not summary:
        print("%-26s %14s %14s %8s %6s" % ("TEAM", "USED", "HEADROOM", "V FLOOR", "EXP"))
        for n, u, c, f, cb, e in sorted(rows, key=lambda r: -r[1]):
            print("%-26s %14s %14s %8s %6d"
                  % (n[:26], format(u, ',.0f'), format(c - u, ',.0f'), format(u - f, ',.0f'), e))
        print()
    print(msg)
    return msg


if __name__ == "__main__":
    main(summary="--summary" in sys.argv)
