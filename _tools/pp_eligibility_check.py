#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerPlay draft-protection sweep. READ ONLY.

Constitution 5.2: a player 21 years of age or younger on September 15 of the
season-start year is DRAFT-PROTECTED and "cannot be added via free agency or any
means other than the annual entry draft".

Fantrax DOES NOT ENFORCE THIS. On 2026-09-20 it processed a claim on an 18 year
old without complaint and neither commissioner caught it until afterwards. This
sweep catches it the next morning instead of after the fact.

WHY IT DOES NOT USE FANTRAX'S AGE COLUMN AS THE VERDICT:
Fantrax reports age TODAY. The rule is age on SEPTEMBER 15. In September those
differ by at most a few days; by March they differ by half a year. So Fantrax age
is used only to NARROW the candidate set, and every candidate's verdict comes from
a real birthdate.

WHAT IT FLAGS:
An under-age player who was ACQUIRED BY CLAIM this season. Being under age is not
itself a breach; a drafted prospect is legal and so is one acquired by trade, since
5.2 governs how a player ENTERS the league, not how they move within it. The breach
is the entry route.

Exit 1 if any violation is found, so a scheduler can post only on a real breach.
"""
import warnings, json, sys, time, datetime, pathlib, unicodedata, urllib.request, urllib.parse
warnings.filterwarnings("ignore")
from fantraxapi import FantraxAPI


def _league():
    f = pathlib.Path(__file__).parent / "config" / "league.json"
    if f.exists():
        d = json.loads(f.read_text())
        return d["league_id"], d.get("season_label", "")
    return "aizqwpvxmoc9uxas", "2026-27"


LEAGUE_ID, SEASON_LABEL = _league()
CUTOFF = datetime.date(int(SEASON_LABEL.split("-")[0]) if SEASON_LABEL else 2026, 9, 15)
MIN_AGE = 22                     # must be 22 or older on the cutoff
SCREEN_AT = MIN_AGE + 2          # resolve a birthdate for anyone Fantrax shows at or below this
CACHE = pathlib.Path(__file__).parent / "state" / "birthdates.json"
CANARIES = ["Macklin Celebrini", "Lane Hutson", "Brandt Clarke"]
UA = {"User-Agent": "powerplay-tools/1.0"}


def _get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20).read())


def _fold(x):
    """Strip accents and case. Fantrax writes Tim Stutzle, NHL writes Tim Stutzle with
    an umlaut, and an exact-string match silently loses every such player."""
    return "".join(c for c in unicodedata.normalize("NFKD", x or "")
                   if not unicodedata.combining(c)).strip().lower()


def birthdate(name, cache):
    """NHL public player record. Cached, because it is a fixed fact per player."""
    if name in cache:
        return cache[name]
    bd = None
    try:
        hits = []
        for active in ("true", "false"):
            hits += _get("https://search.d3.nhle.com/api/v1/search/player"
                         "?culture=en-us&active=%s&limit=12&q=%s"
                         % (active, urllib.parse.quote(name)))
        want = _fold(name)
        m = [h for h in hits if _fold(h.get("name")) == want]
        if not m:
            # surname plus first initial, which catches Ben vs Benjamin and A.J. vs AJ
            wp = want.split()
            if len(wp) >= 2:
                m = [h for h in hits
                     if _fold(h.get("name")).split()[-1:] == wp[-1:]
                     and _fold(h.get("name"))[:1] == wp[0][:1]]
                m = m if len(m) == 1 else []       # ambiguous is not a match
        if m:
            bd = _get("https://api-web.nhle.com/v1/player/%s/landing" % m[0]["playerId"]).get("birthDate")
        time.sleep(0.25)
    except Exception:
        bd = None
    cache[name] = bd
    return bd


def age_on(bd, when):
    b = datetime.date(*map(int, bd.split("-")))
    return when.year - b.year - ((when.month, when.day) < (b.month, b.day))


def main():
    api = FantraxAPI(LEAGUE_ID)

    roster = {}
    for t in api.teams:
        raw = api._request("getTeamRosterInfo", teamId=t.team_id)
        hdr = [c.get("key") for c in raw["tables"][0]["header"]["cells"]]
        ai = hdr.index("age")
        for tbl in raw["tables"]:
            for r in tbl.get("rows", []):
                sc = r.get("scorer") or {}
                cells = r.get("cells", [])
                if sc.get("name") and len(cells) > ai:
                    try:
                        roster[sc["name"].strip()] = (t.name, int(str(cells[ai]["content"])))
                    except (ValueError, TypeError):
                        roster[sc["name"].strip()] = (t.name, None)
        time.sleep(0.12)

    # G9 CANARY-BEFORE-NULL. A reader that cannot find a known-present player
    # returns a confident, wrong "no violations".
    missing = [c for c in CANARIES if c not in roster]
    if missing:
        sys.exit("CANARY FAILED: read %d players but not %s. Refusing to report." % (len(roster), missing))

    # Who was acquired by CLAIM, and by whom.
    # ⚠ BOUNDED LOOKBACK. Fantrax's transaction history does not go back forever, so
    # this set is "claimed within the window the API returns", not "claimed ever". A
    # player claimed before the window reads as not-claimed and therefore as legal.
    # The window is REPORTED rather than assumed away: run daily, this catches every
    # new violation; it is blind to ones already in the past.
    claimed_by, claim_dates = {}, []
    for page in range(1, 40):
        r = api._request("getTransactionDetailsHistory", view="CLAIM_DROP", pageNumber=page)
        tbl = r.get("table") or (r.get("tables") or [{}])[0]
        rows = tbl.get("rows", [])
        if not rows:
            break
        hdr = [c.get("key") for c in tbl["header"]["cells"]]
        ti = hdr.index("team") if "team" in hdr else 0
        di = hdr.index("date") if "date" in hdr else 1
        for row in rows:
            sc = row.get("scorer") or {}
            cells = row.get("cells", [])
            if len(cells) > di and cells[di].get("content"):
                claim_dates.append(str(cells[di]["content"]))
            if sc.get("name") and row.get("claimType") and len(cells) > ti:
                claimed_by[sc["name"].strip()] = str(cells[ti].get("content", "")).strip()

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    cands = {n: v for n, v in roster.items() if v[1] is not None and v[1] <= SCREEN_AT}

    def classify(claimset):
        v, ly, unk = [], [], []
        for n, (team, fage) in sorted(cands.items()):
            bd = birthdate(n, cache)
            if not bd:
                unk.append((n, team, fage))
                continue
            a = age_on(bd, CUTOFF)
            if a >= MIN_AGE:
                continue
            (v if n in claimset else ly).append((n, team, a, bd))
        return v, ly, unk

    viol, legal_young, unknown = classify(set(claimed_by))
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))

    # DETECTOR CANARY. A clean pass from a check that has never fired is not
    # evidence. Re-run the classification with one genuinely under-age player
    # injected as though he had been claimed. If that does not produce exactly
    # one violation, the detection path is broken and this run must not report
    # "no violations". Costs nothing: every birthdate is already cached.
    if legal_young:
        probe = legal_young[0][0]
        pv, _, _ = classify(set(claimed_by) | {probe})
        if len(pv) != len(viol) + 1 or probe not in [x[0] for x in pv]:
            sys.exit("DETECTOR CANARY FAILED: injecting under-age %s as claimed did not "
                     "produce a violation. Refusing to report." % probe)
        canary = "detector canary passed, fired on injected %s (age %d)" % (probe, legal_young[0][2])
    else:
        canary = "DETECTOR CANARY SKIPPED: no under-age player on any roster to probe with"

    ages = sorted(v[1] for v in roster.values() if v[1] is not None)
    out = []
    out.append("**PowerPlay draft-protection sweep** - %s" % datetime.date.today())
    out.append("Rule 5.2: must be %d or older on %s to be added via free agency." % (MIN_AGE, CUTOFF))
    out.append("Swept %d rostered players. Age distribution: min %d, p25 %d, median %d, p75 %d, max %d."
               % (len(ages), ages[0], ages[len(ages)//4], ages[len(ages)//2], ages[3*len(ages)//4], ages[-1]))
    out.append("%d screened at age %d or under, %d birthdates resolved, %d unresolved."
               % (len(cands), SCREEN_AT, len(cands) - len(unknown), len(unknown)))
    out.append(canary)
    if claim_dates:
        def _k(d):
            try:
                return datetime.datetime.strptime(d, "%a %b %d, %Y, %I:%M%p")
            except ValueError:
                return datetime.datetime.min
        lo, hi = min(claim_dates, key=_k), max(claim_dates, key=_k)
        out.append("Claim history examined covers %s to %s, %d acquisitions by claim. "
                   "A player claimed BEFORE that window reads as legal here."
                   % (lo.split(",")[0] + "," + lo.split(",")[1], hi.split(",")[0] + "," + hi.split(",")[1],
                      len(claimed_by)))
    else:
        out.append("NO claim history returned. Treat this run as UNKNOWN, not clean.")
    if viol:
        out.append("")
        out.append(">>> %d VIOLATION(S): under age AND acquired by claim." % len(viol))
        for n, team, a, bd in viol:
            out.append("   %s, %s, age %d on %s (born %s), claimed by %s"
                       % (n, team, a, CUTOFF, bd, claimed_by.get(n, "?")))
    else:
        out.append("")
        out.append(">>> No violations. No under-age player entered by claim.")
    if legal_young:
        out.append("")
        out.append("Under age but LEGAL, drafted or traded rather than claimed: %d" % len(legal_young))
        for n, team, a, bd in legal_young[:12]:
            out.append("   %s, %s, age %d" % (n, team, a))
        if len(legal_young) > 12:
            out.append("   ... and %d more" % (len(legal_young) - 12))
    if unknown:
        out.append("")
        out.append("NO BIRTHDATE FOUND, cannot rule either way, UNKNOWN not clear: %d" % len(unknown))
        for n, team, fage in unknown:
            out.append("   %s, %s, Fantrax age %s%s"
                       % (n, team, fage, "  <-- and was CLAIMED" if n in claimed_by else ""))
    print("\n".join(out))
    return 1 if viol else 0


if __name__ == "__main__":
    sys.exit(main())
