#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Player-location table. READ ONLY. Local use: the CSV goes to a path you give, default
/private/tmp/player_locations.csv, and NEVER into the repo (*.csv is in .gitignore).

One row per player on CapWages' 32 NHL team pages (the pp_salary_watch reader, one page
per team, at most one request a second). Columns:
  name, nhl_club, status, rights_holder, aav, contract_end, powerplay_team, fantrax_salary

  status         CapWages' own word where it has one (NHL, Minor, Junior, LTIR ...); a
                 draft-rights reserve is "Prospect", an AHL-affiliate player with no word
                 is "Minor", an inactive-list player is "Inactive".
  rights_holder  the team page the player sits on, except a reserve (his drafting club)
                 and an AHL-affiliate player (CapWages' nhlRights, blank if none).
  aav            the current season's AAV, else the next season's, else blank (no contract).
  contract_end   the last season of the contract that covers that AAV.
  powerplay_team Fantrax team.short (3 letters) when the player is rostered, matched by
                 the same name rule as pp_salary_watch (exact fold, or surname plus NHL
                 club with a first-name PREFIX; ambiguous is left blank, never guessed).
  fantrax_salary Fantrax's salary string, expiry decimal included.

CANARY: Celebrini and Hutson must be in the table with a contract AAV, and the
rostered-count must equal the number of Fantrax players the match resolved (a reader
that cannot find a player Fantrax rostered returns a confident, wrong blank).

  usage: pp_player_locations.py [out.csv]
"""
import sys, csv, pathlib, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pp_salary_watch as sw

DEFAULT = "/private/tmp/player_locations.csv"


def status_of(e):
    sec = e["section"].split("/")[0]
    if sec == "reserves":
        return "Prospect"
    if e.get("status"):
        return e["status"]
    return {"ahl": "Minor", "inactive": "Inactive"}.get(sec, sec.title())


def rights_of(e):
    sec = e["section"].split("/")[0]
    if sec == "reserves":
        return e.get("drafted_by") or e["page"]
    if sec == "ahl":
        return e.get("nhl_rights") or ""
    return e["page"]


def contract_of(e):
    """-> (aav, last season) of the contract covering this season, else the next one."""
    for want in (sw.SEASON_START, sw.SEASON_START + 1):
        cov = [k for k in e["contracts"] if any(sw.season_start(s) == want for s in k["seasons"])]
        if cov:
            k = max(cov, key=lambda k: max(sw.season_start(s) for s in k["seasons"]))
            s = [x for x in k["seasons"] if sw.season_start(x) == want][0]
            return int(k["seasons"][s]), max(k["seasons"], key=sw.season_start)
    return "", ""


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    if pathlib.Path(out).resolve().is_relative_to(pathlib.Path(__file__).resolve().parents[1]):
        sys.exit("Refusing to write a league data file inside the repo: %s" % out)
    players, tri = sw.read_capwages()
    if len(tri) != 32:
        sys.exit("CapWages answered for %d of 32 teams. Refusing." % len(tri))
    rows, _ = sw.read_fantrax()
    idx = sw.Index(players)
    owner, ambiguous = {}, 0
    for r in rows:
        e, how = idx.lookup(r["name"], r["nhl"])
        if e is None:
            ambiguous += how == "AMBIGUOUS"
            continue
        if e["slug"] in owner:                      # two Fantrax players, one slug: neither is certain
            owner[e["slug"]] = None
        else:
            owner[e["slug"]] = r
    owner = {k: v for k, v in owner.items() if v is not None}
    table = []
    for sl, e in sorted(players.items(), key=lambda kv: kv[1]["names"][0]):
        aav, end = contract_of(e)
        r = owner.get(sl)
        table.append([e["names"][0], e["tri"], status_of(e), rights_of(e), aav, end,
                      r["short"] if r else "", r["salary"] if r else ""])
    byname = {t[0]: t for t in table}
    for n in ("Macklin Celebrini", "Lane Hutson"):
        if n not in byname or byname[n][4] == "":
            sys.exit("CANARY FAILED: %s missing or without a contract AAV. Refusing." % n)
    ros = sum(1 for t in table if t[6])
    if ros != len(owner):
        sys.exit("CANARY FAILED: table shows %d rostered, match resolved %d." % (ros, len(owner)))
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "nhl_club", "status", "rights_holder", "aav", "contract_end", "powerplay_team",
                    "fantrax_salary"])
        w.writerows(table)
    import collections
    st = collections.Counter(t[2] for t in table)
    print("wrote %d players to %s; PowerPlay-rostered %d of %d Fantrax players (%d ambiguous, left blank)"
          % (len(table), out, ros, len(rows), ambiguous))
    print("status distribution: " + ", ".join("%s %d" % kv for kv in st.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
