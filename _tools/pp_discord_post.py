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

# Four channels, deliberately separate. A Discord webhook URL encodes exactly ONE
# destination channel, so each needs its own webhook and its own secret.
#
#   public = #26-27-transactions      the raw Fantrax firehose, all 32 GMs
#   ops    = #commish-ops             PRIVATE, EB + Steve. Breaches, cap, dues.
#   fa     = #26-27-fa-claim-order    free agent bid results and the resulting order
#   waiver = #26-27-waiver-claim-order  waiver claim results and the resulting order
#   test   = #pp-bot-test             staging. Anything new goes here first.
#
# fa and waiver are separate from public because only the RESOLVER knows which
# process produced a result. Fantrax records every award as claimType=FA whether it
# came from pre-season bidding or an in-season waiver, so the feed cannot tell them
# apart. The distinction has to come from the thing that ran the process.
#
# FAIL DIRECTION, deliberate: ops falls back to the LEGACY private webhook file.
# Every other channel has NO fallback and errors out instead. A misconfiguration
# must never be able to push a message toward a channel it was not meant for; the
# worst it can do is send nothing, which is loud and recoverable.
SECRETS = pathlib.Path.home() / ".pp-secrets"
LEGACY = SECRETS / "discord_webhook"
HOOK_FILE = {"public": SECRETS / "discord_webhook_public",
             "ops": SECRETS / "discord_webhook_ops",
             "fa": SECRETS / "discord_webhook_fa",
             "waiver": SECRETS / "discord_webhook_waiver",
             "test": SECRETS / "discord_webhook"}
HOOK_ENV = {"public": "PP_DISCORD_WEBHOOK",
            "ops": "PP_DISCORD_WEBHOOK_OPS",
            "fa": "PP_DISCORD_WEBHOOK_FA",
            "waiver": "PP_DISCORD_WEBHOOK_WAIVER",
            "test": "PP_DISCORD_WEBHOOK_TEST"}

def load_hook(channel="public"):
    if channel not in HOOK_ENV:
        sys.exit("unknown channel %r, expected one of %s"
                 % (channel, ", ".join(sorted(HOOK_ENV))))
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
             "Refusing to guess: only the ops channel has a fallback, by design."
             % (channel.upper(), f, HOOK_ENV[channel]))



def post(msg, dry=False, channel="public"):
    if len(msg) > 1900:
        msg = msg[:1890] + "\n... (truncated)"
    payload = {"content": msg,
               # EB 2026-09-25: no GM handle map. Messages name the TEAM; the
               # commissioners tag a person by hand on the rare occasion it is
               # warranted. So this bot structurally cannot mention ANYONE:
               # no @everyone, no @here, no roles, no users. An empty parse list
               # with no users key is the strongest form of that guarantee, and it
               # means no Discord identifiers exist anywhere in this repository.
               "allowed_mentions": {"parse": []}}
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
    channel = "public"
    for c in ("ops", "fa", "waiver", "test"):
        if "--" + c in args: channel = c
    args = [a for a in args if a not in ("--dry", "--ops", "--fa", "--waiver", "--test")]
    if "--stdin" in args:
        text = sys.stdin.read()
    elif args:
        text = " ".join(args)
    else:
        sys.exit("nothing to post")
    sys.exit(post("```\n" + text.rstrip() + "\n```" if "--stdin" in sys.argv else text, dry, channel))
