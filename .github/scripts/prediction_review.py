"""Score the warnings actually displayed, with missing evidence kept pending."""
import datetime as dt
import json
from pathlib import Path


def direction(value):
    return 1 if value > 0 else -1


def expected_direction(row):
    if not row['eligible']:
        return None
    for field in ('current', 'projected'):
        value = row[field]
        if value is not None and abs(value) >= 100:
            return direction(value)
    return None


def close(row):
    return row['eligible'] and expected_direction(row) is None and any(
        value is not None and 90 <= abs(value) < 100 for value in (row['current'], row['projected']))


def compare(state, actual, night):
    result = {'night': night, 'status': 'pending'}
    roundup = next((b for b in state['batches'] if b['mode'] == 'roundup'), None)
    if not roundup or not all(p['status'] == 'sent' for p in roundup['parts']):
        return dict(result, reason='The nightly roundup was not fully delivered.')
    rows = roundup['rows']
    early = [r for b in state['batches'] if b['mode'] == 'alerts' and all(p['status'] == 'sent' for p in b['parts']) for r in b['rows']]
    if actual is None or any(str(r['id']) not in actual for r in rows + early):
        return dict(result, reason='Confirmed actual prices are missing or incomplete.')
    expected = {(r['id'], expected_direction(r)) for r in rows if expected_direction(r) is not None}
    changes = {(r['id'], direction(actual[str(r['id'])] - r['price'])) for r in rows if actual[str(r['id'])] != r['price']}
    names = {r['id']: r for r in rows}
    def detail(pair):
        pid, sign = pair
        row = names[pid]
        return {'id':pid, 'name':row['name'], 'team':row['team'], 'direction':sign,
                'old':row['price'], 'new':actual[str(pid)], 'on_watch':close(row)}
    early_results = []
    for r in early:
        new = actual[str(r['id'])]
        early_results.append({'id':r['id'], 'name':r['name'], 'team':r['team'], 'old':r['price'], 'new':new,
                              'direction':direction(r['current']), 'correct':(new-r['price'])*r['current'] > 0})
    return dict(result, status='compared', predicted=len(expected), actual=len(changes),
                correct=[detail(p) for p in sorted(expected & changes)],
                false_alarms=[detail(p) for p in sorted(expected - changes)],
                missed=[detail(p) for p in sorted(changes - expected)], early=early_results)


def collect(root, uk):
    results=[]
    for path in sorted(root.glob('*/ledger.json')):
        state=json.loads(path.read_text(encoding='utf-8'))
        day=dt.datetime.fromisoformat(state['deadline']).astimezone(uk).date().isoformat()
        snapshot=Path('data/snapshots')/(day+'.json')
        delivery=Path('data/delivery')/(day+'.json')
        actual=None
        if snapshot.exists() and delivery.exists() and json.loads(delivery.read_text(encoding='utf-8')).get('status')=='complete':
            actual=json.loads(snapshot.read_text(encoding='utf-8'))
        results.append(compare(state,actual,path.parent.name))
    root.mkdir(parents=True,exist_ok=True)
    (root/'review.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return results


def render(result):
    header=f"FPL prediction review — night of {result['night']}\n23:30 roundup vs confirmed overnight prices"
    if result['status']=='pending':
        return [header+'\n\n⏳ Review pending\n'+result['reason']+'\nNo predictions counted as incorrect while evidence is missing.']
    lines=[f"{result['predicted']} predicted · {len(result['correct'])} correct · {len(result['false_alarms'])} false alarms",
           f"{result['actual']} actual changes · {len(result['missed'])} missed ({sum(r['on_watch'] for r in result['missed'])} on watch)",
           'Close-only players are not counted as firm predictions.']
    def movement(r):
        delta=r['new']-r['old']
        return 'unchanged' if delta==0 else f"{'rose' if delta>0 else 'fell'} £{abs(delta)/10:.1f}m"
    for title,items in [('❌ False alarms',result['false_alarms']),('🔎 Missed changes',result['missed'])]:
        lines+=['',title]
        if not items:lines.append('None')
        for r in items:
            label='rise' if r['direction']>0 else 'fall'
            note=' · on watch' if title.startswith('🔎') and r['on_watch'] else ''
            prefix=f'predicted {label}; ' if title.startswith('❌') else ''
            lines.append(f"• {r['name']} ({r['team']}) — {prefix}{movement(r)}{note}")
    early=result['early']
    wrong=[r for r in early if not r['correct']]
    lines+=['','Earlier threshold alerts',f'{len(early)} alerts · {len(early)-len(wrong)} correct · {len(wrong)} false alarms']
    for r in wrong:
        lines.append(f"• {r['name']} ({r['team']}) — alerted {'rise' if r['direction']>0 else 'fall'}; {movement(r)}")
    chunks=[]
    current=header
    for line in lines:
        if len((current+'\n'+line).encode('utf-16-le'))//2>3400:
            chunks.append(current)
            current=header+'\n(continued)'
        current+='\n'+line
    chunks.append(current)
    return [f'{text}\nPart {i}/{len(chunks)}' if len(chunks)>1 else text for i,text in enumerate(chunks,1)]


def morning(now, root, start, end, uk, save, send):
    local=now.astimezone(uk)
    if not start < local.date() <= end or local.hour < 8:
        print('Outside morning review dates/time; no Telegram message')
        return
    night=(local.date()-dt.timedelta(days=1)).isoformat()
    results=collect(root,uk)
    result=next((r for r in results if r['night']==night),{'night':night,'status':'pending','reason':'No trial observations were recorded for this night.'})
    path=root/'morning-receipts'/f'{night}.json'
    receipt=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'night':night,'reports':{}}
    status=result['status']
    # A pending notice and later completed result may each be sent once; never repeat either.
    if 'compared' in receipt['reports']:
        status='compared'
    if status not in receipt['reports']:
        receipt['reports'][status]={'parts':[{'text':t,'status':'ready'} for t in render(result)]}
        save(path,receipt)
    for batch in receipt['reports'].values():
        if any(p['status'] in ('sending','uncertain') for p in batch['parts']):
            raise RuntimeError('Uncertain morning delivery requires review')
    batch=receipt['reports'][status]
    send(batch,lambda:save(path,receipt))
    save(path,receipt)
    print('Morning prediction review: '+status)
