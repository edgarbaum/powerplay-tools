#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily MGP (minimum games played) tracker. READ ONLY.

Constitution v25 section 5.5: every team needs at least 1,200 man-games over the
regular season (Sep 29 to Feb 28, 153 fantasy days). The owner has ruled it counts
ONLY games played while the player sits in an ACTIVE slot (12 F, 6 D, 2 G = 20
slots), not Reserve, Minors or IR. Each NHL club plays 84 games, so the ceiling is
20 x 84 = 1,680 and 1,200 is 71.4% of it.

METHOD
  getTeamRosterInfo(teamId, period=N), N = the daily scoring period (1 = Sep 29).
    statusId   is THAT DAY's slot: 1 Active, 2 Reserve, 3 IR, 9 Minors.
    GP, Opp    GP is SEASON-TO-DATE as of period N, so the day's games are the
               DELTA against the same player's GP in period N-1, wherever he was
               rostered then. Opp is non-empty when his NHL club had a game.
  Per team and day:
    lineup_gp       sum of GP deltas of players in status 1
    off_lineup_gp   GP deltas of everyone else (does NOT count)
    avail           active players whose club had a game (the slot-games on offer)
    dead_slots      avail slots whose player's GP delta was 0 (scratched, AHL,
                    junior, a backup goalie)
  pace = lineup_gp / avail, and the season is projected as pace x 1,680.

WITNESS (the run refuses to report without it): for every one of the 32 teams the
cumulative lineup_gp must equal the GP column of getStandings(view=SEASON_STATS)
table 0. That table is year-to-date through yesterday, and it states its own end
date, so the comparison is made through exactly the last COMPLETED period.
A DETECTOR CANARY proves the comparison fires: one injected value must flag
exactly one team.

STATE _tools/state/mgp.json holds the last processed period, every player's GP at
that period (needed for the next delta) and the cumulative per-team totals, so a
run fetches only the new days. The first run backfills every completed day. State
is written only after the witness passes.

KNOWN LIMIT: a player who was on NO roster in period N-1 has no earlier GP to take
a delta from. GP 0 gives delta 0 exactly. GP above 0 cannot be resolved from
rosters; for an ACTIVE one the day is estimated (1 if his club had a game) and
counted in `est`, an off-lineup one adds nothing. The witness catches a wrong
estimate and the run refuses rather than carry it forward.

EXIT CODES: #DIGEST is the first line of stdout. 3 = a report to post (a new
completed period was processed), 0 = nothing new, anything else non-zero = a crash
or a refused witness.

  usage: pp_mgp.py [--post-out FILE] [--state FILE] [--no-record] [--through N]
"""
import warnings, json, sys, time, hashlib, datetime, pathlib, collections, statistics
warnings.filterwarnings("ignore")

FINDINGS = 3
HERE = pathlib.Path(__file__).parent
STATE = HERE / "state" / "mgp.json"
POST_MAX = 1700
SEASON_DAYS = 153
SLOTS = 20
GAMES = 84
CEILING = SLOTS * GAMES                                   # 1,680
MIN_MGP = 1200
ACTIVE = "1"


def _league():
    f = HERE / "config" / "league.json"
    if f.exists():
        return json.loads(f.read_text())["league_id"]
    return "aizqwpvxmoc9uxas"


LEAGUE_ID = _league()


# ---------------------------------------------------------------- readers

def retry(fn, what):
    last = None
    for attempt in range(3):
        try:
            return fn()
        except Exception as e:                              # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("%s failed 3 times: %s" % (what, last))


def read_period(api, teams, period):
    """-> ({scorerId: {tid, status, gp, opp, name}}, {tid: empty active slots})"""
    players, empty = {}, collections.Counter()
    for t in teams:
        raw = retry(lambda: api._request("getTeamRosterInfo", teamId=t.team_id, period=str(period)),
                    "roster %s period %d" % (t.name, period))
        for tbl in raw["tables"]:
            hdr = [c.get("shortName") for c in tbl["header"]["cells"]]
            if "GP" not in hdr or "Opp" not in hdr:
                continue
            gi, oi = hdr.index("GP"), hdr.index("Opp")
            for r in tbl.get("rows", []):
                sc = r.get("scorer")
                st = str(r.get("statusId"))
                if not sc:
                    if st == ACTIVE:
                        empty[t.team_id] += 1               # an active slot with nobody in it
                    continue
                c = r["cells"]
                gp = str(c[gi].get("content") or "0").strip()
                players[sc["scorerId"]] = {"tid": t.team_id, "status": st,
                                           "gp": int(float(gp)) if gp.replace(".", "").isdigit() else 0,
                                           "opp": str(c[oi].get("content") or "").strip(), "name": sc["name"]}
        time.sleep(0.12)
    return players, empty


def read_standings(api, teams):
    """-> ({tid: GP}, last completed period, start date). The table states its own
    coverage: displayedEndDate - displayedStartDate = the number of completed days."""
    s = retry(lambda: api._request("getStandings", view="SEASON_STATS"), "standings")
    t0 = s["tableList"][0]
    hdr = [c.get("shortName") for c in t0["header"]["cells"]]
    if "GP" not in hdr:
        raise RuntimeError("standings table 0 has no GP column: %s" % hdr)
    gi = hdr.index("GP")
    byname = {t.name: t.team_id for t in teams}
    gp = {}
    for r in t0["rows"]:
        nm = r["fixedCells"][1]["content"]
        if nm not in byname:
            raise RuntimeError("standings team %r is not a league team" % nm)
        gp[byname[nm]] = int(float(r["cells"][gi]["content"] or 0))
    sel = s["displayedSelections"]
    s_ms, e_ms = sel["displayedStartDate"], sel["displayedEndDate"]
    last = int(round((e_ms - s_ms) / 86400000.0))
    start = datetime.datetime.utcfromtimestamp(s_ms / 1000.0).date()
    return gp, min(last, SEASON_DAYS), start


# ---------------------------------------------------------------- one day

def day_stats(prev_gp, cur, empty, first_day):
    """-> ({tid: counts for the day}, new_gp map). Pure."""
    out = collections.defaultdict(collections.Counter)
    for tid, n in empty.items():
        out[tid]["empty"] += n
    for sid, p in cur.items():
        c = out[p["tid"]]
        if sid in prev_gp:
            d, known = p["gp"] - prev_gp[sid], True
        elif first_day or p["gp"] == 0:
            d, known = p["gp"], True                         # GP never falls: 0 stays 0
        else:
            d, known = None, False                           # never rostered at N-1 and has played before
        if p["status"] == ACTIVE:
            if p["opp"]:
                c["avail"] += 1
            if not known:
                d = 1 if p["opp"] else 0
                c["est"] += 1
            c["lineup"] += d
            if p["opp"] and d == 0:
                c["dead"] += 1
        elif known:
            c["off"] += d
    return out, {sid: p["gp"] for sid, p in cur.items()}


def witness(cum, standings):
    """-> teams whose cumulative lineup GP differs from the standings GP."""
    return sorted(t for t in standings if cum.get(t, {}).get("lineup", 0) != standings[t])


def witness_canary(cum, standings):
    bad = witness(cum, standings)
    if bad:
        return bad, None
    t = sorted(standings)[0]
    alt = {k: dict(v) for k, v in cum.items()}
    alt[t] = dict(alt.get(t, {}), lineup=alt.get(t, {}).get("lineup", 0) + 1)
    hit = witness(alt, standings)
    if hit != [t]:
        sys.exit("WITNESS CANARY FAILED: altering one team's total flagged %s, expected exactly [%s]. "
                 "Refusing to report." % (hit, t))
    return [], ("witness canary passed: one team's total altered by 1 flagged exactly that team")


# ---------------------------------------------------------------- report

def shape(vals, fmt):
    q = statistics.quantiles(vals, n=4, method="inclusive")
    return "n=%d, min %s, p25 %s, median %s, p75 %s, max %s" % (
        len(vals), fmt(min(vals)), fmt(q[0]), fmt(q[1]), fmt(q[2]), fmt(max(vals)))


def rows_of(state):
    rows = []
    for tid, v in state["teams"].items():
        pace = v["lineup"] / v["avail"] if v["avail"] else None
        rows.append(dict(v, tid=tid, pace=pace, proj=pace * CEILING if pace is not None else None))
    return rows


def render(state, start, witness_line, canary_line, new_periods):
    rows = rows_of(state)
    last = state["last_period"]
    thru = start + datetime.timedelta(days=last - 1)
    lo5 = sorted(rows, key=lambda r: (r["lineup"], r["pace"] if r["pace"] is not None else 0, r["name"]))[:5]
    paced = [r for r in rows if r["pace"] is not None]
    under = [r for r in paced if r["proj"] < MIN_MGP]
    pct = lambda x: "%.1f%%" % (100 * x)
    head = ("PowerPlay MGP tracker - through %s (period %d of %d), %d teams. Rule 5.5: %s man-games in ACTIVE slots; "
            "ceiling %s, so %s of it." % (thru.strftime("%b %d"), last, SEASON_DAYS, len(rows), "{:,}".format(MIN_MGP),
                                          "{:,}".format(CEILING), pct(MIN_MGP / float(CEILING))))
    d1 = "Lineup GP: " + shape([r["lineup"] for r in rows], str)
    d2 = "Pace (lineup GP / active slot-games on offer): " + shape([r["pace"] for r in paced], pct) + \
         ".  Needed: %s." % pct(MIN_MGP / float(CEILING))
    d3 = "Projected season total (pace x %s): %s.  Below %s: %d of %d teams." % (
        "{:,}".format(CEILING), shape([r["proj"] for r in paced], lambda x: "{:,.0f}".format(x)),
        "{:,}".format(MIN_MGP), len(under), len(paced))
    low = ["%d. %s: lineup GP %d, dead slots %d of %d, projected %s" % (
        i, r["name"], r["lineup"], r["dead"], r["avail"], "{:,.0f}".format(r["proj"]) if r["proj"] is not None else "n/a")
        for i, r in enumerate(lo5, 1)]
    method = ("Method: GP gained by players in Active slots, from daily rosters; dead slot = active player whose "
              "club played but who did not. Early-season pace is noisy. Checked against the standings for all %d "
              "teams." % len(rows))
    full = [head, d1, d2, d3, "", "LOWEST 5 by lineup GP:"] + low + ["", method, witness_line, canary_line, ""]
    full.append("ALL TEAMS (lineup GP, off-lineup GP, dead slots, slot-games on offer, pace, projected, empty active "
                "slot-days, estimated days):")
    for r in sorted(rows, key=lambda r: (r["lineup"], r["name"])):
        full.append("  %-26s %4d %4d %4d %4d %7s %7s %3d %2d" % (
            r["name"], r["lineup"], r["off"], r["dead"], r["avail"],
            pct(r["pace"]) if r["pace"] is not None else "n/a",
            "{:,.0f}".format(r["proj"]) if r["proj"] is not None else "n/a", r["empty"], r["est"]))
    post = [head, d1, d2, d3, "", "Lowest 5:"] + low + ["", method]
    return full, post


# ---------------------------------------------------------------- state

def load_state(path):
    f = pathlib.Path(path)
    if not f.exists():
        return {"last_period": 0, "gp": {}, "teams": {}}
    try:
        d = json.loads(f.read_text())
        if "last_period" in d and "gp" in d and "teams" in d:
            return d
    except ValueError:
        pass
    sys.exit("state %s exists but is unreadable. Refusing to rebuild over it silently; fix or delete by hand." % f)


def save_state(path, state):
    f = pathlib.Path(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")


def main():
    args = sys.argv[1:]
    post_out = args[args.index("--post-out") + 1] if "--post-out" in args else None
    state_path = args[args.index("--state") + 1] if "--state" in args else str(STATE)
    record = "--no-record" not in args
    through = int(args[args.index("--through") + 1]) if "--through" in args else None   # testing: cap the period

    from fantraxapi import FantraxAPI
    api = FantraxAPI(LEAGUE_ID)
    teams = api.teams
    if len(teams) != 32:
        sys.exit("Fantrax returned %d teams, expected 32. Refusing to report." % len(teams))
    standings, last_done, start = read_standings(api, teams)
    if through is not None:
        last_done = min(last_done, through)
    if len(standings) != 32:
        sys.exit("standings carried %d teams, expected 32. Refusing to report." % len(standings))

    state = load_state(state_path)
    if state["last_period"] > last_done:
        sys.exit("state is at period %d but the standings are only through %d. Refusing to report."
                 % (state["last_period"], last_done))
    if state["last_period"] == last_done:
        print("#DIGEST " + hashlib.sha256(("nothing-new|%d" % last_done).encode()).hexdigest()[:16])
        print("No new completed period: state and standings are both through period %d." % last_done)
        return 0

    names = {t.team_id: t.name for t in teams}
    cum = {tid: dict(state["teams"].get(tid, {"lineup": 0, "off": 0, "dead": 0, "avail": 0, "est": 0, "empty": 0}))
           for tid in names}
    prev_gp = dict(state["gp"])
    first = state["last_period"] == 0
    new_periods = list(range(state["last_period"] + 1, last_done + 1))
    for n in new_periods:
        cur, empty = read_period(api, teams, n)
        if len(cur) < 1400:
            sys.exit("period %d carried only %d rostered players, expected 1,400+. Refusing to report." % (n, len(cur)))
        day, prev_gp = day_stats(prev_gp, cur, empty, first and n == 1)
        for tid in names:
            for k in ("lineup", "off", "dead", "avail", "est", "empty"):
                cum[tid][k] = cum[tid].get(k, 0) + day[tid][k]
        print("period %d read: %d players" % (n, len(cur)), file=sys.stderr)

    bad, canary = witness_canary(cum, standings)
    if bad:
        sys.exit("WITNESS FAILED through period %d: cumulative lineup GP differs from the standings GP for %d "
                 "team(s): %s. Refusing to report; state not saved." % (
                     last_done, len(bad), "; ".join("%s computed %d vs standings %d (est %d)" % (
                         names[t], cum[t]["lineup"], standings[t], cum[t].get("est", 0)) for t in bad[:6])))
    witness_line = ("witness passed: for all %d teams, cumulative lineup GP through period %d equals the standings GP"
                    % (len(standings), last_done))

    new_state = {"last_period": last_done, "gp": prev_gp,
                 "teams": {tid: dict(cum[tid], name=names[tid]) for tid in names}}
    digest = hashlib.sha256(json.dumps(
        [last_done] + [[tid, cum[tid]["lineup"], cum[tid]["dead"], cum[tid]["avail"]] for tid in sorted(names)]
    ).encode()).hexdigest()[:16]
    print("#DIGEST " + digest)
    full, post = render(new_state, start, witness_line, canary, new_periods)
    print("\n".join(full))
    if post_out:
        txt = "\n".join(post)
        if len(txt) > POST_MAX:
            txt = txt[:POST_MAX - 40].rsplit("\n", 1)[0] + "\n... (truncated)"
        pathlib.Path(post_out).write_text("#DIGEST " + digest + "\n" + txt + "\n")
    if record:
        save_state(state_path, new_state)
    return FINDINGS


if __name__ == "__main__":
    sys.exit(main())
