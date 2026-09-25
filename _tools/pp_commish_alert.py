#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commissioner roster alert. Tags only mapped commissioners, never the flagged GMs."""
import csv, pathlib, re, subprocess, sys, importlib.util

HERE = pathlib.Path(__file__).parent
COMMISH_TEAMS = ["San Jose Sharks", "Anaheim Ducks"]

def main(dry=False):
    out = subprocess.run([sys.executable, str(HERE/'pp_roster_check.py'), '--summary'],
                         capture_output=True, text=True).stdout.strip()
    gm = {r['fantrax_team']: r for r in csv.DictReader((HERE/'config'/'gm_map.csv').open())}
    tags = " ".join("<@%s>" % gm[t]['discord_user_id'] for t in COMMISH_TEAMS
                    if gm.get(t, {}).get('discord_user_id'))

    # count flagged teams FROM the output, never hardcoded
    m = re.search(r'__(\d+) teams? over a limit__', out)
    n = int(m.group(1)) if m else 0
    if n:
        note = ("\n\nCommissioner note: %d GM%s flagged above %s not been tagged or notified. "
                "This alert goes to the two of you only."
                % (n, "" if n == 1 else "s", "has" if n == 1 else "have"))
    else:
        note = "\n\nCommissioner note: no action needed."

    msg = tags + "\n" + out + note
    spec = importlib.util.spec_from_file_location('m', HERE/'pp_discord_post.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.post(msg, dry=dry)

if __name__ == "__main__":
    sys.exit(main(dry="--dry" in sys.argv))
