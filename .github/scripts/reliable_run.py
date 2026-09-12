"""Persist an outbox and per-message receipts before advancing delivery.

An interrupted/ambiguous POST is deliberately held for review, never retried blindly.
"""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import requests
import price_diff as diff
import post_to_x as x

UTC = dt.timezone.utc
STATE_DIR = Path('data/delivery')


def now():
    return dt.datetime.now(UTC)


def persist(path, state):
    state['updated_at'] = now().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'data/delivery', 'data/snapshots'], check=True)
    if subprocess.run(['git', 'diff', '--cached', '--quiet']).returncode:
        subprocess.run(['git', 'commit', '-m', f"FPL delivery {state['date']}: {state['status']}"], check=True)
        # Failure here stops before any further notification. Never suppress push errors.
        subprocess.run(['git', 'push', 'origin', 'HEAD:main'], check=True)


def fetch():
    started = now()
    response = requests.get(diff.FPL_URL, timeout=40)
    print(json.dumps({'requested_at': started.isoformat(), 'seconds': (now()-started).total_seconds(),
                      'http': response.status_code, 'age': response.headers.get('Age'),
                      'date': response.headers.get('Date'), 'cache_control': response.headers.get('Cache-Control')}), flush=True)
    response.raise_for_status()
    data = response.json()
    players = data.get('elements', [])
    if not players or not data.get('teams'):
        raise ValueError('Incomplete FPL response')
    prices = {str(p['id']): p['now_cost'] for p in players}
    if len(prices) != len(players) or any(not isinstance(v, int) or v <= 0 for v in prices.values()):
        raise ValueError('Invalid FPL prices')
    return data, prices


def observe(previous, clock=now, sleep=time.sleep, fetcher=fetch):
    # Early runs allow stale data to clear through 00:12 UK, with a hard attempt bound.
    # Two matching changed samples are evidence of stability, not proof of publication completeness.
    last_changed = None
    last_unchanged = (None, None)
    for attempt in range(9):
        try:
            data, prices = fetcher()
            if not set(previous).issubset(prices):
                raise ValueError('FPL response is missing baseline players')
            changed = any(prices[k] != v for k, v in previous.items())
            last_unchanged = (None, None) if changed else (data, prices)
            if changed and prices == last_changed:
                return data, prices
            last_changed = prices if changed else None
        except (requests.RequestException, ValueError) as exc:
            print(f'FPL fetch failed: {type(exc).__name__}', flush=True)
            last_changed = None
            last_unchanged = (None, None)
        uk = clock().astimezone(diff.UK_TZ)
        if attempt >= 2 and (uk.hour != 0 or uk.minute >= 12):
            break
        if attempt < 8:
            sleep(60)
    return last_unchanged


def build_state(day, data, prices, previous):
    players = {int(p['id']): (p['web_name'], p['now_cost'], p['team']) for p in data['elements']}
    teams = {int(t['id']): t['short_name'] for t in data['teams']}
    ownership = {int(p['id']): float(p.get('selected_by_percent') or 0) for p in data['elements']}
    timestamp = now()
    if timestamp.astimezone(diff.UK_TZ).date().isoformat() != day:
        raise RuntimeError('UK date changed before message preparation')
    diff.main({'timestamp': timestamp, 'players_data': (players, teams, ownership),
               'previous': previous,
               'gameweek': next((e['id'] for e in data.get('events', []) if e.get('is_current')), None)})
    messages = [{'channel': 'telegram', 'text': Path('tg_message.txt').read_text(encoding='utf-8'), 'status': 'ready'}]
    for group in ('fallers', 'risers'):
        paths = sorted(Path('.').glob(f'x_status_{group}_*.txt'), key=lambda p: int(p.stem.rsplit('_', 1)[1]))
        messages.extend({'channel': 'x', 'group': group, 'text': p.read_text(encoding='utf-8'), 'status': 'ready'} for p in paths)
    return {'date': day, 'status': 'prepared', 'messages': messages, 'report': Path('changes.md').read_text(encoding='utf-8')}


def deliver(state, save, x_session=None, tg_post=requests.post):
    problems = []
    parents = {}
    blocked_groups = set()
    for item in state['messages']:
        group = item.get('group', 'telegram')
        if item['status'] == 'sent':
            if item['channel'] == 'x':
                parents[group] = item['id']
            continue
        if item['status'] in ('sending', 'uncertain') or group in blocked_groups:
            blocked_groups.add(group)
            problems.append('Uncertain delivery requires review: ' + group)
            continue
        item['status'] = 'sending'
        save()  # Durable intent must reach GitHub BEFORE the external side effect.
        try:
            if item['channel'] == 'x':
                if x_session is None:
                    x_session = x.get_session()
                payload = {'text': item['text']}
                if group in parents:
                    payload['reply'] = {'in_reply_to_tweet_id': parents[group]}
                response = x_session.post(x.POST_URL, json=payload, timeout=30)
            else:
                response = tg_post('https://api.telegram.org/bot' + os.environ['TG_BOT_TOKEN'] + '/sendMessage',
                                   json={'chat_id': os.environ['TG_CHAT_ID'], 'text': item['text'],
                                         'parse_mode': 'HTML', 'disable_web_page_preview': True}, timeout=30)
            item['http_status'] = response.status_code
            if 400 <= response.status_code < 500 and response.status_code != 408:
                item['status'] = 'failed'
                problems.append(f'{group}: HTTP {response.status_code}')
                blocked_groups.add(group)
            elif not 200 <= response.status_code < 300:
                item['status'] = 'uncertain'
                problems.append(f'{group}: ambiguous HTTP {response.status_code}')
                blocked_groups.add(group)
            else:
                body = response.json()
                if item['channel'] == 'telegram' and not body.get('ok'):
                    item['status'] = 'failed'
                    problems.append('Telegram rejected message')
                    blocked_groups.add(group)
                else:
                    item['id'] = str(body['data']['id'] if item['channel'] == 'x' else body['result']['message_id'])
                    item['status'] = 'sent'
                    if item['channel'] == 'x':
                        parents[group] = item['id']
        except (requests.RequestException, ValueError, KeyError) as exc:
            # Do not log exception text: Telegram URLs contain a secret token.
            item['status'] = 'uncertain'
            problems.append(f'{group}: uncertain result ({type(exc).__name__})')
            blocked_groups.add(group)
        save()  # A failed receipt push aborts, leaving durable 'sending' for human review.
    state['status'] = 'complete' if all(m['status'] == 'sent' for m in state['messages']) else 'needs_attention'
    save()
    if problems:
        raise RuntimeError('; '.join(problems))


def main():
    day = now().astimezone(diff.UK_TZ).date().isoformat()
    path = STATE_DIR / (day + '.json')
    # Preserve unresolved prior days; never silently replace their outboxes.
    for prior in sorted(STATE_DIR.glob('*.json')):
        old = json.loads(prior.read_text(encoding='utf-8'))
        if prior < path and old.get('messages') and old['status'] != 'complete':
            raise RuntimeError(f'Unresolved delivery {prior.stem}; review before continuing')
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    if '--check' in sys.argv:
        if not state or state['status'] != 'complete':
            raise RuntimeError('00:15 completion check: ' + (state['status'] if state else 'no delivery record'))
        print('Completion check passed for ' + day)
        return
    if state and state['status'] == 'complete':
        print('Already delivered for ' + day)
        return
    if not state or not state.get('messages'):
        baseline = diff.SNAP_DIR / ((dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat() + '.json')
        if not baseline.exists():
            raise RuntimeError('Missing yesterday UK snapshot; baseline needs review')
        previous = json.loads(baseline.read_text(encoding='utf-8'))
        data, prices = observe(previous)
        if data is None:
            raise RuntimeError('FPL response unavailable or changed prices did not stabilise')
        if not any(prices[k] != v for k, v in previous.items()):
            state = {'date': day, 'status': 'no_change_unconfirmed', 'messages': []}
            # Carry forward observed prices so a genuine no-change day does not break tomorrow.
            # Today's state stays unconfirmed and the fallback still rechecks yesterday's baseline.
            (diff.SNAP_DIR / (day + '.json')).write_text(json.dumps(prices), encoding='utf-8')
            persist(path, state)
            raise RuntimeError('No stable changed prices confirmed; fallback will check again')
        if now().astimezone(diff.UK_TZ).date().isoformat() != day:
            raise RuntimeError('UK date changed during observation')
        state = build_state(day, data, prices, previous)
        persist(path, state)
    deliver(state, lambda: persist(path, state))
    with open(os.environ.get('GITHUB_STEP_SUMMARY', os.devnull), 'a', encoding='utf-8') as out:
        out.write(f"## {day}: delivery complete\n")
        for item in state['messages']:
            if item['channel'] == 'x':
                out.write(f"- {item['group']}: https://x.com/i/web/status/{item['id']}\n")


if __name__ == '__main__':
    main()
