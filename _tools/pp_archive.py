#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Append-only archive of Fantrax transaction rows. READ ONLY against Fantrax.

WHY THIS EXISTS. Twice in eight days the record of a claim run has been lost:

  2026-09-20  the CSV holding all 71 FILED bids was overwritten by a later
              export. Recovered only because the numbers happened to survive
              inside a workbook.
  2026-09-27  Steve reversed the run. Every claim vanished from Fantrax and
              from every export. The 28 executed rows survive only because I
              happened to pull them two hours earlier.

Fantrax's history is not an archive. It is a VIEW of current state: it hides
losing bids once a run executes, and drops everything if a run is reversed. So
the record has to live somewhere that only ever grows.

This appends every row it has not seen to a JSONL ledger, committed to git. Rows
are never updated and never deleted, so a reversal in Fantrax cannot erase what
was already true. Run often; the announcer already polls every 15 minutes.

It stores the RAW row, not a summary, because we do not yet know which field will
turn out to matter. `claimType` and `feesUsed` both mattered later, and neither
was in anyone's summary at the time.
"""
import warnings, json, sys, time, hashlib, pathlib, datetime, collections
warnings.filterwarnings("ignore")
from fantraxapi import FantraxAPI

HERE = pathlib.Path(__file__).parent
LEDGER = HERE / "state" / "transaction_ledger.jsonl"
VIEWS = ("CLAIM_DROP", "TRADE")
CANARY_MIN = 20          # the league has far more history than this


def _league():
    f = HERE / "config" / "league.json"
    return json.loads(f.read_text())["league_id"] if f.exists() else "aizqwpvxmoc9uxas"


def rid(view, row, cells):
    """Stable id from the row's own content, so re-reads dedupe."""
    sc = row.get("scorer") or {}
    raw = "|".join([view, str(row.get("txSetId")), str(sc.get("scorerId") or sc.get("name")),
                    str(row.get("transactionType")), "|".join(str(c) for c in cells)])
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def main():
    api = FantraxAPI(_league())
    seen = set()
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            if line.strip():
                seen.add(json.loads(line)["id"])

    fresh, total = [], 0
    for view in VIEWS:
        for page in range(1, 40):
            r = api._request("getTransactionDetailsHistory", view=view, pageNumber=page)
            tbl = r.get("table") or (r.get("tables") or [{}])[0]
            rows = tbl.get("rows", [])
            if not rows:
                break
            hdr = [c.get("key") or c.get("name") for c in tbl["header"]["cells"]]
            for row in rows:
                cells = [c.get("content") for c in row.get("cells", [])]
                total += 1
                i = rid(view, row, cells)
                if i in seen:
                    continue
                sc = row.get("scorer") or {}
                fresh.append({
                    "id": i, "view": view, "seen_at": datetime.datetime.utcnow().isoformat() + "Z",
                    "player": sc.get("name"), "scorer_id": sc.get("scorerId"),
                    "nhl": sc.get("teamShortName"), "pos": sc.get("posShortNames"),
                    "type": row.get("transactionType"), "claim_type": row.get("claimType"),
                    "result": row.get("resultCode"), "executed": row.get("executed"),
                    "deleted": row.get("deleted"), "fees_used": row.get("feesUsed"),
                    "tx_set": row.get("txSetId"), "header": hdr, "cells": cells})
                seen.add(i)
            time.sleep(0.1)

    # G9: a reader that sees almost nothing must not be allowed to write an
    # "everything is fine" ledger entry. Refuse rather than record a false quiet.
    if total < CANARY_MIN:
        sys.exit("CANARY FAILED: only %d rows read across %s. Refusing to write."
                 % (total, ", ".join(VIEWS)))

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        for rec in fresh:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    kept = len(seen)
    print("archive: read %d rows, %d new, %d total in the ledger" % (total, len(fresh), kept))
    if fresh:
        by = collections.Counter((r["view"], r["type"]) for r in fresh)
        for k, n in sorted(by.items()):
            print("   new: %-12s %-8s %d" % (k[0], k[1], n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
