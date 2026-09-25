# Standing this up. Roughly fifteen minutes, once.

No machine of yours stays on. GitHub runs it.

Do these in order. Steps 1 and 2 are Discord, step 3 onward is GitHub.

---

## STEP 1. Create the two Discord channels

The split matters. Anything that names a GM as being in breach, or concerns money,
must not land in front of 32 people before you have looked at it.

ONE new channel, in the category you already have:

    26-27 Season  (existing category)
      # 26-27-transactions     NEW. PUBLIC, all GMs. Announcer output only.

    # commish-ops              NEW. PRIVATE, EB + Steve. Breaches, cap, dues.
                               Not seasonal. Same two people every year.

`#pp-bot-test` stays as it is. Keep it for staging anything new.

Note the naming: `26-27-transactions`, hyphenated, matching the five channels
already in that category. Seasoning the channel is not just tidiness. Fantrax
issues a NEW league id every season, which is why only the current season answers
without a login, so a per-season channel maps one to one onto a per-season data
source. At season end you lock the channel rather than date-filtering a scroll.

Suggested permissions on `#transactions`: everyone can read, nobody can post except
the webhook. It is a feed, not a room. Discussion belongs in your existing chat.

## STEP 2. Create one webhook per channel

For each channel: Edit Channel, Integrations, Webhooks, New Webhook, Copy Webhook URL.

Save them locally, outside Google Drive so they never sync. Copy the webhook URL
to the clipboard, then run the matching line:

    mkdir -p ~/.pp-secrets && chmod 700 ~/.pp-secrets

    # with the #26-27-transactions webhook on the clipboard
    pbpaste > ~/.pp-secrets/discord_webhook_public && chmod 600 ~/.pp-secrets/discord_webhook_public

    # with the #commish-ops webhook on the clipboard
    pbpaste > ~/.pp-secrets/discord_webhook_ops && chmod 600 ~/.pp-secrets/discord_webhook_ops

LEAVE your existing `~/.pp-secrets/discord_webhook` alone. It points at
`#pp-bot-test`, and the code now treats it as a legacy fallback for the OPS
channel only.

That is deliberate and it is the answer to which file means which. The fallbacks
run toward privacy, never away from it:

    ops    missing  ->  falls back to the legacy private test channel, with a note
    public missing  ->  REFUSES and exits, no fallback at all

So the worst a misconfiguration can do is send a public message nowhere, or send
an ops message to the old private channel. Both are recoverable in a minute. The
other direction, a breach notice landing in front of 32 GMs, is not.

Do not paste either URL into this chat, a commit, or a terminal that logs history.

## STEP 3. Create the GitHub repository

Name it `powerplay-tools`. See the note at the bottom on public versus private.

## STEP 4. Add the two secrets

Repository, Settings, Secrets and variables, Actions, New repository secret. Twice.

    PP_DISCORD_WEBHOOK       the #26-27-transactions webhook
    PP_DISCORD_WEBHOOK_OPS   the #commish-ops webhook

Paste straight from the file into the browser.

Add BOTH. On GitHub there is no legacy file to fall back to, so a missing ops
secret means the ops workflows exit with an error rather than post anywhere. That
is the intended behaviour: a failed run you can see beats a message in the wrong
room.

## STEP 5. Push

From `PowerPlay/_repo`:

    git init
    git add .
    git commit -m "PowerPlay commissioner tools"
    git branch -M main
    git remote add origin https://github.com/<your-username>/powerplay-tools.git
    git push -u origin main

## STEP 6. Baseline the announcer BEFORE it can shout

The announcer has never been baselined. Its first unguarded run would post every
historical transaction into `#transactions` at once.

Actions tab, `announcer`, Run workflow, tick **baseline**, run it. The log must say
"baseline recorded, nothing announced". Only then let the schedule take over.

## STEP 7. Watch one real cycle

Make any small roster move in Fantrax. Within 15 minutes, during the evening window,
it should appear in `#transactions`. If it does not, the run log will say why.

---

## What then runs by itself

| Workflow       | When                                      | Where it posts        |
|----------------|-------------------------------------------|-----------------------|
| `announcer`    | every 15 min 16:00-02:00 ET, hourly otherwise | `#26-27-transactions` PUBLIC |
| `roster-check` | daily 13:00 UTC                           | `#commish-ops` PRIVATE |
| `eligibility`  | daily 13:15 UTC                           | `#commish-ops` PRIVATE |
| `trade-clock`  | daily 13:30 UTC                           | `#commish-ops` PRIVATE |

`roster-check` stays silent unless a team is over a limit AND the compliance deadline
has passed. `eligibility` stays silent unless a draft-protected player was actually
added by claim. No daily all-clear noise from either.

## Why that announcer schedule and not every 15 minutes flat

Measured against 95 timestamped transactions from 2026-08-03 onward:

    Hour of day:  80% land between 18:00 and 23:59 league time.
                  32% land in the 21:00 hour alone.
                  2%  land between midnight and 08:00.
    Gaps between consecutive events: median 5 min, p75 848 min, p90 2351 min.

The gap distribution is the interesting part. It is bimodal, not steady. Transactions
arrive in bursts a few minutes apart, separated by half-day silences. A tighter poll
does not catch a burst sooner, because the whole burst is announced together in one
run either way. All a tighter poll buys is a shorter worst-case delay, and that only
has value when someone is awake to read it.

So: tight through the evening and its shoulders, hourly across the dead part of the
day. 54 runs a day instead of 96.

## Public or private

PUBLIC: Actions minutes are unmetered. Any cadence, no arithmetic to do.

PRIVATE: the free allowance is 2,000 minutes a month. At 54 announcer runs a day plus
THREE daily jobs, that is about 1,710 runs a month. GitHub rounds each job up to a
whole minute, and the eligibility sweep takes about two, so roughly 1,740 minutes. It
still fits, with about 13% headroom. Note that headroom has ALREADY shrunk once, from
16% to 13%, because the eligibility sweep was added after this file was first written.
That is the argument against private: the number moves every time the league needs
something new.

Nothing in this repository is secret either way. I scanned every file: zero
secret-shaped strings. The league id is already public, the Fantrax endpoints need no
login, and both webhooks live in GitHub Secrets.

My recommendation is PUBLIC, because the headroom on private is thin and the only
thing privacy would protect is code that contains nothing sensitive. If you would
rather it be private anyway, it works today and I will tell you before anything we add
later pushes it over.

## What `eligibility` does, since it is new

Constitution 5.2 blocks a player who is 21 or younger on September 15 from being added
by free agency. FANTRAX DOES NOT ENFORCE THIS. On 2026-09-20 it processed a claim on an
18 year old without complaint. This sweep catches that the next morning.

It does not trust Fantrax's age column, because Fantrax reports age TODAY and the rule
is age on September 15; by March those differ by half a year. Age only narrows the
candidate set, and every verdict comes from a real birthdate.

a. It flags only under-age players who arrived BY CLAIM. A drafted prospect is legal
   and so is one acquired by trade. The breach is the entry route, not the age.
b. Every run injects a known under-age player as though he had been claimed, and
   REFUSES to report if that does not produce a violation. A clean pass from a check
   that has never fired is not evidence.
c. It prints the claim-history window it examined. Fantrax's history is bounded, so
   "no violations" means "none in that window", not an unqualified all-clear.

The repository ships with 830 birthdates already cached, so the first run is fast
instead of making 830 network calls.

## How it keeps itself alive

Each run commits its state file back. That is what stops the announcer repeating
itself, and it should also keep the schedules clear of GitHub's 60-day inactivity
auto-disable. If GitHub disables them anyway it emails you and re-enabling is one
click. The workflows are schedule-only, so a state commit cannot trigger another run.

## Changing anything later

`PowerPlay/_tools/` stays the working copy. Edit there, copy into `_repo`, push.
Never edit the two copies independently.


---

## Rolling over to next season

One edit and one swap, roughly five minutes:

a. Create a `27-28 Season` category, create `#27-28-transactions` in it, add a
   webhook, replace the `PP_DISCORD_WEBHOOK` secret with it. Lock the whole
   `26-27 Season` category read-only rather than deleting anything.
b. Edit `_tools/config/league.json` with the new season and the new Fantrax league
   id, then push. Every tool reads that one file, which is why this is one edit
   and not five.
c. Get the new league id from the league URL, or from
   `api._request("getFantasyLeagueInfo")["historyLeagues"]`.
d. Delete `_tools/state/announcer_v2.json` so the new season starts clean, then
   run `announcer` once with **baseline** ticked, exactly as in step 6.

## If you later want a live bot, what it costs

GitHub Actions cannot answer a slash command. It wakes up, runs, and exits. A bot
that responds to `/roster`, `/cap`, `/goldpoints`, or reacts to a message, has to
hold an open connection to Discord, which means a process that never stops. That
is the only thing you would be paying for.

Checked 2026-09-20, not recalled:

    Fly.io      shared-cpu-1x, 256MB      $1.94/month   = $23.28/year
                reserve a block instead   $36/year, covering $5/month of usage
    Railway     Hobby                     $5/month      = $60/year

256MB is comfortably enough for a Discord bot serving 32 people. Egress is
negligible. Fly.io at roughly $23 a year is the cheapest credible option.

Against your own numbers in Constitution 1.1 to 1.4: entry fee $125 across 32
teams is $4,000 of inflow, of which about $3,450 goes to prizes and $130 to
Fantrax, leaving $420 a year of operations allocation.

    $23/year  =  5.5% of the $420 operations allocation  =  $0.73 per team per year
    $36/year  =  8.6% of the $420 operations allocation  =  $1.13 per team per year

So it is about a dollar a team. The catch is not the money, it is that Constitution
1.4 currently sends that $420 remainder into the Dynasty pot, and 1.4 describes the
Dynasty prize as accruing "approximately $420 per year". Funding hosting out of it
changes a number the constitution states. That is an amendment to 1.1 and 1.4, not
a commissioner decision, and it should be one line in the season addendum rather
than a quiet reallocation.


---

## Channels that already exist, and what could feed them later

Your `26-27 Season` category already has five channels. Two of them are outputs
this toolset can produce, which is worth knowing before anyone hand-maintains them:

    26-27-fa-claim-order        the free agent claim resolver produces exactly this
    26-27-waiver-claim-order    the claim order changes every time someone claims,
                                and that is a derived value, not a typed one
    26-27-player-waive-or-release
    26-27-trade-bait            GM-authored, leave alone
    26-27-trade-complaints      GM-authored, leave alone

Nothing is wired to those yet and nothing should be until you say so. Adding one is
now a small job: the poster takes a named channel, so a third name and a third
secret is the whole change. The one I would argue for first is
`26-27-waiver-claim-order`, because a claim order that updates itself after every
claim removes a standing source of argument, and the tools already compute it.
