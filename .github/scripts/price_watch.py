"""Seven-day Telegram-only prediction trial. Public data, durable grouped alerts."""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo
import requests

UK = ZoneInfo('Europe/London')
START = dt.date(2026, 9, 13)
END = START + dt.timedelta(days=7)  # Exclusive: last roundup 19 September.
ROOT = Path('data/price-watch')
URL = 'https://fantasy.premierleague.com/api/bootstrap-static/'


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def instant(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def in_window(now, mode):
    local = now.astimezone(UK)
    if not START <= local.date() < END:
        return False
    minute = local.hour * 60 + local.minute
    return (480 <= minute < 1395) if mode == 'alerts' else 1410 <= minute < 1440


def extract(data, now):
    deadline = instant(data['game_config']['settings']['price_change_deadlines'][0])
    expected = dt.datetime.combine(now.astimezone(UK).date() + dt.timedelta(days=1), dt.time(), UK)
    if deadline != expected:
        raise ValueError('API deadline does not match tonight; refusing stale predictions')
    updated = instant(data['game_config']['status']['price_change_last_updated'])
    age = (now - updated).total_seconds()
    if not -300 <= age <= 2700:
        raise ValueError('Prediction data is stale or has an invalid timestamp')
    teams = {t['id']: t['short_name'] for t in data['teams']}
    rows = []
    for p in data['elements']:
        projections = p.get('price_change_projections') or []
        projection = next((q for q in projections if q['offset'] == 0), {})
        lock = p.get('price_change_locked_until')
        eligible = not p.get('removed') and not p.get('price_change_calibrating') and not (lock and instant(lock) > now)
        rows.append({'id': p['id'], 'name': p['web_name'], 'team': teams[p['team']], 'price': p['now_cost'],
                     'current': number(p.get('price_change_percent')), 'projected': number(projection.get('projected_percent')),
                     'eligible': eligible, 'locked_until': lock, 'calibrating': p.get('price_change_calibrating')})
    if not rows:
        raise ValueError('Empty player response')
    return deadline.isoformat(), updated.isoformat(), rows


def key(row):
    return f"{row['id']}:{'rise' if row['current'] > 0 else 'fall'}"


def category(row):
    if not row['eligible']:
        return None
    current, projected = row['current'], row['projected']
    if current is not None and abs(current) >= 100:
        return 'Threshold reached'
    if projected is not None and abs(projected) >= 100:
        return 'Predicted to change'
    if any(v is not None and abs(v) >= 90 for v in (current, projected)):
        return 'Close to threshold'
    return None


def fmt(value):
    return 'n/a' if value is None else f'{value:+.1f}%'


def render(rows, mode, now, alerted=()):
    local = now.astimezone(UK)
    title = 'FPL threshold watch' if mode == 'alerts' else 'FPL tonight — roundup'
    header = f'{title} · {local:%d %b, %H:%M} UK\nFor midnight tonight · 7-day Telegram trial\nFPL predictions — not guaranteed changes.'
    lines = []
    sections = ['Threshold reached'] if mode == 'alerts' else ['Threshold reached', 'Predicted to change', 'Close to threshold']
    for section in sections:
        selected = [r for r in rows if category(r) == section]
        if not selected:
            continue
        lines += ['', section]
        for direction, label in [(1, '📈 Risers'), (-1, '📉 Fallers')]:
            field = 'projected' if section == 'Predicted to change' else 'current'
            def value(r):
                v = r[field]
                if section == 'Close to threshold':
                    v = max((v for v in (r['current'],r['projected']) if v is not None), key=abs)
                return v
            group = sorted([r for r in selected if value(r) * direction > 0], key=lambda r: (-abs(value(r)),r['name']))
            if not group:
                continue
            lines.append(f'{label} ({len(group)})')
            for r in group:
                earlier = ' · alerted earlier' if r['current'] is not None and key(r) in alerted else ''
                lines.append(f"• {r['name']} ({r['team']}) £{r['price']/10:.1f}m | now {fmt(r['current'])} → midnight {fmt(r['projected'])}{earlier}")
    if not lines:
        lines = ['', 'No eligible players currently at or close to the threshold.']
    if mode == 'alerts':
        lines += ['', 'First observed at/over the threshold in this monitoring window.']
    # Grouped parts only when Telegram's size limit requires it; count UTF-16 units conservatively.
    chunks, current = [], header
    for line in lines:
        if len((current + '\n' + line).encode('utf-16-le')) // 2 > 3400:
            chunks.append(current)
            current = header + '\n(continued)'
        current += '\n' + line
    chunks.append(current)
    return [f'{text}\nPart {i}/{len(chunks)}' if len(chunks)>1 else text for i,text in enumerate(chunks,1)]


def persist(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
    subprocess.run(['git','add','data/price-watch'],check=True)
    if subprocess.run(['git','diff','--cached','--quiet']).returncode:
        subprocess.run(['git','commit','-m','Record Telegram prediction trial observation'],check=True)
        subprocess.run(['git','push','origin','HEAD:main'],check=True)


def send(batch, save, post=requests.post):
    for part in batch['parts']:
        if part['status'] == 'sent':
            continue
        if part['status'] in ('sending','uncertain'):
            raise RuntimeError('Uncertain Telegram delivery: review before retrying')
        part['status'] = 'sending'
        save()
        try:
            response = post('https://api.telegram.org/bot'+os.environ['TG_BOT_TOKEN']+'/sendMessage',
                            json={'chat_id':os.environ['TG_CHAT_ID'],'text':part['text'],'disable_web_page_preview':True},timeout=30)
            if 400 <= response.status_code < 500 and response.status_code != 408:
                part['status'] = 'failed'
            elif response.status_code != 200:
                part['status'] = 'uncertain'
            else:
                body=response.json()
                if body.get('ok'):
                    part.update(status='sent',id=body['result']['message_id'])
                else:
                    part['status']='failed'
        except (requests.RequestException,ValueError,KeyError):
            part['status']='uncertain'
        save()
        if part['status']!='sent':
            raise RuntimeError('Telegram delivery '+part['status']+'; no automatic ambiguous POST retry')


def review():
    results=[]
    for path in sorted(ROOT.glob('*/ledger.json')):
        state=json.loads(path.read_text(encoding='utf-8'))
        actual_day=instant(state['deadline']).astimezone(UK).date().isoformat()
        actual_path=Path('data/snapshots')/(actual_day+'.json')
        roundup=next((b for b in state['batches'] if b['mode']=='roundup'),None)
        if not roundup or not actual_path.exists():
            results.append({'night':path.parent.name,'status':'awaiting roundup or actual snapshot'})
            continue
        actual=json.loads(actual_path.read_text())
        rows=roundup['rows']
        expected={(r['id'],1 if r['projected']>0 else -1) for r in rows if r['eligible'] and r['projected'] is not None and abs(r['projected'])>=100}
        changed={(r['id'],1 if actual[str(r['id'])]>r['price'] else -1) for r in rows if str(r['id']) in actual and actual[str(r['id'])]!=r['price']}
        early=[r for b in state['batches'] if b['mode']=='alerts' and all(p['status']=='sent' for p in b['parts']) for r in b['rows']]
        early_correct=[r['id'] for r in early if str(r['id']) in actual and (actual[str(r['id'])]-r['price'])*r['current']>0]
        results.append({'night':path.parent.name,'status':'compared','predicted':len(expected),'actual':len(changed),
                        'correct':sorted(expected & changed),'not_changed_as_predicted':sorted(expected-changed),'missed':sorted(changed-expected),
                        'early_alerted':len(early),'early_correct_player_ids':early_correct,
                        'early_not_changed_as_alerted':[r['id'] for r in early if r['id'] not in early_correct]})
    ROOT.mkdir(parents=True,exist_ok=True)
    output=ROOT/'review.json'
    output.write_text(json.dumps(results,indent=2)+'\n')
    return results


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['alerts','roundup','preview','review'],default='preview')
    args=parser.parse_args()
    now=dt.datetime.now(dt.timezone.utc)
    if args.mode=='review':
        results=review()
        persist(ROOT/'review.json',results)
        print(json.dumps(results))
        return
    if args.mode!='preview' and not in_window(now,args.mode):
        print('Outside trial dates or send window; no notification')
        return
    response=requests.get(URL,timeout=40)
    response.raise_for_status()
    deadline,updated,rows=extract(response.json(),now)
    if args.mode=='preview':
        Path('price-watch-preview.txt').write_text('\n\n'.join(render(rows,'roundup',now)),encoding='utf-8')
        print(Path('price-watch-preview.txt').read_text(encoding='utf-8'))
        return
    day=now.astimezone(UK).date().isoformat()
    folder=ROOT/day
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/'ledger.json'
    state=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'deadline':deadline,'batches':[]}
    if state['deadline']!=deadline:
        raise RuntimeError('Ledger deadline mismatch')
    (folder/(now.strftime('%H%M%SZ')+'.json')).write_text(json.dumps({'at':now.isoformat(),'updated':updated,'deadline':deadline,'rows':rows},ensure_ascii=False),encoding='utf-8')
    # Resume definite rejections only within 45 minutes; never resend uncertain parts.
    for batch in state['batches']:
        if any(p['status'] in ('sending','uncertain') for p in batch['parts']):
            raise RuntimeError('Uncertain prior Telegram batch requires review')
        if any(p['status']!='sent' for p in batch['parts']) and (now-instant(batch['at'])).total_seconds()<=2700:
            send(batch,lambda:persist(path,state))
    reserved={k for b in state['batches'] for k in b['keys']}
    alerted={k for b in state['batches'] if b['mode']=='alerts' and all(p['status']=='sent' for p in b['parts']) for k in b['keys']}
    candidates=[r for r in rows if category(r)=='Threshold reached' and key(r) not in reserved]
    if args.mode=='roundup' and any(b['mode']=='roundup' for b in state['batches']):
        persist(path,state)
        return
    if args.mode=='alerts' and not candidates:
        persist(path,state)
        print('No new threshold crossings; Telegram silent')
        return
    selected=candidates if args.mode=='alerts' else rows
    batch={'at':now.isoformat(),'mode':args.mode,'keys':[key(r) for r in candidates] if args.mode=='alerts' else [],
           'rows':selected,'parts':[{'text':text,'status':'ready'} for text in render(selected,args.mode,now,alerted)]}
    state['batches'].append(batch)
    persist(path,state)
    send(batch,lambda:persist(path,state))
    review()
    persist(path,state)
    print(f"Telegram {args.mode}: {len(batch['parts'])} grouped message(s) sent")


if __name__=='__main__':
    main()
