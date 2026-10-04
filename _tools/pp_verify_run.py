#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verify a resolved claim-run workbook BEFORE it is sent to anyone.

WHY THIS EXISTS. On 2026-09-27 three defects reached EB and Steve. Every one of
them was in the DELIVERED FILE, not in the logic:

  a. the Awards tab was empty, so the Results tab computed the order from
     nothing and showed Winnipeg last. Steve caught it.
  b. the posted-order fingerprint was keyed by which order rather than by where
     it went, so a staged post marked the live channel as done.
  c. a name match on surname-plus-initial tied Daniil Isayev (26) to Dmitri
     Isayev (19) and produced a penalty against a legal claim. EB caught it.

I had verified the computation each time and not the artefact. This checks the
artefact against an INDEPENDENT recomputation from the same inputs, which is the
only check that would have caught all three.

  pp_verify_run.py <workbook.xlsx> <bids.csv> <YYYY-MM-DD>

Exit 0 only if every check passes. Anything else means do not send it.
"""
import sys, csv, json, collections, itertools, pathlib, datetime

def byp_all(bids, p):
    return [b for b in bids if b["p"] == p]


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    from openpyxl import load_workbook
    wbp, csvp, runday = sys.argv[1], sys.argv[2], sys.argv[3]
    d = datetime.date(*map(int, runday.split("-")))
    tag = d.strftime("%b %-d") if hasattr(d, "strftime") else runday

    wb = load_workbook(wbp)
    # the Results order is only checkable on COMPUTED values. Reading formulas made
    # check 7 below a silent no-op on every recalculated workbook (found 2026-10-04).
    wbv = load_workbook(wbp, data_only=True)
    fail, warn = [], []

    # --- inputs as the workbook holds them ---
    B = wb["Bids"]
    bids = []
    for r in range(2, 700):
        t = B.cell(row=r, column=1).value
        if not t: break
        bids.append(dict(t=t, pr=B.cell(row=r, column=2).value, g=B.cell(row=r, column=3).value,
                         mx=B.cell(row=r, column=4).value, p=B.cell(row=r, column=5).value,
                         bid=float(B.cell(row=r, column=6).value or 0),
                         age=B.cell(row=r, column=7).value))
    A = wb["Awards"]
    awards = []
    for r in range(2, 400):
        p = A.cell(row=r, column=2).value
        if not p: break
        awards.append(dict(rnd=A.cell(row=r, column=1).value, p=p,
                           t=A.cell(row=r, column=3).value,
                           price=float(A.cell(row=r, column=4).value or 0),
                           pen="PENALTY" in str(A.cell(row=r, column=5).value or "").upper()))
    T = wb["Teams"]
    order, budget = [], {}
    for r in range(2, 34):
        n = T.cell(row=r, column=2).value
        order.append(n); budget[n] = float(T.cell(row=r, column=3).value or 0)

    # --- 1. the source CSV and the Bids tab must agree ---
    src = [r for r in list(csv.reader(open(csvp)))[1:] if r[3] == "Claim" and tag in r[8]]
    if len(src) != len(bids):
        fail.append("Bids tab has %d rows, the export has %d claims for %s"
                    % (len(bids), len(src), tag))

    # --- 2. THE ONE THAT BIT: Awards must not be empty or short ---
    if not awards:
        fail.append("Awards tab is EMPTY. The Results tab derives the order from it, so the "
                    "order shown will be the ENTERING order. This is defect (a).")

    # --- 3. ages present for every bid target ---
    noage = sorted({b["p"] for b in bids if b["age"] in (None, "")})
    if noage:
        fail.append("no age for %d bid target(s): %s" % (len(noage), ", ".join(noage[:6])))
    under = sorted({b["p"] for b in bids if isinstance(b["age"], (int, float)) and b["age"] < 22})
    pens = {a["p"] for a in awards if a["pen"]}
    for p in under:
        if any(a["p"] == p and not a["pen"] for a in awards):
            fail.append("%s is under 22 and was AWARDED; it must be a penalty" % p)
        elif p not in pens and any(b["p"] == p for b in bids):
            warn.append("%s is under 22 and was bid on but has no penalty row" % p)

    # --- 4. duplicate priorities ---
    for t, cs in itertools.groupby(sorted(bids, key=lambda c: c["t"]), key=lambda c: c["t"]):
        prs = [c["pr"] for c in cs]
        if len(prs) != len(set(prs)):
            fail.append("%s has duplicate priorities %s; renumber and record it"
                        % (t, sorted(p for p in prs if prs.count(p) > 1)))

    # --- 5. every award must be the highest bid actually filed on that player ---
    byp = collections.defaultdict(list)
    for b in bids: byp[b["p"]].append(b)
    for a in [x for x in awards if x["pen"]]:
        # a penalty: the player must be under 22, the price 0, and the team must have bid on him
        if a["p"] not in under:
            fail.append("%s recorded as a PENALTY but is not under 22" % a["p"])
        if a["price"]:
            fail.append("%s penalty carries a price of $%.0f; must be 0" % (a["p"], a["price"]))
        if not any(x["t"] == a["t"] for x in byp_all(bids, a["p"])):
            fail.append("%s penalty charged to %s, who filed no bid on him" % (a["p"], a["t"]))
    for a in [x for x in awards if not x["pen"]]:
        field = byp.get(a["p"], [])
        if not field:
            fail.append("awarded %s but nobody bid on him" % a["p"]); continue
        top = max(x["bid"] for x in field)
        if a["price"] > top:
            fail.append("%s awarded at $%.0f but the highest bid was $%.0f" % (a["p"], a["price"], top))
        if not any(x["t"] == a["t"] and x["bid"] == a["price"] for x in field):
            fail.append("%s awarded to %s at $%.0f, which is not a bid they filed"
                        % (a["p"], a["t"], a["price"]))

    # --- 6. nobody spends more than they brought ---
    spent = collections.Counter()
    for a in awards: spent[a["t"]] += a["price"]
    for t, s in spent.items():
        if s > budget.get(t, 0) + 1e-6:
            fail.append("%s spent $%.0f of $%.0f" % (t, s, budget.get(t, 0)))

    # --- 7. THE REAL CHECK: recompute the order independently and compare ---
    base = {t: i + 1 for i, t in enumerate(order)}
    lw = {a["t"]: a["rnd"] for a in sorted(awards, key=lambda x: x["rnd"] or 0)}
    want = [t for t in order if t not in lw] + sorted(lw, key=lambda t: lw[t])
    R = wbv["Results"]
    got = [(R.cell(row=r, column=1).value, R.cell(row=r, column=2).value) for r in range(2, 34)]
    unrecalculated = any(isinstance(x, str) and x.startswith("=") for g in got for x in g)
    if unrecalculated or all(g[0] is None for g in got):
        warn.append("Results tab holds formulas, not values. Open and recalculate, or drive it "
                    "through LibreOffice, before trusting what it shows.")
    else:
        shown = [t for _, t in sorted(got, key=lambda x: (x[0] is None, x[0]))]
        if shown != want:
            for i, (a, b) in enumerate(zip(shown, want), 1):
                if a != b:
                    fail.append("Results position %d shows %s, recomputation says %s" % (i, a, b))
                    break

    print("VERIFY %s" % pathlib.Path(wbp).name)
    print("   bids %d | awards %d | teams that won %d | spend $%.0f"
          % (len(bids), len(awards), len(spent), sum(spent.values())))
    for w in warn: print("   WARN  %s" % w)
    for f in fail: print("   FAIL  %s" % f)
    print("   %s" % ("PASS, safe to send" if not fail else "DO NOT SEND"))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
