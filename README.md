# PowerPlay commissioner tools

Read-only against the Fantrax public API, write-only to one Discord webhook.
No Fantrax login, no password, no cookie. See `SETUP.md`.

| Script                  | Does                                                            |
|-------------------------|-----------------------------------------------------------------|
| `pp_announcer.py`       | Announces new transactions across trades, claims and drops, lineup changes and draft picks. `--dry`, `--baseline`. |
| `pp_roster_check.py`    | Four hard roster limits per team. Exits 1 only on a real post-deadline violation. `--summary`. |
| `pp_cap_check.py`       | League-wide cap position. Fantrax salary is ALREADY the constitution-adjusted hit; do not halve it again. |
| `pp_trade_clock.py`     | Bill clock on pending trades. `--dry`.                          |
| `pp_discord_post.py`    | The only thing that posts. Fail-closed mention allowlist from `_tools/config/gm_map.csv`. |
| `pp_commish_alert.py`   | Ad hoc commissioner message.                                    |

Mentions can only ever reach a Discord id listed in `gm_map.csv`. `allowed_mentions`
is sent with `parse: []`, so an @everyone in message text cannot fire.
