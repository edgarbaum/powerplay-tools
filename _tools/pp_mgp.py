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

LOST GAMES: why a team's lineup GP falls short of the games its players played.
  played_pool   players whose GP delta that day is above 0, by position F, D, G (a player
                eligible at two positions, F,D, goes wherever room remains)
  best_major    min(12,F)+min(6,D)+min(2,G) over pool players in Active or Reserve
  best_all      the same, also counting Minors (status 9). IR never counts.
  avoidable_loss        best_all - lineup_gp: games a better lineup could have had
  avoidable_loss_major  best_major - lineup_gp (moving a player up from the Minors may
                        have limits, so this is the stricter figure)
  capacity_loss         played_total - best_all, played_total = every non-IR player who
                        played: games lost purely because only 12/6/2 can count a day
  A synthetic day (14 forwards played) must give best 12 and capacity_loss 2.

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
POST_MAX = 1850
VERSION = 2                                               # 2 added the lost-games fields; an older state is rebuilt
CAPS = {"F": 12, "D": 6, "G": 2}
KEYS = ("lineup", "off", "dead", "avail", "est", "empty", "best_major", "best_all", "played")
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
                                           "opp": str(c[oi].get("content") or "").strip(), "name": sc["name"],
                                           "pos": sc.get("posShortNames") or ""}
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

def pos_key(pos):
    t = sorted({x.strip() for x in (pos or "").split(",")} & set(CAPS))
    return "".join(t) if t else "F"


def best_lineup(pool):
    """pool: Counter of position keys ('F','D','G','DF') -> players. Maximum that can
    count under 12 F / 6 D / 2 G. A two-position player takes whichever room remains."""
    f, d, g, fd = pool["F"], pool["D"], pool["G"], pool["DF"]
    fu, du = min(CAPS["F"], f), min(CAPS["D"], d)
    flex = min(fd, (CAPS["F"] - fu) + (CAPS["D"] - du))
    other = sum(v for k, v in pool.items() if k not in ("F", "D", "G", "DF"))     # unexpected combos: first token
    return fu + du + flex + min(CAPS["G"], g) + min(0, other)


def day_stats(prev_gp, cur, empty, first_day):
    """-> ({tid: counts for the day}, new_gp map). Pure."""
    out = collections.defaultdict(collections.Counter)
    pools = collections.defaultdict(lambda: {"major": collections.Counter(), "all": collections.Counter()})
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
        if d and d > 0 and p["status"] in ("1", "2", "9"):    # played, and not on IR
            k = pos_key(p.get("pos"))
            pools[p["tid"]]["all"][k] += 1
            c["played"] += 1
            if p["status"] in ("1", "2"):
                pools[p["tid"]]["major"][k] += 1
    for tid, pl in pools.items():
        out[tid]["best_major"] += best_lineup(pl["major"])
        out[tid]["best_all"] += best_lineup(pl["all"])
    return out, {sid: p["gp"] for sid, p in cur.items()}


def lost_canary():
    """A synthetic day through the real day_stats: 14 forwards played (12 active, 2 on
    reserve) and a D and a G in the minors who did not. Best is 12 and capacity_loss is 2."""
    cur = {}
    for i in range(14):
        cur["f%d" % i] = {"tid": "T", "status": "1" if i < 12 else "2", "gp": 1, "opp": "X", "name": "f", "pos": "F"}
    cur["d0"] = {"tid": "T", "status": "9", "gp": 0, "opp": "", "name": "d", "pos": "D"}
    day, _ = day_stats({}, cur, {}, True)
    c = day["T"]
    got = (c["lineup"], c["best_major"], c["best_all"], c["played"] - c["best_all"], c["best_all"] - c["lineup"])
    if got != (12, 12, 12, 2, 0):
        sys.exit("LOST-GAMES CANARY FAILED: 14 forwards played gave (lineup, best_major, best_all, capacity_loss, "
                 "avoidable) = %s, expected (12, 12, 12, 2, 0). Refusing to report." % (got,))
    # 8 minors defensemen who played plus one D/F in an active slot: only 6 D can count,
    # the D/F takes the forward room, so best_all 7, best_major 1, played 9 (capacity 2)
    cur2 = {"d%d" % i: {"tid": "T", "status": "9", "gp": 1, "opp": "X", "name": "d", "pos": "D"} for i in range(8)}
    cur2["x"] = {"tid": "T", "status": "1", "gp": 1, "opp": "X", "name": "x", "pos": "D,F"}
    c2 = day_stats({}, cur2, {}, True)[0]["T"]
    got2 = (c2["best_all"], c2["best_major"], c2["lineup"], c2["played"])
    if got2 != (7, 1, 1, 9):
        sys.exit("LOST-GAMES CANARY FAILED: 8 minors D plus one D/F gave (best_all, best_major, lineup, played) = %s, "
                 "expected (7, 1, 1, 9). Refusing to report." % (got2,))
    return ("lost-games canary passed: a synthetic day with 14 forwards played gave best 12 and capacity_loss 2 "
            "(avoidable 0); 8 minors D plus one D/F gave best_all 7, best_major 1, played 9")


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
        av = v["avail"]
        pace = v["lineup"] / av if av else None
        bpace = v["best_all"] / av if av else None
        rows.append(dict(v, tid=tid, pace=pace, proj=pace * CEILING if pace is not None else None,
                         bpace=bpace, bproj=bpace * CEILING if bpace is not None else None,
                         avoid=v["best_all"] - v["lineup"], avoid_major=v["best_major"] - v["lineup"],
                         cap=v["played"] - v["best_all"]))
    return rows


def render(state, start, witness_line, canary_lines, new_periods):
    rows = rows_of(state)
    last = state["last_period"]
    thru = start + datetime.timedelta(days=last - 1)
    lo5 = sorted(rows, key=lambda r: (r["lineup"], r["pace"] if r["pace"] is not None else 0, r["name"]))[:5]
    hi5 = sorted(rows, key=lambda r: (-r["avoid"], r["name"]))[:5]
    paced = [r for r in rows if r["pace"] is not None]
    under = [r for r in paced if r["proj"] < MIN_MGP]
    bunder = [r for r in paced if r["bproj"] < MIN_MGP]
    pct = lambda x: "%.1f%%" % (100 * x)
    n0 = lambda x: "{:,.0f}".format(x)
    head = ("PowerPlay MGP tracker - through %s (period %d of %d), %d teams. Rule 5.5: %s man-games in ACTIVE slots; "
            "ceiling %s, so %s of it." % (thru.strftime("%b %d"), last, SEASON_DAYS, len(rows), "{:,}".format(MIN_MGP),
                                          "{:,}".format(CEILING), pct(MIN_MGP / float(CEILING))))
    d1 = "Lineup GP: " + shape([r["lineup"] for r in rows], str)
    d2 = "Pace (lineup GP / active slot-games on offer): " + shape([r["pace"] for r in paced], pct) + \
         ".  Needed: %s." % pct(MIN_MGP / float(CEILING))
    d3 = "Projected season total (pace x %s): %s.  Below %s: %d of %d teams." % (
        "{:,}".format(CEILING), shape([r["proj"] for r in paced], n0), "{:,}".format(MIN_MGP), len(under), len(paced))
    d4 = "Best-possible projection (best lineup's pace, same denominator): %s.  Below %s: %d of %d." % (
        shape([r["bproj"] for r in paced], n0), "{:,}".format(MIN_MGP), len(bunder), len(paced))
    d5 = "Avoidable loss (best lineup incl. Minors - actual): " + shape([r["avoid"] for r in rows], str)
    d5b = "Avoidable loss, Active+Reserve only: " + shape([r["avoid_major"] for r in rows], str)
    d6 = "Capacity loss (played non-IR - best lineup; only 12F/6D/2G can count): " + shape([r["cap"] for r in rows], str)
    low = ["%d. %s: lineup %d, dead %d of %d, projected %s" % (
        i, r["name"], r["lineup"], r["dead"], r["avail"], n0(r["proj"]) if r["proj"] is not None else "n/a")
        for i, r in enumerate(lo5, 1)]
    top = ["%d. %s: avoidable %d (Active+Reserve %d), lineup %d, capacity %d, dead %d" % (
        i, r["name"], r["avoid"], r["avoid_major"], r["lineup"], r["cap"], r["dead"]) for i, r in enumerate(hi5, 1)]
    method = ("Method: GP by Active players from daily rosters, checked against the standings for all %d teams. "
              "Avoidable = a better lineup from players who played, Minors included. Best projection can pass 1,680. "
              "Early-season pace is noisy." % len(rows))
    full = [head, d1, d2, d3, d4, d5, d5b, d6, "", "LOWEST 5 by lineup GP:"] + low + \
           ["", "LARGEST 5 by avoidable loss:"] + top + ["", method, witness_line] + list(canary_lines) + [""]
    full.append("ALL TEAMS: lineup GP, off-lineup GP, dead slots, slot-games on offer, avoidable (all), avoidable "
                "(Active+Reserve), capacity loss, pace, projected, best projection, empty active slot-days, estimated days:")
    for r in sorted(rows, key=lambda r: (r["lineup"], r["name"])):
        full.append("  %-26s %4d %4d %4d %4d %4d %4d %4d %7s %7s %7s %3d %2d" % (
            r["name"], r["lineup"], r["off"], r["dead"], r["avail"], r["avoid"], r["avoid_major"], r["cap"],
            pct(r["pace"]) if r["pace"] is not None else "n/a", n0(r["proj"]) if r["proj"] is not None else "n/a",
            n0(r["bproj"]) if r["bproj"] is not None else "n/a", r["empty"], r["est"]))
    short = lambda t: t.replace("Lineup GP: ", "Lineup GP ").replace("Projected season total (pace x 1,680)", "Projected (pace x 1,680)") \
        .replace("Best-possible projection (best lineup's pace, same denominator)", "Best-possible projection") \
        .replace("Avoidable loss (best lineup incl. Minors - actual)", "Avoidable loss") \
        .replace("Capacity loss (played non-IR - best lineup; only 12F/6D/2G can count)", "Capacity loss") \
        .replace("Pace (lineup GP / active slot-games on offer)", "Pace (lineup GP / active slot-games)")
    post = [short(head).replace("tracker - ", "").replace("PowerPlay MGP ", "PowerPlay MGP "), short(d1), short(d2), short(d3),
            short(d4), short(d5), short(d6), "", "Lowest 5 by lineup GP:"] + low + ["", "Largest 5 by avoidable loss:"] + top + \
           ["", method]
    return full, post


# ---------------------------------------------------------------- state

def load_state(path):
    f = pathlib.Path(path)
    if not f.exists():
        return {"last_period": 0, "gp": {}, "teams": {}}
    try:
        d = json.loads(f.read_text())
        if "last_period" in d and "gp" in d and "teams" in d:
            if d.get("version") != VERSION:
                print("state is version %s, this tool needs %d: rebuilding from period 1" % (d.get("version"), VERSION),
                      file=sys.stderr)
                return {"last_period": 0, "gp": {}, "teams": {}}
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

    lost = lost_canary()                                      # pure: fails fast, before any request
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
    nothing_new = state["last_period"] == last_done
    if nothing_new and not post_out:
        print("#DIGEST " + hashlib.sha256(("nothing-new|%d" % last_done).encode()).hexdigest()[:16])
        print("No new completed period: state and standings are both through period %d." % last_done)
        return 0
    # With --post-out and nothing new, still re-witness the saved totals against the standings and
    # write the season-to-date report: the weekly channel post needs it even when the daily run
    # already processed today (2026-10-05: the weekly step failed for want of a report).

    names = {t.team_id: t.name for t in teams}
    cum = {tid: dict({k: 0 for k in KEYS}, **state["teams"].get(tid, {})) for tid in names}
    prev_gp = dict(state["gp"])
    first = state["last_period"] == 0
    new_periods = list(range(state["last_period"] + 1, last_done + 1))
    neg = []
    for n in new_periods:
        cur, empty = read_period(api, teams, n)
        if len(cur) < 1400:
            sys.exit("period %d carried only %d rostered players, expected 1,400+. Refusing to report." % (n, len(cur)))
        day, prev_gp = day_stats(prev_gp, cur, empty, first and n == 1)
        for tid in names:
            for k in KEYS:
                cum[tid][k] = cum[tid].get(k, 0) + day[tid][k]
            if day[tid]["best_major"] < day[tid]["lineup"]:
                neg.append("%s period %d (best %d < lineup %d)" % (names[tid], n, day[tid]["best_major"], day[tid]["lineup"]))
        print("period %d read: %d players" % (n, len(cur)), file=sys.stderr)

    bad, canary = witness_canary(cum, standings)
    if bad:
        sys.exit("WITNESS FAILED through period %d: cumulative lineup GP differs from the standings GP for %d "
                 "team(s): %s. Refusing to report; state not saved." % (
                     last_done, len(bad), "; ".join("%s computed %d vs standings %d (est %d)" % (
                         names[t], cum[t]["lineup"], standings[t], cum[t].get("est", 0)) for t in bad[:6])))
    if neg:
        sys.exit("AVOIDABLE LOSS WENT NEGATIVE for %d team-day(s), e.g. %s. The best lineup cannot be worse than the "
                 "one actually used, so the position model is wrong. Refusing to report." % (len(neg), "; ".join(neg[:3])))
    witness_line = ("witness passed: for all %d teams, cumulative lineup GP through period %d equals the standings GP"
                    % (len(standings), last_done))

    new_state = {"version": VERSION, "last_period": last_done, "gp": prev_gp,
                 "teams": {tid: dict(cum[tid], name=names[tid]) for tid in names}}
    digest = hashlib.sha256(json.dumps(
        [last_done] + [[tid, cum[tid]["lineup"], cum[tid]["dead"], cum[tid]["avail"], cum[tid]["best_all"], cum[tid]["played"]]
                    for tid in sorted(names)]
    ).encode()).hexdigest()[:16]
    print("#DIGEST " + digest)
    full, post = render(new_state, start, witness_line, [canary, lost], new_periods)
    print("\n".join(full))
    if post_out:
        txt = "\n".join(post)
        if len(txt) > POST_MAX:
            txt = txt[:POST_MAX - 40].rsplit("\n", 1)[0] + "\n... (truncated)"
        pathlib.Path(post_out).write_text("#DIGEST " + digest + "\n" + txt + "\n")
    if nothing_new:
        return 0                                           # report written; state unchanged
    if record:
        save_state(state_path, new_state)
    return FINDINGS


if __name__ == "__main__":
    sys.exit(main())
