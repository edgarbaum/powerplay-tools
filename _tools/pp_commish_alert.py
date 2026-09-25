#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Commissioner roster alert to the PRIVATE ops channel.

Names teams, never people. EB 2026-09-25: no GM handle map, the
commissioners tag by hand when it is actually warranted.
"""
import pathlib, re, subprocess, sys, importlib.util

HERE = pathlib.Path(__file__).parent


def main(dry=False):
    out = subprocess.run([sys.executable, str(HERE/'pp_roster_check.py'), '--summary'],
                         capture_output=True, text=True).stdout.strip()
    # count flagged teams FROM the output, never hardcoded
    m = re.search(r'__(\d+) teams? over a limit__', out)
    n = int(m.group(1)) if m else 0
    if n:
        note = ("\n\nCommissioner note: %d GM%s flagged above %s not been tagged or notified. "
                "This channel is private to the two of you."
                % (n, "" if n == 1 else "s", "has" if n == 1 else "have"))
    else:
        note = "\n\nCommissioner note: no action needed."

    msg = out + note
    spec = importlib.util.spec_from_file_location('m', HERE/'pp_discord_post.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.post(msg, dry=dry, channel="ops")

if __name__ == "__main__":
    sys.exit(main(dry="--dry" in sys.argv))
