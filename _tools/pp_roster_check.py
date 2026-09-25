#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerPlay illegal-roster notifier.  READ ONLY.  Changes nothing on Fantrax.

Limits from PP-Constitution-2026-27-v9:
  4.1 Active roster        20  (12 F, 6 D, 2 G)
  4.2 Major roster         25  (active + up to 5 reserve)
  4.3 Minor league roster  74  (skaters GP < 83, goalies GP < 51)
  4.4 Total roster cap     99  (active + reserve + minors + IR)
"""
import sys, warnings, datetime
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
ACTIVE_MAX, MAJOR_MAX, MINOR_MAX, TOTAL_MAX = 20, 25, 74, 99

# Section 3.1: "Cap compliance deadline = 2 days before first NHL game".
# 2026-27 first NHL game = 2026-09-29  ->  deadline 2026-09-27.
FIRST_GAME = datetime.date(2026, 9, 29)
COMPLIANCE_DEADLINE = FIRST_GAME - datetime.timedelta(days=2)

def summary_block(counts, viol, live, days):
    """Compact Discord-sized digest. Flags first, table never."""
    import statistics
    tot=sorted(c[5] for c in counts)
    L=[]
    L.append("**PowerPlay roster check** - %s" % datetime.date.today())
    if live:
        L.append("Compliance deadline %s has PASSED." % COMPLIANCE_DEADLINE)
    else:
        L.append("Compliance deadline **%s** - %d days away. Flags are informational."
                 % (COMPLIANCE_DEADLINE, days))
    flagged=[c for c in counts if c[3]>MAJOR_MAX or c[1]>ACTIVE_MAX or c[5]>TOTAL_MAX
             or max(0,c[5]-c[3]-c[4])>MINOR_MAX]
    if flagged:
        L.append("")
        L.append("__%d team%s over a limit__" % (len(flagged), "" if len(flagged)==1 else "s"))
        for nm,a,r,mj,ir,t,mn in flagged:
            why=[]
            if a>ACTIVE_MAX: why.append("active %d>%d"%(a,ACTIVE_MAX))
            if mj>MAJOR_MAX: why.append("major %d>%d"%(mj,MAJOR_MAX))
            if mn>MINOR_MAX: why.append("minors %d>%d"%(mn,MINOR_MAX))
            if t>TOTAL_MAX:  why.append("total %d>%d"%(t,TOTAL_MAX))
            L.append("  %s - %s" % (nm, ", ".join(why)))
    else:
        L.append("")
        L.append("All 32 teams within active/major/minors/total limits.")
    L.append("")
    L.append("Total rosters: min %d | median %d | max %d | mean %.1f  (cap %d)"
             % (tot[0], statistics.median(tot), tot[-1], statistics.mean(tot), TOTAL_MAX))
    return "\n".join(L)


def main():
    quiet = "--summary" in sys.argv
    api = FantraxAPI(LEAGUE_ID)
    teams = list(api.teams)
    if not quiet: print("PowerPlay roster check  |  %s  |  %d teams  |  READ ONLY"
          % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), len(teams)))
    if not quiet: print("Limits: active %d, major %d, minors %d, total %d"
          % (ACTIVE_MAX, MAJOR_MAX, MINOR_MAX, TOTAL_MAX))
    today = datetime.date.today()
    days = (COMPLIANCE_DEADLINE - today).days
    if days > 0:
        if not quiet: print("Compliance deadline %s (Sec 3.1, 2 days before first NHL game %s): %d DAYS AWAY."
              % (COMPLIANCE_DEADLINE, FIRST_GAME, days))
        if not quiet: print("==> Flags below are INFORMATIONAL. Nothing is a violation until the deadline.\n")
        live = False
    else:
        if not quiet: print("Compliance deadline %s has PASSED. Flags below are LIVE violations.\n"
              % COMPLIANCE_DEADLINE)
        live = True

    hdr = "%-26s %6s %8s %7s %6s %6s   %s" % (
        "TEAM", "ACTIVE", "RESERVE", "MAJOR", "IR", "TOTAL", "FLAGS")
    if not quiet:
        print(hdr); print("-" * len(hdr))

    viol, rows_out, counts = 0, [], []
    for t in teams:
        try:
            r = api.roster_info(t.team_id)
        except Exception as e:
            print("%-26s  ERROR %s" % (t.name[:26], str(e)[:60])); continue
        def n(x):
            try: return len(x)
            except TypeError: return int(x) if isinstance(x, (int, float)) else 0
        active, reserve, injured = n(r.active), n(r.reserve), n(r.injured)
        total_rows = len(r.rows)
        major = active + reserve
        minors = max(0, total_rows - major - injured)

        flags = []
        if active > ACTIVE_MAX: flags.append("ACTIVE>%d" % ACTIVE_MAX)
        if major  > MAJOR_MAX:  flags.append("MAJOR>%d"  % MAJOR_MAX)
        if minors > MINOR_MAX:  flags.append("MINORS>%d" % MINOR_MAX)
        if total_rows > TOTAL_MAX: flags.append("TOTAL>%d" % TOTAL_MAX)
        if flags: viol += 1
        counts.append((t.name, active, reserve, major, injured, total_rows, minors))
        if not quiet:
            print("%-26s %6d %8d %7d %6d %6d   %s"
                  % (t.name[:26], active, reserve, major, injured, total_rows,
                     ", ".join(flags) if flags else ""))

    if quiet:
        print(summary_block(counts, viol, live, days))
        return 1 if (viol and live) else 0
    print()
    if viol:
        if live:
            print(">>> %d team(s) IN VIOLATION. Deadline has passed." % viol)
        else:
            print(">>> %d team(s) over a limit, %d days before the deadline. Not yet a violation."
                  % (viol, days))
    else:
        print(">>> No teams over any of the four hard limits.")

    # distribution, never a bare summary stat
    tot = [c[5] for c in counts]
    tot.sort()
    if tot:
        import statistics
        print("\nTotal-roster distribution across %d teams:" % len(tot))
        print("   min %d | p25 %d | median %d | p75 %d | max %d | mean %.1f"
              % (tot[0], tot[len(tot)//4], statistics.median(tot),
                 tot[3*len(tot)//4], tot[-1], statistics.mean(tot)))
        buckets = {}
        for v in tot:
            b = (v // 10) * 10
            buckets[b] = buckets.get(b, 0) + 1
        print("   histogram (by 10s): " +
              "  ".join("%d-%d:%d" % (b, b+9, c) for b, c in sorted(buckets.items())))
    return 1 if (viol and live) else 0

if __name__ == "__main__":
    sys.exit(main())
