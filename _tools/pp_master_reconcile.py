#!/usr/bin/env python3
"""
Reconcile Fantrax salaries against the commissioners' master salary sheet. READ ONLY.

The master (Steve's Master-SalarySheet-*.ods, kept in Drive, never in this public repo) is the
league's own record: column F 'Final AAV' already carries the 50% flag, retention and the expiry
decimal. Fantrax should show exactly that number. Every rostered player whose Fantrax salary differs
from master F is a salary-update item; this is the check Steve otherwise does by eye.

Join key: the Fantrax player ID (master column A, '*xxxxx*' = Fantrax scorerId). Names are never
used to join.

usage: pp_master_reconcile.py <master.xlsx (values)> <fantrax_snapshot.json> <out.csv>
"""
import csv, json, sys, collections
import openpyxl


def main():
    mp, sp, outp = sys.argv[1:4]
    ws = openpyxl.load_workbook(mp, data_only=True).active
    master, dup = {}, collections.Counter()
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r[0]:
            continue
        sid = str(r[0]).strip("*")
        dup[sid] += 1
        master[sid] = dict(name=r[2], nhl=r[3], pos=r[4], final=r[5], pp_aav=r[6], half=r[7],
                           expiry=r[9], ext=r[11], ret=r[12], ret_team=r[13])
    snap = json.load(open(sp))
    rows, cls = [], collections.Counter()
    for team, v in snap.items():
        for t in v["raw"]["tables"]:
            for x in t.get("rows", []):
                sc = x.get("scorer") or {}
                if not sc.get("name"):
                    continue
                cells = [c.get("content") for c in x["cells"]]
                fx = float((cells[2] or "0").replace(",", ""))
                m = master.get(sc.get("scorerId"))
                if m is None:
                    k = "NOT IN MASTER"
                else:
                    mf = m["final"] if isinstance(m["final"], (int, float)) else None
                    if mf is not None and mf < 2:          # unsigned rows compute to about -19; Fantrax shows 1.00
                        mf = 1.0
                    k = "MATCH" if mf is not None and abs(mf - fx) < 0.005 else "DIFFERS"
                cls[k] += 1
                rows.append([team, sc["name"], sc.get("scorerId"), sc.get("teamShortName"), "%.2f" % fx,
                             "" if m is None else m["final"], k,
                             "" if m is None else (m["name"] or ""),
                             "" if m is None else (m["half"] or ""), "" if m is None else (m["ret"] or ""),
                             "" if m is None else (m["ext"] or "")])
    with open(outp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["league_team", "fantrax_name", "id", "nhl", "fantrax_salary", "master_final", "status",
                    "master_name", "half", "retention", "extension_note"])
        w.writerows(sorted(rows, key=lambda r: (r[6] != "DIFFERS", r[0], r[1])))
    print("rostered n=%d | %s" % (len(rows), ", ".join("%s %d" % kv for kv in cls.most_common())))
    print("master rows %d, ids appearing more than once: %d" % (sum(dup.values()), sum(1 for c in dup.values() if c > 1)))
    return 3 if cls["DIFFERS"] else 0


if __name__ == "__main__":
    sys.exit(main())
