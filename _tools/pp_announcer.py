#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerPlay activity announcer v2.  READ ONLY.

v1 watched ONE source and missed every trade in the league. Fantrax splits
activity across FOUR places:

  view=TRADE          getTransactionDetailsHistory   trades, grouped by txSetId
  view=CLAIM_DROP     getTransactionDetailsHistory   waiver claims and drops
  view=LINEUP_CHANGE  getTransactionDetailsHistory   daily lineup moves  (OFF by default, 216 and counting)
  getDraftResults                                    draft selections  (a 4th class, in no view)

Announces only what it has not seen. State per source in _tools/state/.
  --dry        print, do not post
  --baseline   record everything as seen, announce nothing
  --lineups    include lineup changes (noisy)
"""
import json, pathlib, sys, re, html, collections, datetime, warnings, importlib.util
warnings.filterwarnings('ignore')
from fantraxapi import FantraxAPI

HERE = pathlib.Path(__file__).parent
STATE = HERE / "state" / "announcer_v2.json"
def _league():
    """Season config, so the annual rollover is one edit in one file."""
    import json as _j, pathlib as _p
    f = _p.Path(__file__).parent / "config" / "league.json"
    if f.exists():
        d = _j.loads(f.read_text())
        return d["league_id"], d.get("season", ""), d.get("season_label", "")
    return "aizqwpvxmoc9uxas", "2627", "2026-27"   # fallback, current season

LEAGUE_ID, SEASON, SEASON_LABEL = _league()


def clean(s):
    return html.unescape(re.sub(r'<[^>]+>', '', str(s or ''))).strip()


def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(d):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=1, sort_keys=True))


_ABBR = {}


def abbr(api, name):
    """League team -> Fantrax's own 3-letter code. EB 2026-09-25."""
    if not _ABBR:
        try:
            _ABBR.update({t.name: (t.short or t.name) for t in api.teams})
        except Exception:
            pass
    return _ABBR.get((name or "").strip(), (name or "").strip())


def get_trades(api):
    raw = api._request("getTransactionDetailsHistory", maxResultsPerPage="500", view="TRADE")
    tr = collections.OrderedDict()
    for r in raw['table']['rows']:
        cells = {c.get('key'): c for c in r.get('cells', [])}
        tid = r.get('txSetId'); sc = r.get('scorer') or {}
        if r.get('draftPickDisplayParts'):
            d = r['draftPickDisplayParts']
            asset = "%s %s" % (clean(d.get('year')), clean(d.get('roundInfo'))); kind = "pick"
        else:
            asset = sc.get('name') or ''; kind = "player"
        t = tr.setdefault(tid, dict(date=cells.get('date', {}).get('content'),
                                    comment=clean(r.get('result', {}).get('content', '')).replace('Executed', '').strip(),
                                    fees=r.get('feesUsed'), moves=[]))
        t['moves'].append((cells.get('from', {}).get('content'),
                           cells.get('to', {}).get('content'), kind, asset))
    return tr


def fmt_trade(api, tid, v):
    sides = collections.defaultdict(list)
    for f, t, k, a in v['moves']:
        sides[t].append(a)
    L = ["**TRADE**  %s" % (v['date'] or '')]
    for team, assets in sides.items():
        L.append("  %s receive: %s" % (abbr(api, team), ", ".join(assets)))
    if v['comment']:
        L.append("  _comment: %s_" % v['comment'][:160])
    return "\n".join(L)


def get_claims(api):
    """Claims AND drops. Two bugs lived here until 2026-09-25:

      a. It printed `resultCode`, which is "EXECUTED" for every row, so a pickup
         and a drop read identically. The field that distinguishes them is
         `transactionType` (Claim / Drop).
      b. It read cells 'from'/'to', which exist in the TRADE view but NOT here:
         this view's column is 'team'. So the league team was silently blank.

    Format per EB 2026-09-25: LEAGUE TEAM, then player, then the real NHL club.
    """
    raw = api._request("getTransactionDetailsHistory", maxResultsPerPage="500", view="CLAIM_DROP")
    out = {}
    for r in raw['table']['rows']:
        cells = {c.get('key'): c.get('content') for c in r.get('cells', [])}
        sc = r.get('scorer') or {}
        key = "%s|%s" % (r.get('txSetId'), sc.get('scorerId') or sc.get('name'))
        nhl = sc.get('teamShortName')
        kind = (r.get('transactionType') or '').upper() or 'CLAIM/DROP'
        team = cells.get('team') or cells.get('to') or cells.get('from') or ''
        out[key] = "**%s**  %s  %s (%s, %s)  %s" % (
            kind, abbr(api, team), sc.get('name'), sc.get('posShortNames'),
            "unsigned" if nhl in ('(N/A)', 'N/A', '', None) else nhl,
            cells.get('date') or '')
    return out


def get_draft(api):
    d = api._request("getDraftResults")
    scorers = {x['scorerId']: x for x in (d.get('scorers') or []) if isinstance(x, dict)}
    teams = {t.team_id: t.name for t in api.teams}
    out = {}
    for p in d.get('draftPicksOrdered') or []:
        s = scorers.get(p.get('scorerId'), {})
        key = "%s-%s" % (p.get('round'), p.get('pickNumber'))
        out[key] = "**DRAFT** R%s P%s - %s select %s (%s, %s)" % (
            p.get('round'), p.get('pickNumber'), teams.get(p.get('teamId'), '?'),
            s.get('name', '?'), s.get('posShortNames', ''), s.get('teamShortName', ''))
    return out


def main():
    dry = "--dry" in sys.argv
    baseline = "--baseline" in sys.argv
    api = FantraxAPI(LEAGUE_ID)
    st = load()
    blocks, counts = [], {}

    trades = get_trades(api)
    seen = set(st.get('trades', []))
    new = [t for t in trades if t not in seen]
    counts['trades'] = (len(trades), len(new))
    for tid in new:
        blocks.append(fmt_trade(api, tid, trades[tid]))
    st['trades'] = sorted(set(trades))

    claims = get_claims(api)
    seen = set(st.get('claims', []))
    newc = [k for k in claims if k not in seen]
    counts['claims'] = (len(claims), len(newc))
    for k in newc:
        blocks.append(claims[k])
    st['claims'] = sorted(claims)

    draft = get_draft(api)
    seen = set(st.get('draft', []))
    newd = [k for k in draft if k not in seen]
    counts['draft'] = (len(draft), len(newd))
    for k in newd[:12]:
        blocks.append(draft[k])
    if len(newd) > 12:
        blocks.append("_...and %d more picks_" % (len(newd) - 12))
    st['draft'] = sorted(draft)

    print("sources: " + " | ".join("%s %d total, %d new" % (k, v[0], v[1]) for k, v in counts.items()))

    if baseline:
        if not dry: save(st)
        print("baseline recorded, nothing announced")
        return 0
    if not blocks:
        if not dry: save(st)
        print("nothing new")
        return 0

    msg = "\n\n".join(blocks)
    if len(msg) > 1850:
        msg = msg[:1840] + "\n_...truncated_"
    if dry:
        print("\n--- WOULD POST (%d chars) ---\n%s" % (len(msg), msg))
        return 0
    spec = importlib.util.spec_from_file_location('m', HERE / 'pp_discord_post.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    rc = m.post(msg)
    if rc == 0: save(st)
    return rc


if __name__ == "__main__":
    sys.exit(main())
