#!/usr/bin/env python3
"""
Per-season contract grid for one team's roster, from CapWages. READ ONLY.

Fantrax carries only the CURRENT season's salary (the decimals encode the
expiry year: 850,000.27 = ends 2026-27). A signed extension that starts next
season is invisible there. This pulls every contract a player has, including
ones that have not started, so future cap years are real rather than guessed.

Two witnesses for the current season: Fantrax (synced from PuckPedia) and
CapWages. Disagreement is reported, never silently resolved.

Name matching: a page is accepted only when its player name folds to the same
string as the Fantrax name (no surname+initial matching, trap T10).

usage: pp_contracts.py <fantrax_snapshot.json> "<Team Name>" <out.json>
"""
import json, re, sys, time, unicodedata, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}


def fold(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def same_person(a, b):
    # surnames equal AND one first name a PREFIX of the other (Alex/Alexander yes,
    # Daniil/Dmitri no). Never surname plus initial: trap T10.
    fa, _, la = a.strip().partition(" ")
    fb, _, lb = b.strip().partition(" ")
    fa, fb = fold(fa), fold(fb)
    return fold(la) == fold(lb) and bool(fa) and bool(fb) and (fa.startswith(fb) or fb.startswith(fa))


def slug(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = s.replace(".", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def money(x):
    if x in (None, "", "-"): return None
    return float(re.sub(r"[^0-9.]", "", x) or 0)


VARIANTS = {"alexander": "alex", "alex": "alexander", "tj": "t-j"}


def fetch(name):
    p, src = fetch_slug(slug(name))
    if p is None:
        first, _, rest = slug(name).partition("-")
        alt = VARIANTS.get(first.replace("-", ""))
        if alt:
            p2, src2 = fetch_slug(alt + "-" + rest)
            if p2 is not None:
                return p2, src2
    return p, src


def fetch_slug(sl):
    u = "https://capwages.com/players/" + sl
    try:
        s = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=25).read().decode()
    except Exception as e:
        return None, "fetch failed: %s" % e
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', s, re.S)
    if not m:
        return None, "no data block"
    p = json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("player")
    if not p:
        return None, "no player on page (slug %s)" % sl
    return p, u


def main():
    snap, team, out = sys.argv[1], sys.argv[2], sys.argv[3]
    raw = json.load(open(snap))[team]["raw"]
    rows = []
    for t in raw["tables"]:
        for r in t.get("rows", []):
            sc = r.get("scorer") or {}
            if not sc.get("name"): continue
            c = [x.get("content") for x in r["cells"]]
            sal = c[2] or "0"
            whole, _, dec = sal.replace(",", "").partition(".")
            rows.append({"name": sc["name"], "nhl": sc.get("teamShortName"),
                         "pos": sc.get("posShortNames"), "status": r.get("statusId"),
                         "fantrax_age": c[0], "fantrax_salary": float(whole or 0),
                         "fantrax_expiry": ("20" + dec) if dec not in ("", "00") else None})
    for i, row in enumerate(rows):
        p, src = fetch(row["name"])
        row["source"] = src
        if p is None:
            row["match"] = "NOT FOUND"
        else:
            pname = p.get("name") or ""
            if "," in pname:                      # CapWages stores "Last, First"
                last, _, first = pname.partition(",")
                pname = "%s %s" % (first.strip(), last.strip())
            row["born"] = p.get("born")
            row["drafted"] = p.get("acquisitionDetails")
            row["nhl_id"] = p.get("nhlId")
            row["draft_year"] = p.get("draft_year")
            row["draft_pick"] = ("%s:%s" % (p.get("draft_round"), p.get("drafted_overall"))
                                 if p.get("draft_round") else None)
            row["pro_status"] = p.get("status")
            row["capwages_name"] = pname
            row["match"] = "OK" if fold(pname) == fold(row["name"]) else \
                "OK-PREFIX" if same_person(pname, row["name"]) else "NAME MISMATCH"
            row["contracts"] = []
            for k in p.get("contracts") or []:
                row["contracts"].append({
                    "type": k.get("type"), "length": k.get("length"), "value": k.get("value"),
                    "signed": k.get("signingDate"), "expiry_status": k.get("expiryStatus"),
                    "unconfirmed": bool(k.get("unconfirmed")),
                    "seasons": [{"season": d.get("season"), "cap_hit": money(d.get("capHit")),
                                 "aav": money(d.get("aav"))} for d in (k.get("details") or [])]})
        print("%2d %-24s %-14s %s" % (i + 1, row["name"], row["match"],
              ", ".join("%s %s" % (k["type"], k["length"]) for k in row.get("contracts", []))[:90]))
        time.sleep(1.0)
    json.dump(rows, open(out, "w"), indent=1)


if __name__ == "__main__":
    main()
