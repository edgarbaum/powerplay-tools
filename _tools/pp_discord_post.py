#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Post a message to a Discord channel via webhook.

⛔ THE WEBHOOK URL IS A SECRET. Anyone holding it can post to your server.
   It is read from a file that lives OUTSIDE this Drive folder and is never
   printed, logged, or committed. Do not paste it into chat.

Setup, once:
   1. Discord > Server Settings > Integrations > Webhooks > New Webhook
   2. Choose the channel. Name it "PP Roster Bot". Copy Webhook URL.
   3. mkdir -p ~/.pp-secrets && chmod 700 ~/.pp-secrets
   4. Paste the URL into  ~/.pp-secrets/discord_webhook  (one line, nothing else)
   5. chmod 600 ~/.pp-secrets/discord_webhook

Usage:
   python3 _tools/pp_discord_post.py "your message"
   python3 _tools/pp_roster_check.py | python3 _tools/pp_discord_post.py --stdin
"""
import os, sys, json, urllib.request, urllib.error, pathlib

# Two channels, deliberately separate.
#   public = the GM-facing per-season channel, e.g. #2627-transactions.
#            Transactions only.
#   ops    = the private commissioner channel, EB and Steve. Anything that names
#            a GM as being in breach, or concerns money, goes here.
#
# FAIL DIRECTION, deliberate: the ops channel falls back to the LEGACY
# ~/.pp-secrets/discord_webhook, which points at a private channel. The public
# channel has NO fallback and errors out instead. A misconfiguration must never
# be able to push a private message toward a public room; the worst it can do is
# send a public message nowhere, or send an ops message to the old private
# test channel. Both are recoverable. The other direction is not.
SECRETS = pathlib.Path.home() / ".pp-secrets"
LEGACY = SECRETS / "discord_webhook"
HOOK_FILE = {"public": SECRETS / "discord_webhook_public",
             "ops": SECRETS / "discord_webhook_ops"}
HOOK_ENV = {"public": "PP_DISCORD_WEBHOOK",
            "ops": "PP_DISCORD_WEBHOOK_OPS"}

def load_hook(channel="public"):
    if channel not in HOOK_ENV:
        sys.exit("unknown channel %r, expected public or ops" % channel)
    env = os.environ.get(HOOK_ENV[channel])
    if env: return env.strip()
    f = HOOK_FILE[channel]
    if f.exists():
        return f.read_text().strip()
    if channel == "ops" and LEGACY.exists():
        print("NOTE: ops not configured, using legacy %s" % LEGACY.name)
        return LEGACY.read_text().strip()
    sys.exit("No %s webhook configured.\n"
             "Expected file %s, or env %s.\n"
             "Refusing to guess: the public channel has no fallback by design."
             % (channel.upper(), f, HOOK_ENV[channel]))


def allowlist():
    """Only IDs present in gm_map.csv may ever be pinged. Fail closed."""
    import csv as _csv
    f = pathlib.Path(__file__).parent / "config" / "gm_map.csv"
    if not f.exists(): return []
    return [r["discord_user_id"].strip() for r in _csv.DictReader(f.open())
            if r.get("discord_user_id", "").strip().isdigit()]


def post(msg, dry=False, channel="public"):
    if len(msg) > 1900:
        msg = msg[:1890] + "\n... (truncated)"
    payload = {"content": msg,
               # structurally cannot ping @everyone, @here, roles, or any
               # user not explicitly listed in gm_map.csv
               "allowed_mentions": {"parse": [], "users": allowlist()}}
    body = json.dumps(payload).encode()
    if dry:
        print("DRY RUN to %s, would post %d chars:\n%s" % (channel, len(msg), msg)); return 0
    hook = load_hook(channel)
    # Discord sits behind Cloudflare, which rejects the default Python urllib
    # user-agent with error 1010. Send the documented DiscordBot UA instead.
    hdrs = {"Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/edgarbaum/powerplay-tools, 1.0)"}
    req = urllib.request.Request(hook, data=body, headers=hdrs)
    try:
        with urllib.request.urlopen(req) as r:
            print("posted to %s, HTTP %s" % (channel, r.status))
        return 0
    except urllib.error.HTTPError as e:
        print("FAILED HTTP %s: %s" % (e.code, e.read().decode()[:200]))
        return 1

if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    dry = "--dry" in args
    channel = "ops" if "--ops" in args else "public"
    args = [a for a in args if a not in ("--dry", "--ops")]
    if "--stdin" in args:
        text = sys.stdin.read()
    elif args:
        text = " ".join(args)
    else:
        sys.exit("nothing to post")
    sys.exit(post("```\n" + text.rstrip() + "\n```" if "--stdin" in sys.argv else text, dry, channel))
