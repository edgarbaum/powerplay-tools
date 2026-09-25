#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reconcile live Fantrax state against an expected claim-run result. READ ONLY.

Two independent signals must agree before the run is called complete:
  a. every expected player is on the expected team
  b. every team's claim budget equals pre-run minus what it was expected to pay
A roster that matches while budgets do not means the award was made without the
charge, or at the wrong price.

G9: the roster reader is canaried against known-present players before any
absence is believed. A broken reader reports a confident, wrong "nothing moved".
"""
import warnings, json, time, sys, collections
warnings.filterwarnings("ignore")
from fantraxapi import FantraxAPI

LEAGUE = "aizqwpvxmoc9uxas"
CANARIES = ["Macklin Celebrini", "Lane Hutson", "Brandt Clarke"]

exp = json.load(open("/tmp/resolution_log.json"))["log"]
pre = json.load(open("/tmp/claim_budgets.json"))

api = FantraxAPI(LEAGUE)
post, roster = {}, {}
for t in api.teams:
    raw = api._request("getTeamRosterInfo", teamId=t.team_id)
    d = {i["key"]: i for i in raw["miscData"]["salaryInfo"]["info"]}
    post[t.name] = float(d["claimBudget"]["value"])
    for tbl in raw["tables"]:
        for r in tbl.get("rows", []):
            sc = r.get("scorer") or {}
            if sc.get("name"):
                roster[sc["name"].strip()] = t.name
    time.sleep(0.12)
miss = [c for c in CANARIES if c not in roster]
if miss:
    sys.exit("CANARY FAILED: reader found %d players but not %s" % (len(roster), miss))

done  = [e for e in exp if roster.get(e["player"]) == e["winner"]]
todo  = [e for e in exp if e["player"] not in roster]
wrong = [(e, roster[e["player"]]) for e in exp
         if e["player"] in roster and roster[e["player"]] != e["winner"]]

spend = collections.Counter()
for e in done: spend[e["winner"]] += e["price"]
charged = {k: pre[k] - post[k] for k in pre if pre[k] != post[k]}
money_ok = dict(spend) == charged

print("EXPECTED %d awards, $%.0f" % (len(exp), sum(e["price"] for e in exp)))
print("   applied and correct : %d" % len(done))
print("   not yet applied     : %d" % len(todo))
print("   on the WRONG team   : %d" % len(wrong))
print("   charged $%.0f, roster and money agree: %s" % (sum(charged.values()), money_ok))
for e, h in wrong:
    print("   MISMATCH round %d: %s should be %s, is on %s" % (e["rnd"], e["player"], e["winner"], h))
if not money_ok:
    for k in sorted(set(spend) | set(charged)):
        if spend.get(k, 0) != charged.get(k, 0):
            print("   MONEY round-up %-24s awarded $%.0f but charged $%.0f"
                  % (k, spend.get(k, 0), charged.get(k, 0)))
for e in todo:
    print("   TODO round %-3d %-24s -> %-22s $%.0f" % (e["rnd"], e["player"], e["winner"], e["price"]))

clean = not todo and not wrong and money_ok
print("\n%s" % ("RUN COMPLETE. Roster and budgets both reconcile to the expected result."
               if clean else "NOT COMPLETE. See above."))
sys.exit(0 if clean else 1)
