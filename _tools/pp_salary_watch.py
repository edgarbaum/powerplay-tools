#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nightly salary-change finder. READ ONLY: it uploads nothing and does NOT decide
the 50% draft-discount; it finds what the commissioner has to look at.

WHAT THE TWO SOURCES SAY
  Fantrax  getTeamRosterInfo salary "850,000.27": the whole part is the league
           charge, the 2-digit decimal is the expiry year (.27 = through 2026-27).
           "1.00" is the placeholder for an unsigned player.
  CapWages one page per NHL team carries every contract of every player on it
           (see --coverage-audit, which re-proves that against the player pages).
           32 requests instead of about 1,600, at most one per second.

THE LEAGUE CHARGE is the AAV, or AAV/2 for a drafted player still held by his
drafting team. Nothing in the source says which, so a charge is accepted if it is
EITHER, and a draft upload line shows BOTH candidates when the state is unknown.

LEAGUE RULE (constitution v25 section 2.6): real-life NHL events do not affect fantasy contracts.
"The fantasy team retains the player at the previous fantasy AAV." So for a player who already has a
fantasy contract, FANTRAX IS THE SALARY and a CapWages difference is not an error.

CLASSES (each rostered player lands in exactly one, first match wins):
  NEW SIGNING       Fantrax $1 and the source shows a contract.            -> upload line
  ROLLOVER          the Fantrax expiry decimal is EARLIER than this season (the fantasy
                    contract has run out) and the source has a current contract. -> upload line
  REAL DIFFERS (2.6) Fantrax whole number is neither AAV nor AAV/2 (within $1000 or 0.1%).
                    INFORMATION ONLY: never an upload line; posted only when a NEW one appears.
  EXPIRY CHECK      Fantrax decimal differs from the source's contract end, though not earlier
                    than this season. An open item labelled "check": not proven wrong, no line.
  FUTURE EXTENSION  a signed contract that starts in a later season. REMINDER ONLY, once.
  CONSISTENT        count only.
  (+ counts that are not findings: NO CONTRACT IN SOURCE, AMBIGUOUS, UNMATCHED BY NAME)

Upload lines come ONLY from NEW SIGNING and ROLLOVER, and never for a player the age check
(pp_eligibility_check, rule 5.2) flags: that claim may be reversed, so he is shown HOLD.

NAME MATCHING is exact fold, or the same_person prefix rule (Alex/Alexander yes,
Daniil/Dmitri no). Never surname plus initial. CapWages' own nickname in brackets,
"Spellacy, Anthony (AJ)", is read as a second exact spelling. A duplicated name is
split by the NHL team Fantrax shows; if that still leaves two, it is AMBIGUOUS and
reported, never guessed. Unmatched players are counted and the signed ones listed,
with a same-team-same-surname HINT that is printed and NOT used.

OPEN ITEMS: NEW SIGNING, ROLLOVER and EXPIRY CHECK stay open until Fantrax is fixed. EVERY post lists
all of them, with FULL or HALF decided per player from Fantrax's own draft, claim and trade
history (see "how a player reached the team"). They are never folded into the baseline.
REAL DIFFERS is information only, posted as a DELTA: only a NEW one, against the "known" set in
_tools/state/salary_baseline.json (seeded on the first run with a one-line summary). The digest hashes the open set plus the
delta, so the post repeats only when the set changes; pp_discord_post re-nudges a stale one.
--csv-out writes Steve's upload format (no header) for NEW SIGNING and ROLLOVER items whose basis
is known and that are not on HOLD.

CANARIES, ALL MUST PASS OR THE SCRIPT REFUSES TO REPORT (reader, detector, delta)
  reader    Celebrini 2027-28 AAV 18,800,000 (an extension) and Hutson 2026-27
            AAV 8,850,000, read from the same data the classifier uses.
  detector  an altered Fantrax value injected into a consistent player must produce
            EXACTLY ONE more finding, for each of three kinds of alteration.

EXIT CODES: #DIGEST is the first line of stdout. 3 = findings (post), 0 = none,
anything else non-zero = a crash or a refused canary, which the workflow turns into
a red run and never into a post.

  usage: pp_salary_watch.py [--post-out FILE] [--baseline FILE] [--csv-out FILE] [--no-record] [--coverage-audit N]
"""
import warnings, json, sys, time, re, hashlib, datetime, pathlib, random, collections
import urllib.request
warnings.filterwarnings("ignore")

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import pp_contracts as pc                      # fetch_slug, fold, same_person, money, slug, UA

FINDINGS = 3                                   # same convention as pp_eligibility_check
HERE = pathlib.Path(__file__).parent
POST_MAX = 1650                                # pp_discord_post truncates at 1890 incl. the fence
SLEEP = 1.0                                    # at most one CapWages request per second

NHL_SLUGS = """anaheim_ducks boston_bruins buffalo_sabres calgary_flames carolina_hurricanes
chicago_blackhawks colorado_avalanche columbus_blue_jackets dallas_stars detroit_red_wings
edmonton_oilers florida_panthers los_angeles_kings minnesota_wild montreal_canadiens
nashville_predators new_jersey_devils new_york_islanders new_york_rangers ottawa_senators
philadelphia_flyers pittsburgh_penguins san_jose_sharks seattle_kraken st_louis_blues
tampa_bay_lightning toronto_maple_leafs utah_mammoth vancouver_canucks vegas_golden_knights
washington_capitals winnipeg_jets""".split()

CLASS_ORDER = ["NEW SIGNING", "ROLLOVER", "REAL DIFFERS", "EXPIRY CHECK", "FUTURE EXTENSION", "CONSISTENT"]
ACTIONABLE = ("NEW SIGNING", "ROLLOVER", "REAL DIFFERS", "EXPIRY CHECK")
UPLOAD = ("NEW SIGNING", "ROLLOVER")                                # the ONLY classes that can yield an upload line


def _league():
    f = HERE / "config" / "league.json"
    if f.exists():
        d = json.loads(f.read_text())
        return d["league_id"], d.get("season_label", "2026-27")
    return "aizqwpvxmoc9uxas", "2026-27"


LEAGUE_ID, SEASON_LABEL = _league()
SEASON_START = int(SEASON_LABEL.split("-")[0])           # 2026
SEASON_KEY = SEASON_LABEL.replace("20", "", 1).replace("-", "")   # "2627"


def season_start(s):                                      # "2027-28" -> 2027
    return int(s.split("-")[0])


def season_end_yy(s):                                     # "2026-27" -> 27, the expiry decimal
    return (season_start(s) + 1) % 100


def half_up(x):                                           # 944,583.5 -> 944,584 (Fantrax rounds that way)
    return int(x + 0.5)


# ---------------------------------------------------------------- CapWages reader

def get_team_page(sl):
    u = "https://capwages.com/teams/" + sl
    last = None
    for attempt in range(3):
        try:
            s = urllib.request.urlopen(urllib.request.Request(u, headers=pc.UA), timeout=40).read().decode()
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', s, re.S)
            if not m:
                raise ValueError("no __NEXT_DATA__ block")
            pp = json.loads(m.group(1))["props"]["pageProps"]
            if not pp.get("teamMetadata") or "data" not in pp:
                raise ValueError("no team data on page")
            return pp
        except Exception as e:                            # noqa: BLE001
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("CapWages team page %s failed after 3 tries: %s" % (sl, last))


def name_forms(raw):
    """'Last, First (Nick)' -> ['First Last', 'Nick Last']. Both are exact spellings
    CapWages itself publishes; neither is a guess."""
    raw = (raw or "").strip()
    if "," in raw:
        last, _, first = raw.partition(",")
    else:
        first, _, last = raw.partition(" ")
    last = last.strip()
    out = []
    m = re.search(r"\(([^)]*)\)", first)
    if m and m.group(1).strip() and m.group(1).strip().upper() not in ("LT", "ST"):
        out.append("%s %s" % (m.group(1).strip(), last))
        first = re.sub(r"\([^)]*\)", "", first)
    out.insert(0, "%s %s" % (first.strip(), last))
    return out


def parse_contracts(raw):
    """-> list of {type, unconfirmed, seasons{season: aav}}. A contract with no
    season detail carries nothing usable and is dropped."""
    out = []
    for k in raw or []:
        seasons = {}
        for d in k.get("details") or []:
            a = pc.money(d.get("aav"))
            if d.get("season") and a is not None:
                seasons[d["season"]] = a
        if seasons:
            out.append({"type": k.get("type"), "unconfirmed": bool(k.get("unconfirmed")),
                        "seasons": seasons})
    return out


def read_capwages():
    """-> (players{slug: entry}, team_tricodes). One entry per slug: contracts from
    the page of the player's CURRENT team where the slug appears on several pages."""
    players, tricodes = {}, set()
    for i, sl in enumerate(NHL_SLUGS):
        if i:
            time.sleep(SLEEP)
        pp = get_team_page(sl)
        tri = pp["teamMetadata"]["tricode"]
        tricodes.add(tri)

        def add(o, section, has_contracts):
            if not isinstance(o, dict) or not o.get("slug") or not o.get("name"):
                return
            e = {"slug": o["slug"], "names": name_forms(o["name"]), "raw_name": o["name"],
                 "tri": o.get("currentTeamTricode") or tri, "page": tri, "section": section,
                 "has_contracts": has_contracts,
                 "contracts": parse_contracts(o.get("contracts")) if has_contracts else []}
            old = players.get(o["slug"])
            # keep the entry that lives on the player's own team page, and prefer
            # one that carries contracts over one that does not
            if old is None or (tri == e["tri"] and old["page"] != old["tri"]) or \
               (has_contracts and not old["has_contracts"]):
                players[o["slug"]] = e

        data = pp.get("data") or {}
        for sec in ("roster", "non-roster"):                 # 'dead cap' is other teams' players: skipped
            for grp, lst in (data.get(sec) or {}).items():
                if isinstance(lst, list):
                    for o in lst:
                        add(o, sec + "/" + grp, True)
        inact = data.get("inactive")
        if isinstance(inact, dict):
            for grp, lst in inact.items():
                if isinstance(lst, list):
                    for o in lst:
                        add(o, "inactive/" + grp, True)
        for o in pp.get("reserves") or []:                   # draft rights: no contract exists
            add(o, "reserves", False)
        for o in (pp.get("ahlClub") or {}).get("players") or []:
            add(o, "ahl", False)
    return players, tricodes


class Index:
    def __init__(self, players):
        self.players = players
        self.exact = collections.defaultdict(set)            # fold(full name) -> slugs
        self.bysur = collections.defaultdict(set)            # fold(surname) -> slugs
        for sl, e in players.items():
            for nm in e["names"]:
                self.exact[pc.fold(nm)].add(sl)
                self.bysur[pc.fold(nm.partition(" ")[2])].add(sl)

    def _narrow(self, slugs, tri):
        slugs = sorted(slugs)
        if len(slugs) > 1 and tri and tri != "(N/A)":
            same = [s for s in slugs if self.players[s]["tri"] == tri]
            if same:
                slugs = same
        return slugs

    def lookup(self, name, tri):
        """-> (entry|None, how). how: exact | prefix | AMBIGUOUS | none"""
        c = self._narrow(self.exact.get(pc.fold(name), ()), tri)
        if len(c) == 1:
            return self.players[c[0]], "exact"
        if len(c) > 1:
            return None, "AMBIGUOUS"
        fa, _, la = name.strip().partition(" ")
        c = [s for s in self.bysur.get(pc.fold(la), ())
             if any(pc.same_person(nm, name) for nm in self.players[s]["names"])]
        c = self._narrow(c, tri)
        if len(c) == 1:
            return self.players[c[0]], "prefix"
        if len(c) > 1:
            return None, "AMBIGUOUS"
        return None, "none"

    def hints(self, name, tri):
        """Same NHL team and same surname. PRINTED, NEVER USED."""
        la = pc.fold(name.strip().partition(" ")[2])
        return [self.players[s] for s in sorted(self.bysur.get(la, ()))
                if tri and self.players[s]["tri"] == tri]


# ---------------------------------------------------------------- Fantrax reader

def read_fantrax():
    from fantraxapi import FantraxAPI
    api = FantraxAPI(LEAGUE_ID)
    teams = api.teams
    if len(teams) != 32:
        sys.exit("Fantrax returned %d teams, expected 32. Refusing to report." % len(teams))
    rows, seen = [], set()
    for t in teams:
        raw = None
        for attempt in range(3):
            try:
                raw = api._request("getTeamRosterInfo", teamId=t.team_id)
                break
            except Exception:                              # noqa: BLE001
                time.sleep(3 * (attempt + 1))
        if raw is None:
            raise RuntimeError("Fantrax roster call failed 3 times for %s" % t.name)
        for tbl in raw["tables"]:
            hdr = [c.get("key") for c in tbl["header"]["cells"]]
            if "salary" not in hdr:
                continue
            si = hdr.index("salary")
            for r in tbl.get("rows", []):
                sc = r.get("scorer") or {}
                if not sc.get("name") or sc["scorerId"] in seen:
                    continue
                seen.add(sc["scorerId"])
                sal = str(r["cells"][si].get("content") or "0").replace(",", "")
                whole, _, dec = sal.partition(".")
                pos = (sc.get("posShortNames") or "F").split(",")[0].strip()
                rows.append({"team": t.name, "short": t.short or t.name, "name": sc["name"].strip(), "sid": sc["scorerId"],
                             "nhl": sc.get("teamShortName") or "", "pos": pos if pos in ("F", "D", "G") else "F",
                             "whole": int(float(whole or 0)), "dec": int(dec) if dec.isdigit() else 0,
                             "salary": r["cells"][si].get("content")})
        time.sleep(0.12)
    return rows, api


# ---------------------------------------------------------------- how a player reached the team
#
# FULL or HALF price cannot be read from the contract; it comes from Fantrax's own
# history. A player ACQUIRED by claim or trade into his current team is charged in
# full; one DRAFTED by his current team and not moved since is charged half. Anything
# else is UNKNOWN and gets both lines. History is bounded (Fantrax does not return it
# forever), so a missing record reads as UNKNOWN, never as "drafted".

def _when(txt):
    try:
        return datetime.datetime.strptime(txt, "%a %b %d, %Y, %I:%M%p")
    except (ValueError, TypeError):
        return datetime.datetime.min


def _history_rows(api, view):
    page, rows = 1, []
    while True:
        raw = api._request("getTransactionDetailsHistory", maxResultsPerPage="500", pageNumber=page, view=view)
        rows += (raw.get("table") or {}).get("rows", [])
        if page >= ((raw.get("paginatedResultSet") or {}).get("totalNumPages") or 1) or page > 40:
            return rows
        page += 1


def read_history(api):
    """-> {scorerId: [(datetime, kind, league team name, label)]} for kind in
    claim | trade | draft."""
    acq = collections.defaultdict(list)
    trows = _history_rows(api, "TRADE")
    dates = {}                                              # only the first row of a trade group carries the date
    for r in trows:
        for c in r.get("cells", []):
            if c.get("key") == "date" and c.get("content"):
                dates.setdefault(r.get("txSetId"), c["content"])
    for r in trows:
        sc = r.get("scorer") or {}
        if not sc.get("scorerId") or not r.get("executed", True):
            continue
        cells = {c.get("key"): c.get("content") for c in r.get("cells", [])}
        d = dates.get(r.get("txSetId")) or cells.get("date")
        acq[sc["scorerId"]].append((_when(d), "trade", (cells.get("to") or "").strip(), d or "?"))
    for r in _history_rows(api, "CLAIM_DROP"):
        sc = r.get("scorer") or {}
        if not sc.get("scorerId") or (r.get("transactionType") or "").lower() != "claim" or not r.get("executed", True):
            continue
        cells = {c.get("key"): c.get("content") for c in r.get("cells", [])}
        acq[sc["scorerId"]].append((_when(cells.get("date")), "claim", (cells.get("team") or "").strip(),
                                    cells.get("date") or "?"))
    names = {t.team_id: t.name for t in api.teams}
    for p in (api._request("getDraftResults").get("draftPicksOrdered") or []):
        if p.get("scorerId") and p.get("teamId") in names:
            when = datetime.datetime.fromtimestamp((p.get("modifiedDate") or 0) / 1000.0)
            acq[p["scorerId"]].append((when, "draft", names[p["teamId"]],
                                       "R%s P%s" % (p.get("round"), p.get("pickNumber"))))
    return acq


def _day(label):
    d = _when(label)
    return "%s %d" % (d.strftime("%b"), d.day) if d != datetime.datetime.min else label


def basis_of(acq, sid, team):
    """The LATEST way he reached THIS team decides. -> {state full|half|unknown, label, short}"""
    ev = [e for e in acq.get(sid, []) if e[2] == team]
    if not ev:
        return {"state": "unknown", "label": "basis: UNKNOWN", "short": "UNKNOWN"}
    when, kind, _, lab = max(ev, key=lambda e: (e[0], e[1] != "draft"))      # a claim or trade outranks a same-moment draft
    if kind == "draft":
        return {"state": "half", "label": "basis: drafted %s" % lab, "short": "drafted " + lab.replace(" ", "")}
    word = "claimed" if kind == "claim" else "traded"
    return {"state": "full", "label": "basis: %s %s" % (word, _day(lab)), "short": "%s %s" % (word, _day(lab))}


OPEN = ("NEW SIGNING", "ROLLOVER", "EXPIRY CHECK")           # open items: listed in EVERY post until closed


def attach_basis(res, acq, holds):
    """-> the open items, each with x['basis'] and, for an age-flagged one, x['hold']."""
    out = []
    for x in (x for x in res if x["cls"] in OPEN):
        r = x["row"]
        if x["cls"] == "EXPIRY CHECK":
            x["basis"] = {"state": "check", "label": "check: not proven wrong, no upload line", "short": "check",
                          "check": True}
        else:
            x["basis"] = basis_of(acq, r["sid"], r["team"])
            h = holds.get((r["name"], r["team"]))
            if h is not None:
                x["hold"] = h
        out.append(x)
    # ready first, then HOLD, then check
    out.sort(key=lambda x: (2 if x["basis"].get("check") else 1 if x.get("hold") else 0,
                            OPEN.index(x["cls"]), x["row"]["name"]))
    return out


def open_lines(x, idx):
    """Upload lines for one open item. NONE for HOLD (rule 5.2) and for a check item."""
    r = x["row"]
    b = x["basis"]
    if x["cls"] not in UPLOAD or x.get("hold"):
        return []

    def line(c):
        return "*%s*,%d,%s,%s,%s,%d.%02d" % (r["sid"], idx, r["name"], r["nhl"] or "FA", r["pos"], c, x["exp"])

    if b["state"] == "full":
        return [(b["label"], line(x["full"]))]
    if b["state"] == "half":
        return [(b["label"], line(x["half"]))]
    return [(b["label"] + ", FULL price", line(x["full"])), (b["label"] + ", HALF price", line(x["half"]))]


def basis_counts(opens):
    ready = collections.Counter(x["basis"]["short"].split(" ")[0] for x in opens
                                if x["cls"] in UPLOAD and not x.get("hold"))
    parts = ["ready: " + (", ".join("%s %d" % (k, ready[k]) for k in ("claimed", "traded", "drafted", "UNKNOWN")
                                    if ready[k]) or "none")]
    nh = sum(1 for x in opens if x.get("hold"))
    if nh:
        parts.append("HOLD rule 5.2: %d" % nh)
    nc = sum(1 for x in opens if x["cls"] == "EXPIRY CHECK")
    if nc:
        parts.append("check: %d" % nc)
    return "; ".join(parts)


def csv_text(opens):
    """Steve's exact format, no header. Only items whose basis is KNOWN."""
    out = []
    for i, x in enumerate(opens, 1):
        ls = open_lines(x, i)
        if ls and x["basis"]["state"] != "unknown":
            out.append(ls[0][1])
    return "\n".join(out) + ("\n" if out else "")


# ---------------------------------------------------------------- age check (rule 5.2)

def age_holds():
    """Run pp_eligibility_check's own logic and read its violations. A claimed player
    under 22 on Sep 15 breaks rule 5.2 and his claim may be reversed, so he must not
    reach an upload line. -> ({(name, fantasy team): {age, born, by}}, {(name, team)} claimed
    with no birthdate). If the check cannot run, REFUSE: failing open would let an illegal
    claim through to the file Steve uploads."""
    import io, re, contextlib
    import pp_eligibility_check as ec
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = ec.main()
    except SystemExit as e:
        sys.exit("AGE CHECK DID NOT RUN (%s). Refusing to report: an unchecked claim must not reach an upload line."
                 % (e.code,))
    if code not in (0, ec.VIOLATION):
        sys.exit("AGE CHECK returned %r. Refusing to report." % (code,))
    holds, unknown = {}, set()
    for ln in buf.getvalue().splitlines():
        m = re.match(r"^\s+(.+?), (.+?), age (\d+) on \S+ \(born (\S+)\), claimed by (.+)$", ln)
        if m:
            holds[(m.group(1).strip(), m.group(2).strip())] = {"age": int(m.group(3)), "born": m.group(4),
                                                                  "by": m.group(5).strip()}
            continue
        m = re.match(r"^\s+(.+?), (.+?), Fantrax age (\S+)\s+<-- and was CLAIMED", ln)
        if m:
            key, fage = (m.group(1).strip(), m.group(2).strip()), m.group(3)
            # No birthdate, but Fantrax's age TODAY can only be at or above his age on Sep 15,
            # so a Fantrax age under the minimum proves he was under it on Sep 15 as well.
            if fage.isdigit() and int(fage) < ec.MIN_AGE:
                holds[key] = {"age": int(fage), "born": "unresolved (inferred from Fantrax age %s)" % fage,
                              "by": key[1], "inferred": True}
            else:
                unknown.add(key)
    if code == ec.VIOLATION and not any(not h.get("inferred") for h in holds.values()):
        sys.exit("AGE CHECK reported violations but none could be parsed. Refusing to report.")
    return holds, unknown


# ---------------------------------------------------------------- classifier

def current_contract(entry):
    """The contract covering the season. Several distinct ones = AMBIGUOUS."""
    cov = [k for k in entry["contracts"] if SEASON_LABEL in k["seasons"]]
    if not cov:
        return None, False
    if len(cov) > 1 and len({(max(k["seasons"]), k["seasons"][SEASON_LABEL]) for k in cov}) > 1:
        return None, True
    return cov[0], False


def future_contract(entry):
    fut = [k for k in entry["contracts"] if min(season_start(s) for s in k["seasons"]) > SEASON_START]
    return min(fut, key=lambda k: min(season_start(s) for s in k["seasons"])) if fut else None


def tol(x):
    return max(1000.0, 0.001 * x)                          # within $1000 OR 0.1%, whichever is wider


def classify(rows, index):
    """-> (results, tallies). Pure: injecting a changed row and re-calling is the
    detector canary."""
    res, tally = [], collections.Counter()
    looked = [index.lookup(r["name"], r["nhl"]) for r in rows]
    # Two Fantrax players resolving to ONE CapWages player means at least one is
    # wrong (Matt Murray, a free agent, and Matthew Murray of NSH, both reached
    # matt-murray-1). Which one cannot be told from names, so none is classified.
    claims = collections.Counter(e["slug"] for e, _ in looked if e is not None)
    for r, (e, how) in zip(rows, looked):
        out = {"row": r, "cls": None, "note": ""}
        if e is not None and claims[e["slug"]] > 1:
            e, how = None, "AMBIGUOUS"
        if e is None:
            out["cls"] = "AMBIGUOUS" if how == "AMBIGUOUS" else "UNMATCHED"
            res.append(out)
            tally[out["cls"]] += 1
            continue
        out["entry"] = e
        cur, amb = current_contract(e)
        fut = future_contract(e)
        if amb:
            out["cls"] = "AMBIGUOUS"
            res.append(out)
            tally["AMBIGUOUS"] += 1
            continue
        if fut:
            out["future"] = fut
        if cur is None:
            if fut:
                out["cls"] = "FUTURE EXTENSION"
            else:
                out["cls"] = "NO CONTRACT IN SOURCE"
            res.append(out)
            tally[out["cls"]] += 1
            continue
        aav = cur["seasons"][SEASON_LABEL]
        end = max(cur["seasons"], key=season_start)
        out.update(aav=aav, end=end, exp=season_end_yy(end), cur=cur)
        full, half = half_up(aav), half_up(aav / 2.0)
        out["full"], out["half"] = full, half
        w = r["whole"]
        if w <= 1:
            out["cls"] = "NEW SIGNING"
        elif 0 < r["dec"] < out["exp"]:
            # the decimal is the expiry year and it is behind this season: the fantasy
            # contract has run out and the player now has a new one
            out["cls"] = "ROLLOVER"
        else:
            hit = [c for c in (full, half) if abs(w - c) <= tol(c)]
            if not hit:
                out["cls"] = "REAL DIFFERS"       # 2.6: Fantrax is the salary, this is information
            else:
                # nearest candidate; if the two candidates are within tolerance of
                # each other it cannot matter which
                out["matched"] = min(hit, key=lambda c: abs(w - c))
                out["state"] = "full" if out["matched"] == full else "half"
                if r["dec"] != out["exp"]:
                    out["cls"] = "EXPIRY CHECK"
                elif fut:
                    out["cls"] = "FUTURE EXTENSION"
                else:
                    out["cls"] = "CONSISTENT"
        if fut and out["cls"] != "FUTURE EXTENSION":
            out["note"] = (out["note"] + "; " if out["note"] else "") + "also has a future contract"
        res.append(out)
        tally[out["cls"]] += 1
    return res, tally


def findings_of(res):
    return [x for x in res if x["cls"] in ACTIONABLE]


# ---------------------------------------------------------------- canaries

def reader_canary(players):
    want = [("Macklin Celebrini", "2027-28", 18800000.0), ("Lane Hutson", "2026-27", 8850000.0)]
    idx = Index(players)
    for nm, season, aav in want:
        e, how = idx.lookup(nm, "")
        if e is None:
            sys.exit("READER CANARY FAILED: %s not found on any CapWages team page. Refusing to report." % nm)
        got = [k["seasons"].get(season) for k in e["contracts"] if season in k["seasons"]]
        if aav not in got:
            sys.exit("READER CANARY FAILED: %s %s AAV expected %s, read %s. Refusing to report."
                     % (nm, season, "{:,.0f}".format(aav), got))
    return "reader canary passed: Celebrini 2027-28 AAV 18,800,000, Hutson 2026-27 AAV 8,850,000"


def detector_canary(rows, index, res, tally):
    """Alter ONE Fantrax value on a player who is currently CONSISTENT and prove it
    yields exactly one more finding. Four alterations, four classes: an AAV that is
    neither candidate (REAL DIFFERS), a later expiry decimal (EXPIRY CHECK), an expiry
    decimal behind this season (ROLLOVER) and a $1 placeholder (NEW SIGNING)."""
    base = len(findings_of(res))
    pool = [x for x in res if x["cls"] == "CONSISTENT"]
    if not pool:
        sys.exit("DETECTOR CANARY FAILED: no CONSISTENT player to inject into. Refusing to report.")
    probe = pool[0]
    pr = probe["row"]
    msgs = []
    for label, change, want in (
            # twice the full AAV is farther than the tolerance from BOTH candidates
            ("AAV", {"whole": 2 * probe["full"] + 7777}, "REAL DIFFERS"),
            ("expiry", {"dec": (probe["exp"] + 1) % 100}, "EXPIRY CHECK"),
            ("rollover", {"dec": probe["exp"] - 1}, "ROLLOVER"),
            ("placeholder", {"whole": 1, "dec": 0}, "NEW SIGNING")):
        alt = [dict(x, **change) if x is pr else x for x in rows]
        r2, _ = classify(alt, index)
        f2 = findings_of(r2)
        hit = [x for x in f2 if x["row"]["sid"] == pr["sid"]]
        if len(f2) != base + 1 or len(hit) != 1 or hit[0]["cls"] != want:
            sys.exit("DETECTOR CANARY FAILED: altering %s of %s gave %d findings (expected %d) and class %s "
                     "(expected %s). Refusing to report."
                     % (label, pr["name"], len(f2), base + 1, hit[0]["cls"] if hit else None, want))
        msgs.append(label)
    return ("detector canary passed: altering %s for %s each produced exactly one more finding (%d -> %d)"
            % (", ".join(msgs), pr["name"], base, base + 1))


# ---------------------------------------------------------------- output

def money_s(x):
    return "{:,.0f}".format(x)


def describe(x):
    r = x["row"]
    sal = r["salary"]
    if x["cls"] == "NEW SIGNING":
        return "%s (%s, %s): Fantrax %s, source %s AAV %s ends %s" % (
            r["name"], r["team"], r["nhl"] or "FA", sal, SEASON_LABEL, money_s(x["aav"]), x["end"])
    if x["cls"] == "ROLLOVER":
        return "%s (%s, %s): Fantrax %s, expiry .%02d has passed, source %s AAV %s ends %s" % (
            r["name"], r["team"], r["nhl"] or "FA", sal, r["dec"], SEASON_LABEL, money_s(x["aav"]), x["end"])
    if x["cls"] == "REAL DIFFERS":
        return "%s (%s, %s): Fantrax %s, CapWages AAV %s (half %s) ends %s. Fantrax stays (constitution 2.6)" % (
            r["name"], r["team"], r["nhl"] or "FA", sal, money_s(x["aav"]), money_s(x["half"]), x["end"])
    if x["cls"] == "EXPIRY CHECK":
        return "%s (%s, %s): Fantrax %s, expiry %s, CapWages contract ends %s (.%02d). Not proven wrong, no upload line" % (
            r["name"], r["team"], r["nhl"] or "FA", sal,
            (".%02d" % r["dec"]) if r["dec"] else ".00 (none)", x["end"], x["exp"])
    k = x["future"]
    first = min(k["seasons"], key=season_start)
    last = max(k["seasons"], key=season_start)
    return "%s (%s, %s): Fantrax %s, signed %s to %s, %s AAV%s" % (
        r["name"], r["team"], r["nhl"] or "FA", sal, first, last,
        money_s(k["seasons"][first]), " UNCONFIRMED" if k["unconfirmed"] else "")


def render(res, tally, n, canaries, index, fetched, delta_block):
    out = []
    today = datetime.date.today()
    out.append("**PowerPlay salary watch** - %s, season %s (key %s)" % (today, SEASON_LABEL, SEASON_KEY))
    fs = findings_of(res)
    out.append("Rostered players n=%d. Source: %d CapWages team pages, %d player entries, "
               "%d with contract data." % (n, fetched[0], len(index.players),
                                           sum(1 for e in index.players.values() if e["has_contracts"])))
    for c in canaries:
        out.append(c)
    out.append("")
    out.extend(delta_block)
    out.append("FULL LIST, for reference:")
    out.append("COUNTS (each player in exactly one row, sums to n=%d):" % n)
    rows = CLASS_ORDER + ["NO CONTRACT IN SOURCE", "AMBIGUOUS", "UNMATCHED"]
    for c in rows:
        label = {"UNMATCHED": "UNMATCHED BY NAME", "AMBIGUOUS": "AMBIGUOUS NAME"}.get(c, c)
        out.append("   %-24s %4d  %5.1f%%" % (label, tally.get(c, 0), 100.0 * tally.get(c, 0) / n))
    assert sum(tally.values()) == n, "class counts do not sum to n"
    ac = sorted(x["row"]["whole"] / x["aav"] for x in res if x["cls"] == "REAL DIFFERS")
    if ac:
        q = lambda p: ac[min(len(ac) - 1, int(p * len(ac)))]
        bk = collections.Counter("<0.45" if v < 0.45 else "0.45-0.55" if v < 0.55 else "0.55-0.90" if v < 0.9
                                 else "0.90-1.10" if v <= 1.1 else ">1.10" for v in ac)
        out.append("REAL DIFFERS shape, Fantrax whole / source AAV: n=%d, min %.2f, p25 %.2f, median %.2f, p75 %.2f, "
                   "max %.2f; buckets %s" % (len(ac), ac[0], q(.25), q(.5), q(.75), ac[-1],
                                             ", ".join("%s: %d" % (k, bk[k]) for k in
                                                       ("<0.45", "0.45-0.55", "0.55-0.90", "0.90-1.10", ">1.10") if bk[k])))
    out.append("Open items (new signing + rollover + expiry check): %d.  Real differs (2.6, information only): %d.  "
               "Reminders (future extension): %d." %
               (sum(1 for x in fs if x["cls"] in OPEN), tally.get("REAL DIFFERS", 0), tally.get("FUTURE EXTENSION", 0)))
    unf = [x for x in res if x["cls"] == "UNMATCHED"]
    unf1 = sum(1 for x in unf if x["row"]["whole"] <= 1)
    out.append("Unmatched by name: %d, of which %d are $1 placeholders and %d carry a real salary."
               % (len(unf), unf1, len(unf) - unf1))
    out.append("")
    for cls in ("REAL DIFFERS",):                           # open items were listed in the block above
        grp = sorted([x for x in fs if x["cls"] == cls], key=lambda x: x["row"]["name"])
        if not grp:
            continue
        out.append(">>> %s (constitution 2.6, information only, NO upload line): %d" % (cls, len(grp)))
        for x in grp:
            out.append("  %s%s" % (describe(x), "  [%s]" % x["note"] if x["note"] else ""))
        out.append("")
    fu = sorted([x for x in res if x["cls"] == "FUTURE EXTENSION"], key=lambda x: x["row"]["name"])
    if fu:
        out.append(">>> FUTURE EXTENSION (reminder only, no upload line): %d" % len(fu))
        for x in fu:
            out.append("  " + describe(x))
        out.append("")
    nc = [x for x in res if x["cls"] == "NO CONTRACT IN SOURCE" and x["row"]["whole"] > 1]
    if nc:
        out.append(">>> Fantrax shows a salary but the source has no %s contract: %d" % (SEASON_LABEL, len(nc)))
        for x in nc:
            out.append("  %s (%s, %s): Fantrax %s" % (x["row"]["name"], x["row"]["team"], x["row"]["nhl"], x["row"]["salary"]))
        out.append("")
    am = [x for x in res if x["cls"] == "AMBIGUOUS"]
    if am:
        out.append(">>> Ambiguous names, NOT classified: %d" % len(am))
        for x in am:
            out.append("  %s (%s, %s): Fantrax %s" % (x["row"]["name"], x["row"]["team"], x["row"]["nhl"], x["row"]["salary"]))
        out.append("")
    if unf:
        signed = [x for x in unf if x["row"]["whole"] > 1]
        out.append(">>> UNMATCHED with a real Fantrax salary, cannot be checked: %d" % len(signed))
        for x in signed:
            r = x["row"]
            h = [e for e in index.hints(r["name"], r["nhl"])]
            hs = ""
            if h:
                hs = "   HINT, NOT USED: CapWages has %s" % ", ".join(e["raw_name"] for e in h[:3])
            out.append("  %s (%s, %s): Fantrax %s%s" % (r["name"], r["team"], r["nhl"] or "FA", r["salary"], hs))
        hint1 = []
        for x in unf:
            r = x["row"]
            if r["whole"] <= 1:
                for e in index.hints(r["name"], r["nhl"]):
                    if any(SEASON_LABEL in k["seasons"] for k in e["contracts"]):
                        hint1.append("%s -> CapWages %s has a %s contract" % (r["name"], e["raw_name"], SEASON_LABEL))
        if hint1:
            out.append("")
            out.append(">>> $1 players unmatched by name where a same-team same-surname CapWages player "
                       "HAS a contract (possible new signings, NOT USED): %d" % len(hint1))
            for h in hint1:
                out.append("  " + h)
        out.append("")
    out.append("Upload line = *Fantrax id*,index,name,NHL team,F/D/G,charge.expiry. The index is a running number "
               "for this report; the discount (FULL vs HALF) is Steve's call.")
    return out, fs


# ---------------------------------------------------------------- delta against a baseline
#
# The baseline holds what was ALREADY reported, so the post carries only what is new.
# Per Fantrax scorerId it keeps each finding's class, Fantrax value and source value.
# A finding that disappears (Fantrax got fixed) is listed once as RESOLVED and dropped.
# A FUTURE EXTENSION is a reminder: it is announced once, when first seen.

BASELINE = HERE / "state" / "salary_baseline.json"


def snapshot(res):
    f, fu, op = {}, {}, {}
    for x in res:
        r = x["row"]
        if x["cls"] in OPEN:
            op[r["sid"]] = {"cls": x["cls"], "name": r["name"], "team": r["team"], "nhl": r["nhl"],
                            "fantrax": r["salary"], "source": "%d|%s" % (x["aav"], x["end"])}
        elif x["cls"] in ACTIONABLE:
            f[r["sid"]] = {"cls": x["cls"], "name": r["name"], "team": r["team"], "nhl": r["nhl"],
                           "fantrax": r["salary"], "source": "%d|%s" % (x["aav"], x["end"]),
                           "aav": x["aav"], "half": x["half"], "exp": x["exp"]}
        elif x["cls"] == "FUTURE EXTENSION":
            k = x["future"]
            first = min(k["seasons"], key=season_start)
            fu[r["sid"]] = {"name": r["name"], "nhl": r["nhl"], "fantrax": r["salary"],
                            "source": "%s|%d" % (first, k["seasons"][first]),
                            "first": first, "last": max(k["seasons"], key=season_start),
                            "aav": k["seasons"][first], "unconfirmed": k["unconfirmed"]}
    return {"findings": f, "future": fu, "open": op}


def delta(prev, cur):
    """-> list of {kind, sid, now, was}. kind: NEW | RESOLVED | FUTURE.
    REAL DIFFERS (2.6) is information only: only a NEW divergence is reported. A
    divergence that moves or disappears is dropped silently; the baseline is the
    "known" set and is simply refreshed."""
    out = []
    pf, cf = prev["findings"], cur["findings"]
    for sid in sorted(cf):
        if sid not in pf:
            out.append({"kind": "NEW", "sid": sid, "now": cf[sid], "was": None})
    for sid in sorted(prev["open"]):                       # an open item that closed: reported once, then dropped
        if sid not in cur["open"]:
            out.append({"kind": "RESOLVED", "sid": sid, "now": None, "was": prev["open"][sid]})
    for sid in sorted(cur["future"]):
        if sid not in prev["future"]:
            out.append({"kind": "FUTURE", "sid": sid, "now": cur["future"][sid], "was": None})
    return out


def delta_digest(items, opens=()):
    """Hashes the open-item SET together with the delta, so a post repeats only when
    the set changes (pp_discord_post's 3-day rule re-nudges a stale one)."""
    lines = ["%s|%s|%s|%s|%s" % (i["kind"], i["sid"], (i["now"] or i["was"]).get("cls", ""),
                                 (i["now"] or {}).get("fantrax", ""), (i["now"] or {}).get("source", ""))
             for i in items]
    lines += ["OPEN|%s|%s|%s|%d|%s|%s|%s" % (x["row"]["sid"], x["cls"], x["row"]["salary"], x["aav"], x["end"],
                                             x.get("basis", {}).get("label", ""), "HOLD" if x.get("hold") else "")
              for x in opens]
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()[:16]


def delta_canary(rows, index, res):
    """Inject ONE new REAL DIFFERS and prove the delta contains exactly it. Also: an
    unchanged day is empty; a divergence that is fixed or moves is SILENT (2.6: information
    only); a $1 placeholder or a rollover decimal enters ONLY the open set, changes the
    digest, and closing it is exactly one RESOLVED. Pure functions on in-memory data."""
    base = snapshot(res)
    if delta(base, base):
        sys.exit("DELTA CANARY FAILED: an identical snapshot produced a non-empty delta. Refusing to report.")
    pool = [x for x in res if x["cls"] == "CONSISTENT"]
    if not pool:
        sys.exit("DELTA CANARY FAILED: no CONSISTENT player to inject into. Refusing to report.")
    pr = pool[0]["row"]
    probe = pool[0]
    kinds = lambda d: [(i["kind"], i["sid"]) for i in d]

    def alt_snapshot(change):
        # the salary STRING is what the snapshot compares, so rebuild it like Fantrax shows it
        ch = dict(change, salary="{:,}.{:02d}".format(change.get("whole", pr["whole"]), change.get("dec", pr["dec"])))
        r2, _ = classify([dict(x, **ch) if x is pr else x for x in rows], index)
        return snapshot(r2)

    inj = alt_snapshot({"whole": 2 * probe["full"] + 7777})
    if kinds(delta(base, inj)) != [("NEW", pr["sid"])] or set(inj["open"]) != set(base["open"]):
        sys.exit("DELTA CANARY FAILED: injecting one divergence for %s gave delta %s, expected exactly one NEW "
                 "and no change to the open set. Refusing to report." % (pr["name"], kinds(delta(base, inj))))
    if delta(inj, base) or delta(inj, alt_snapshot({"whole": 2 * probe["full"] + 9999})):
        sys.exit("DELTA CANARY FAILED: a fixed or moved divergence was not silent. Refusing to report.")
    if pr["sid"] in base["open"]:
        sys.exit("DELTA CANARY FAILED: %s was already open before injection. Refusing to report." % pr["name"])
    mk = lambda snap: [{"row": {"sid": k, "salary": v["fantrax"]}, "cls": v["cls"], "aav": 0, "end": v["source"]}
                       for k, v in snap["open"].items()]
    for label, change, cls in (("a $1 placeholder", {"whole": 1, "dec": 0}, "NEW SIGNING"),
                               ("a rollover decimal", {"dec": probe["exp"] - 1}, "ROLLOVER")):
        op = alt_snapshot(change)
        if set(op["open"]) - set(base["open"]) != {pr["sid"]} or set(base["open"]) - set(op["open"]) \
                or op["open"][pr["sid"]]["cls"] != cls or delta(base, op) or op["findings"] != base["findings"]:
            sys.exit("DELTA CANARY FAILED: injecting %s for %s did not add exactly that player to the open set as "
                     "%s and nothing else. Refusing to report." % (label, pr["name"], cls))
        if kinds(delta(op, base)) != [("RESOLVED", pr["sid"])]:
            sys.exit("DELTA CANARY FAILED: closing the open item (%s) gave %s, expected exactly one RESOLVED. "
                     "Refusing to report." % (label, kinds(delta(op, base))))
        if delta_digest([], mk(base)) == delta_digest([], mk(op)) or delta_digest([], mk(base)) != delta_digest([], mk(base)):
            sys.exit("DELTA CANARY FAILED: the digest does not track the open-item set. Refusing to report.")
    return ("delta canary passed: one injected divergence for %s gave exactly one NEW and no open item, fixing or "
            "moving it was silent, an unchanged day gave none; an injected $1 placeholder and an injected rollover "
            "decimal each entered only the open set, changed the digest, and closing each gave exactly one RESOLVED"
            % pr["name"])


def load_baseline(path):
    f = pathlib.Path(path)
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text())
        if "findings" in d and "future" in d:
            # Migration: NEW SIGNING and EXPIRY MISMATCH are OPEN items, never baseline. An
            # earlier baseline folded them into "findings"; move them across so each is still
            # reported RESOLVED once when it closes.
            d.setdefault("open", {})
            # Renames (2.6): AAV CHANGE -> REAL DIFFERS, EXPIRY MISMATCH -> EXPIRY CHECK. The
            # existing entries are the "known" set, so a rename must not read as a change.
            rename = {"AAV CHANGE": "REAL DIFFERS", "EXPIRY MISMATCH": "EXPIRY CHECK"}
            for sec in ("findings", "open"):
                for v in d[sec].values():
                    v["cls"] = rename.get(v["cls"], v["cls"])
            for sid in [k for k, v in d["findings"].items() if v["cls"] in OPEN]:
                d["open"].setdefault(sid, d["findings"][sid])
                del d["findings"][sid]
            return d
    except ValueError:
        pass
    sys.exit("baseline %s exists but is unreadable. Refusing to treat it as a seed: that would re-post "
             "everything. Fix or delete it by hand." % f)


def save_baseline(path, snap):
    d = dict(snap, seeded=str(datetime.date.today()))
    old = load_baseline(path)
    if old and old.get("seeded"):
        d["seeded"] = old["seeded"]
    f = pathlib.Path(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, indent=1, sort_keys=True) + "\n")


def seed_line(tally, n):
    return ("PowerPlay salary watch - %s: baseline seeded, n=%d. new signing %d | rollover %d | expiry check %d "
            "| real differs (2.6, info only) %d | future ext %d | consistent %d | unmatched by name %d. "
            "From now on only changes are posted."
            % (datetime.date.today(), n, tally.get("NEW SIGNING", 0), tally.get("ROLLOVER", 0),
               tally.get("EXPIRY CHECK", 0), tally.get("REAL DIFFERS", 0), tally.get("FUTURE EXTENSION", 0),
               tally.get("CONSISTENT", 0), tally.get("UNMATCHED", 0)))


def delta_line(i):
    now, was = i["now"], i["was"]
    nm = (now or was)["name"]
    nhl = (now or was).get("nhl") or "FA"
    if i["kind"] == "NEW":                                    # only REAL DIFFERS produces a NEW
        return "REAL DIFFERS (2.6) %s %s: Fantrax %s, CapWages AAV %s. Info only, Fantrax stays (constitution 2.6)" % (
            nm, nhl, now["fantrax"], money_s(now["aav"]))
    if i["kind"] == "RESOLVED":
        return "RESOLVED %s %s: was %s, Fantrax was %s" % (nm, nhl, was["cls"], was["fantrax"])
    return "FUTURE (reminder, once) %s %s: signed %s to %s, %s AAV%s" % (
        nm, nhl, now["first"], now["last"], money_s(now["aav"]), " UNCONFIRMED" if now["unconfirmed"] else "")


def open_short(x, prev_open):
    r = x["row"]
    b = x["basis"]
    mark = "+" if r["sid"] not in prev_open else " "
    who = "%s %s %s" % (r["name"], r["nhl"] or "FA", r["short"])
    if x["cls"] == "EXPIRY CHECK":
        return "%sCHECK %s: expiry %s, CapWages ends .%02d, not proven wrong, no line" % (
            mark, who, (".%02d" % r["dec"]) if r["dec"] else ".00", x["exp"])
    tag = "ROLLOVER " if x["cls"] == "ROLLOVER" else ""
    if x.get("hold"):
        return "%sHOLD: rule 5.2 %s%s: age %d, %s, no upload line" % (mark, tag, who, x["hold"]["age"], b["short"])
    if b["state"] == "unknown":
        return "%s%s%s: %s / %s .%02d UNKNOWN full/half" % (mark, tag, who, money_s(x["full"]), money_s(x["half"]), x["exp"])
    c = x["full"] if b["state"] == "full" else x["half"]
    return "%s%s%s: %d.%02d %s %s" % (mark, tag, who, c, x["exp"], b["state"].upper(), b["short"])


def compact(items, seeded, tally, n, digest, opens, prev_open):
    """What goes to Discord: ALL open items first, then the AAV delta (or the one-line
    seed summary on the first run)."""
    o = ["#DIGEST " + digest]
    if seeded:
        o.append(seed_line(tally, n))
    else:
        kinds = collections.Counter(i["kind"] for i in items)
        o.append("PowerPlay salary watch - %s, n=%d. Since last report: %s" % (
            datetime.date.today(), n,
            ", ".join("%s %d" % (lab, kinds[k]) for k, lab in (("NEW", "new real-life divergence (2.6)"),
                                                                ("RESOLVED", "resolved"), ("FUTURE", "future ext"))
                      if kinds[k]) or "nothing new"))
    if opens:
        cc = collections.Counter(x["cls"] for x in opens)
        o.append("OPEN ITEMS %d (%s). %s. Upload lines: %d. (+ = new since last report)" % (
            len(opens), ", ".join("%s %d" % (k.lower(), cc[k]) for k in OPEN if cc[k]), basis_counts(opens),
            len(csv_text(opens).splitlines())))
    else:
        o.append("OPEN ITEMS 0")
    used = len("\n".join(o))
    budget = POST_MAX - 120                                  # leave room for the closing line
    lines = [open_short(x, prev_open) for x in opens if not x.get("hold")]
    held = [x for x in opens if x.get("hold")]
    if held:                                                 # one line, so all of them stay visible
        lines.append("HOLD: rule 5.2, no upload line (%d): %s" % (
            len(held), ", ".join("%s %s" % (x["row"]["name"], x["row"]["short"]) for x in held)))
    order = {"RESOLVED": 0, "NEW": 1, "FUTURE": 2}
    lines += [delta_line(i) for i in sorted(items, key=lambda i: (order[i["kind"]], (i["now"] or i["was"])["name"]))
              if not seeded or i["kind"] == "RESOLVED"]
    shown = 0
    for t in lines:
        if used + len(t) + 1 > budget:
            break
        o.append(t)
        used += len(t) + 1
        shown += 1
    o.append("... and %d more in the run log and the salary-report artifact" % (len(lines) - shown)
             if shown < len(lines) else
             "Upload lines for known basis: salary-upload-DRAFT artifact. Full list: salary-report artifact.")
    return "\n".join(o) + "\n"


def render_delta(items, seeded, res, tally, n, opens, prev_open):
    """The open-item and delta blocks at the top of the full report, with upload lines."""
    out = []
    if seeded:
        out += [">>> BASELINE SEEDED (first run). REAL DIFFERS are in the full list below; the post carries one line.",
                seed_line(tally, n), ""]
    cc = collections.Counter(x["cls"] for x in opens)
    out.append(">>> OPEN ITEMS (listed in every post until closed): %d  (%s).  %s.  Upload lines: %d" % (
        len(opens), ", ".join("%s %d" % (k, cc[k]) for k in OPEN if cc[k]) or "none", basis_counts(opens) or "n/a",
        len(csv_text(opens).splitlines())))
    for i, x in enumerate(opens, 1):
        r = x["row"]
        out.append("  %s%s%s" % ("+ " if r["sid"] not in prev_open else "", describe(x),
                                 "  [%s]" % x["note"] if x["note"] else ""))
        if x.get("hold"):
            out.append("      HOLD: rule 5.2. Age %d on Sep 15 (born %s), claimed by %s. No upload line: the claim "
                       "may be reversed." % (x["hold"]["age"], x["hold"]["born"], x["hold"]["by"]))
        elif x.get("age_unknown"):
            out.append("      note: claimed, but the age check found no birthdate, so rule 5.2 is UNCHECKED for him")
        if x["basis"].get("check"):
            out.append("      " + x["basis"]["label"])
        for lab, ln in open_lines(x, i):
            out.append("      %s: %s" % (lab, ln))
    out.append("")
    if not seeded:
        out.append(">>> SINCE THE LAST REPORT: %d  (%s)" % (
            len(items), ", ".join("%s %d" % (k, sum(1 for i in items if i["kind"] == k))
                                  for k in ("NEW", "RESOLVED", "FUTURE")
                                  if any(i["kind"] == k for i in items)) or "nothing changed"))
        for i in items:
            out.append("  " + delta_line(i))
        out.append("")
    return out


# ---------------------------------------------------------------- coverage audit

def coverage_audit(players, n):
    """Re-prove 'a team page carries every player's contracts': compare the (season,
    AAV) set from season 2026-27 on against the player's OWN page, for a seeded
    random sample that includes minor leaguers and prospects, plus draft-rights
    players that carry no contract on the team page."""
    def sig(contracts):
        return {(s, a) for k in contracts for s, a in k["seasons"].items() if season_start(s) >= SEASON_START}
    rnd = random.Random(7)
    withc = [e for e in players.values() if e["has_contracts"]]
    minors = [e for e in withc if e["section"].startswith(("non-roster", "inactive"))]
    nocon = [e for e in players.values() if not e["has_contracts"]]
    sample = rnd.sample(withc, n) + rnd.sample(minors, min(n // 2, len(minors))) + rnd.sample(nocon, min(n // 2, len(nocon)))
    bad = checked = 0
    for e in sample:
        p, src = pc.fetch_slug(e["slug"])
        time.sleep(SLEEP)
        if p is None:
            print("  page fail %-26s %s" % (e["raw_name"], src))
            continue
        checked += 1
        a, b = sig(e["contracts"]), sig(parse_contracts(p.get("contracts")))
        ok = a == b
        bad += (not ok)
        print("  %s %-28s %-22s team page %d, player page %d%s" %
              ("OK  " if ok else "DIFF", e["raw_name"], e["section"], len(a), len(b),
               "" if ok else "   %s vs %s" % (sorted(a), sorted(b))))
    print("coverage audit: %d compared, %d differ" % (checked, bad))
    return bad


# ---------------------------------------------------------------- main

def main():
    args = sys.argv[1:]
    post_out = args[args.index("--post-out") + 1] if "--post-out" in args else None
    audit = int(args[args.index("--coverage-audit") + 1]) if "--coverage-audit" in args else 0
    baseline_path = args[args.index("--baseline") + 1] if "--baseline" in args else str(BASELINE)
    csv_out = args[args.index("--csv-out") + 1] if "--csv-out" in args else None
    record = "--no-record" not in args          # --no-record: do not write the baseline (local trials)

    players, tricodes = read_capwages()
    if len(tricodes) != 32:
        sys.exit("CapWages answered for %d of 32 teams. Refusing to report." % len(tricodes))
    with_c = sum(1 for e in players.values() if e["has_contracts"])
    if with_c < 1000:
        sys.exit("CapWages carried contract data for only %d players, expected 1000+. Refusing to report." % with_c)
    c1 = reader_canary(players)
    if audit:
        sys.exit(1 if coverage_audit(players, audit) else 0)

    rows, api = read_fantrax()
    names = {r["name"] for r in rows}
    miss = [c for c in ("Macklin Celebrini", "Lane Hutson") if c not in names]
    if miss:
        sys.exit("CANARY FAILED: Fantrax read %d players but not %s. Refusing to report." % (len(rows), miss))
    bad_tri = {r["nhl"] for r in rows if r["nhl"] and r["nhl"] != "(N/A)"} - tricodes
    if bad_tri:
        print("note: Fantrax NHL team codes CapWages does not have: %s" % sorted(bad_tri), file=sys.stderr)

    index = Index(players)
    res, tally = classify(rows, index)
    c2 = detector_canary(rows, index, res, tally)
    c3 = delta_canary(rows, index, res)
    cur = snapshot(res)
    prev = load_baseline(baseline_path)
    seeded = prev is None
    items = [] if seeded else delta(prev, cur)
    prev_open = {} if seeded else prev["open"]
    acq = read_history(api) if any(x["cls"] in OPEN for x in res) else {}
    holds, unk = age_holds() if any(x["cls"] in UPLOAD for x in res) else ({}, set())
    opens = attach_basis(res, acq, holds)
    for x in opens:
        if x["cls"] in UPLOAD and not x.get("hold") and (x["row"]["name"], x["row"]["team"]) in unk:
            x["age_unknown"] = True
    if seeded:
        digest = hashlib.sha256(("seed|" + seed_line(tally, len(rows))[len("PowerPlay salary watch - "):]
                                 ).encode()).hexdigest()[:16]
        digest = hashlib.sha256((digest + delta_digest([], opens)).encode()).hexdigest()[:16]
    else:
        digest = delta_digest(items, opens)
    print("#DIGEST " + digest)
    lines, fs = render(res, tally, len(rows), [c1, c2, c3], index, (len(tricodes),),
                       render_delta(items, seeded, res, tally, len(rows), opens, prev_open))
    print("\n".join(lines))
    if post_out:
        pathlib.Path(post_out).write_text(compact(items, seeded, tally, len(rows), digest, opens, prev_open))
    if csv_out:
        pathlib.Path(csv_out).write_text(csv_text(opens))
    if record:
        save_baseline(baseline_path, cur)
    return FINDINGS if (seeded or items or opens) else 0


if __name__ == "__main__":
    sys.exit(main())
