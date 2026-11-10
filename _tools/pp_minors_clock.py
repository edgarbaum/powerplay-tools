#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Minors clock. READ ONLY.

LEAGUE RULE 4.3 says a player with 83 or more career NHL regular-season games (51 or
more for a goalie) is minors-INELIGIBLE. A PROPOSAL going to a vote adds a clock: such a
player sitting in a Minors slot (Fantrax statusId 9) must come up after 10 NHL games or
30 days in the minors, whichever comes first.

This tool tracks that clock for every team, so the vote can be judged on real numbers.

WHO IS INELIGIBLE: NHL public API, never Fantrax. Fantrax's own minors flag is OFF and
marks everyone eligible, so eligibleStatusIds is not used. Name match is an exact fold
over search.d3.nhle.com, or surname plus the same NHL club with the first name a PREFIX
of the other (Ben/Benjamin yes, Daniil/Dmitri no). Never surname plus initial. Career
games come from api-web.nhle.com landing careerTotals.regularSeason.gamesPlayed, which
already includes this season. A name that stays ambiguous is counted UNRESOLVED, never
guessed.

THE CLOCK, per ineligible player in a Minors slot:
  days   today minus start
  games  his NHL regular-season games (per-game log, season 20262027) dated on or after start
  start  the later of the date he was first seen in minors and ADOPTED.
         ADOPTED is None until the proposal passes. While None the clocks are "proposed"
         and start at the first run that saw him. First-seen dates persist in
         _tools/state/minors_clock.json; a player who leaves the minors is dropped, so
         the clock restarts if he returns.
  Limits: 30 days, 10 games. Flags: 80% of either limit, and DUE at 100% or more.

CANARIES (both must pass or the run refuses to report)
  1. known-ineligible: Mike Reilly (about 463 career GP, a skater) must classify as
     ineligible through the real lookup path.
  2. injected player: Reilly is injected into the minors roster as a synthetic row; the
     tracker must produce exactly one more tracked player, with a zero-day clock.

EXIT CODES: #DIGEST is the first line of stdout. 3 = a report to post, 0 = nothing to
track, anything else non-zero = a crash or a refused canary. The digest covers who is
tracked and each flag level, not the day counts, so the post repeats only on change.

  usage: pp_minors_clock.py [--post-out FILE] [--no-record]
"""
import warnings, json, sys, time, hashlib, datetime, pathlib, unicodedata, urllib.request, urllib.parse
warnings.filterwarnings("ignore")

FINDINGS = 3
HERE = pathlib.Path(__file__).parent
STATE = HERE / "state" / "minors_clock.json"
CAREER = HERE / "state" / "nhl_career.json"
ADOPTED = None                      # datetime.date once the proposal passes; None = not yet voted
MIN_GP, MIN_GP_G = 83, 51           # rule 4.3: career NHL regular-season games
LIMIT_DAYS, LIMIT_GAMES = 30, 10
WARN = 0.8
GAME_SEASON = "20262027"
POST_MAX = 1800
WIDTH = 50
REFRESH_DAYS = 7                    # a below-threshold or unknown career record is re-read weekly
MINORS = "9"
UA = {"User-Agent": "powerplay-tools/1.0"}
CANARIES = ["Macklin Celebrini", "Lane Hutson", "Brandt Clarke"]
KNOWN_INELIGIBLE = ("Mike Reilly", "CAR")


def _league():
    f = HERE / "config" / "league.json"
    return json.loads(f.read_text())["league_id"] if f.exists() else "aizqwpvxmoc9uxas"


# ---------------------------------------------------------------- NHL reader

def _get(u):
    last = None
    for attempt in range(5):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25).read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except Exception as e:                                # noqa: BLE001
            last = e
        time.sleep(3 * (attempt + 1))
    raise RuntimeError("NHL API failed 5 times for %s: %s" % (u.split("?")[0], last))


def fold(x):
    return "".join(c for c in unicodedata.normalize("NFKD", x or "") if not unicodedata.combining(c)).strip().lower()


def pick(name, club, hits):
    """-> (hit|None, how). Exact fold, else surname + same NHL club + first-name PREFIX."""
    want = fold(name)
    ex = [h for h in hits if fold(h.get("name")) == want]
    if len(ex) > 1 and club:
        ex = [h for h in ex if club in (h.get("teamAbbrev"), h.get("lastTeamAbbrev"))] or ex
    if len(ex) == 1:
        return ex[0], "exact"
    if len(ex) > 1:
        return None, "AMBIGUOUS"
    wp = want.split()
    if len(wp) >= 2 and club and club != "(N/A)":
        def ok(h):
            op = fold(h.get("name")).split()
            if len(op) < 2 or op[-1] != wp[-1]:
                return False
            if club not in (h.get("teamAbbrev"), h.get("lastTeamAbbrev")):
                return False
            a, b = sorted([op[0].replace(".", ""), wp[0].replace(".", "")], key=len)
            return len(a) >= 2 and b.startswith(a)
        m = [h for h in hits if ok(h)]
        if len(m) == 1:
            return m[0], "prefix"
        if len(m) > 1:
            return None, "AMBIGUOUS"
    return None, "none"


def nhl_career(name, club):
    """-> {pid, gp, pos, how}. gp None = no NHL record or unresolved."""
    hits = []
    for active in ("true", "false"):
        r = _get("https://search.d3.nhle.com/api/v1/search/player?culture=en-us&active=%s&limit=12&q=%s"
                 % (active, urllib.parse.quote(name)))
        hits += r or []
        time.sleep(0.2)
    seen, uniq = set(), []
    for h in hits:
        if h["playerId"] not in seen:
            seen.add(h["playerId"])
            uniq.append(h)
    h, how = pick(name, club, uniq)
    if h is None:
        return {"pid": None, "gp": None, "pos": None, "how": how}
    land = _get("https://api-web.nhle.com/v1/player/%s/landing" % h["playerId"]) or {}
    gp = ((land.get("careerTotals") or {}).get("regularSeason") or {}).get("gamesPlayed")
    time.sleep(0.2)
    return {"pid": h["playerId"], "gp": gp, "pos": land.get("position") or h.get("positionCode"), "how": how}


def threshold(pos):
    return MIN_GP_G if pos == "G" else MIN_GP


def ineligible(rec):
    return rec["gp"] is not None and rec["gp"] >= threshold(rec["pos"])


def career_of(name, club, cache, today, stats):
    """Cached. An ineligible verdict is permanent (games only go up); anything else is
    re-read every REFRESH_DAYS."""
    k = "%s|%s" % (name, club)
    c = cache.get(k)
    if c and (ineligible(c) or (today - datetime.date.fromisoformat(c["asof"])).days < REFRESH_DAYS):
        stats["cached"] += 1
        return c
    rec = nhl_career(name, club)
    rec["asof"] = today.isoformat()
    cache[k] = rec
    stats["fetched"] += 1
    return rec


def games_since(pid, start):
    """NHL regular-season games dated on or after start."""
    gl = _get("https://api-web.nhle.com/v1/player/%s/game-log/%s/2" % (pid, GAME_SEASON))
    time.sleep(0.2)
    return sum(1 for g in (gl or {}).get("gameLog", []) if g.get("gameDate", "") >= start.isoformat())


# ---------------------------------------------------------------- Fantrax reader

def read_minors(api):
    """-> (minors rows, all main-roster names). A row: sid, name, team, nhl."""
    rows, names = [], set()
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
        for tbl in raw["tables"]:
            for r in tbl.get("rows", []):
                sc = r.get("scorer") or {}
                if not sc.get("name"):
                    continue
                names.add(sc["name"].strip())
                if str(r.get("statusId")) == MINORS:
                    rows.append({"sid": sc["scorerId"], "name": sc["name"].strip(), "team": t.short or t.name,
                                 "nhl": sc.get("teamShortName") or ""})
        time.sleep(0.12)
    return rows, names


# ---------------------------------------------------------------- the clock

def clock_start(first_seen):
    return max(first_seen, ADOPTED) if ADOPTED else first_seen


def track(minors, lookup, first_seen, today, games_fn):
    """Pure given its inputs. -> list of tracked dicts for the minors-INELIGIBLE rows.
    first_seen is updated in place: new minors players are stamped today."""
    out = []
    for r in minors:
        first_seen.setdefault(r["sid"], today.isoformat())
        rec = lookup(r)
        if not ineligible(rec):
            continue
        start = clock_start(datetime.date.fromisoformat(first_seen[r["sid"]]))
        days = max(0, (today - start).days)
        games = games_fn(rec["pid"], start)
        pct = max(days / LIMIT_DAYS, games / LIMIT_GAMES)
        out.append({"sid": r["sid"], "name": r["name"], "team": r["team"], "nhl": r["nhl"], "gp": rec["gp"],
                    "pos": rec["pos"], "start": start.isoformat(), "days": days, "games": games, "pct": pct,
                    "flag": "DUE" if pct >= 1 else "80%" if pct >= WARN else ""})
    return out


def canaries(minors, lookup, first_seen, today, games_fn, base):
    name, club = KNOWN_INELIGIBLE
    rec = nhl_career(name, club)
    if not ineligible(rec):
        sys.exit("CANARY FAILED: %s must classify as minors-ineligible, got career GP %s (%s). Refusing to report."
                 % (name, rec["gp"], rec["how"]))
    fake = {"sid": "CANARY-INJECTED", "name": name, "team": "ZZZ", "nhl": club}
    probe_first = {}
    got = track(list(minors) + [fake], lambda r: rec if r is fake else lookup(r), probe_first, today, games_fn)
    mine = [x for x in got if x["sid"] == "CANARY-INJECTED"]
    if len(got) != base + 1 or len(mine) != 1 or mine[0]["days"] != 0:
        sys.exit("INJECTED-PLAYER CANARY FAILED: %d tracked with the probe, expected %d, probe rows %d. "
                 "Refusing to report." % (len(got), base + 1, len(mine)))
    return "canaries ok: %s GP %d ineligible; injected row fired (+1)" % (name, rec["gp"])


# ---------------------------------------------------------------- render

def fit(s):
    return s[:WIDTH]


def render_post(tr, digest, unresolved, today, mode, n_min):
    o = ["#DIGEST " + digest,
         fit("MINORS CLOCK %s %s" % ("(PROPOSED)" if mode == "proposed" else "(ADOPTED)", today)),
         fit("Rule 4.3: 83 GP (G 51) = ineligible."),
         fit("Up at 10 games or 30 days."),
         fit("%s." % ("Not voted: clocks start today" if mode == "proposed" else "Clock from %s" % ADOPTED)),
         fit("Ineligible in minors: %d of %d" % (len(tr), n_min))]
    b = [0, 0, 0, 0]
    for x in tr:
        d = x["days"]
        b[0 if d < 10 else 1 if d < 24 else 2 if d < 30 else 3] += 1
    o.append(fit("Days: 0-9 %d, 10-23 %d, 24-29 %d, 30+ %d" % tuple(b)))
    o.append(fit("Flags: DUE %d, 80%% %d" % (sum(1 for x in tr if x["flag"] == "DUE"),
                                              sum(1 for x in tr if x["flag"] == "80%"))))
    if unresolved:
        o.append(fit("Name unresolved, not tracked: %d" % unresolved))
    o.append("")
    o.append("TM  PLAYER            DAYS   GMS  FLAG")
    used = len("\n".join(o))
    rows = sorted(tr, key=lambda x: (-x["pct"], x["team"], x["name"]))
    shown = 0
    for x in rows:
        line = fit("%-3s %-17s %2d/%d %2d/%d %s" % (x["team"], x["name"][:17], x["days"], LIMIT_DAYS,
                                                    x["games"], LIMIT_GAMES, x["flag"])).rstrip()
        if used + len(line) + 1 > POST_MAX - 40:
            break
        o.append(line)
        used += len(line) + 1
        shown += 1
    if shown < len(rows):
        o.append(fit("... and %d more in the run log" % (len(rows) - shown)))
    return "\n".join(o) + "\n"


def main():
    args = sys.argv[1:]
    post_out = args[args.index("--post-out") + 1] if "--post-out" in args else None
    record = "--no-record" not in args
    today = datetime.date.today()
    from fantraxapi import FantraxAPI
    api = FantraxAPI(_league())
    if len(api.teams) != 32:
        sys.exit("Fantrax returned %d teams, expected 32. Refusing to report." % len(api.teams))
    minors, names = read_minors(api)
    miss = [c for c in CANARIES if c not in names]
    if miss:
        sys.exit("CANARY FAILED: Fantrax read %d players but not %s. Refusing to report." % (len(names), miss))
    if not minors:
        sys.exit("Fantrax showed no player in a Minors slot on any team: UNKNOWN, not clean. Refusing to report.")

    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    first_seen = st.get("first_seen", {})
    cache = json.loads(CAREER.read_text()) if CAREER.exists() else {}
    stats = {"cached": 0, "fetched": 0}
    unresolved = []

    def lookup(r):
        rec = career_of(r["name"], r["nhl"], cache, today, stats)
        if rec["how"] == "AMBIGUOUS":
            unresolved.append(r["name"])
        return rec

    # Career lookups are about 3 s each and there are around a thousand names, so they run in
    # a small pool (6 workers, each request still spaced). Cached verdicts make later runs short.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda r: career_of(r["name"], r["nhl"], cache, today, stats), minors))

    # forget players who left the minors, so a return restarts the clock
    live = {r["sid"] for r in minors}
    first_seen = {k: v for k, v in first_seen.items() if k in live}
    gcache = {}

    def games_fn(pid, start):
        k = (pid, start)
        if k not in gcache:
            gcache[k] = games_since(pid, start)
        return gcache[k]

    fs_before = dict(first_seen)
    tr = track(minors, lookup, first_seen, today, games_fn)
    unresolved = sorted(set(unresolved))
    c = canaries(minors, lookup, dict(fs_before), today, games_fn, len(tr))

    mode = "adopted" if ADOPTED else "proposed"
    sig = sorted("%s|%s" % (x["sid"], x["flag"]) for x in tr)
    digest = hashlib.sha256(("%s\n%s" % (mode, "\n".join(sig))).encode()).hexdigest()[:16]
    print("#DIGEST " + digest)
    print("Minors clock %s, %s. %d players in Minors slots, %d minors-ineligible tracked, %d name(s) unresolved."
          % (mode, today, len(minors), len(tr), len(unresolved)))
    print(c)
    print("career lookups: %d cached, %d fetched" % (stats["cached"], stats["fetched"]))
    if unresolved:
        print("unresolved (ambiguous name): " + ", ".join(unresolved))
    for x in sorted(tr, key=lambda x: (-x["pct"], x["team"], x["name"])):
        print("%s %-22s %s GP%d  start %s  %d/%d days  %d/%d games  %s"
              % (x["team"], x["name"], x["pos"], x["gp"], x["start"], x["days"], LIMIT_DAYS, x["games"], LIMIT_GAMES,
                 x["flag"]))
    if post_out:
        pathlib.Path(post_out).write_text(render_post(tr, digest, len(unresolved), today, mode, len(minors)))
    if record:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"first_seen": first_seen, "adopted": str(ADOPTED) if ADOPTED else None},
                                    indent=1, sort_keys=True) + "\n")
        CAREER.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n")
    return FINDINGS if tr else 0


if __name__ == "__main__":
    sys.exit(main())
