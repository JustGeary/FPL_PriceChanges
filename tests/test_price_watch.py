import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.github/scripts'))
import price_watch as w


def row(i=1,current=100,projected=110,eligible=True):
    return {'id':i,'name':f'Player {i}','team':'ARS','price':50,'current':current,'projected':projected,'eligible':eligible}


class WatchTests(unittest.TestCase):
    def test_categories_are_exclusive(self):
        self.assertEqual(w.category(row()),'Threshold reached')
        self.assertEqual(w.category(row(current=95)), 'Predicted to change')
        self.assertEqual(w.category(row(current=89,projected=-91)), 'Close to threshold')
        self.assertIsNone(w.category(row(eligible=False)))
        self.assertIsNone(w.category(row(current=80,projected=89)))

    def test_roundup_groups_and_marks_earlier(self):
        text='\n'.join(w.render([row(),row(2,-101,-110)],'roundup',dt.datetime(2026,9,13,22,30,tzinfo=dt.timezone.utc),{'1:rise'}))
        self.assertEqual(text.count('• Player 1'),1)
        self.assertIn('alerted earlier',text)
        self.assertIn('📈 Risers (1)',text)
        self.assertIn('📉 Fallers (1)',text)

    def test_long_digest_preserves_all_players_in_grouped_parts(self):
        chunks=w.render([row(i) for i in range(100)],'roundup',dt.datetime.now(dt.timezone.utc))
        self.assertGreater(len(chunks),1)
        self.assertLess(len(chunks),10)
        self.assertTrue(all(len(t.encode('utf-16-le'))//2<4096 for t in chunks))
        self.assertEqual(sum(t.count('• Player') for t in chunks),100)

    def test_windows_and_expiry(self):
        self.assertTrue(w.in_window(dt.datetime(2026,9,13,7,tzinfo=dt.timezone.utc),'alerts'))
        self.assertFalse(w.in_window(dt.datetime(2026,9,13,6,59,tzinfo=dt.timezone.utc),'alerts'))
        self.assertTrue(w.in_window(dt.datetime(2026,9,19,22,30,tzinfo=dt.timezone.utc),'roundup'))
        self.assertFalse(w.in_window(dt.datetime(2026,9,20,7,tzinfo=dt.timezone.utc),'alerts'))

    def test_uncertain_is_not_retried(self):
        batch={'parts':[{'text':'hello','status':'ready'}]}
        post=Mock(side_effect=w.requests.Timeout())
        with patch.dict(os.environ,{'TG_BOT_TOKEN':'test','TG_CHAT_ID':'1'}):
            with self.assertRaises(RuntimeError):w.send(batch,Mock(),post)
            with self.assertRaises(RuntimeError):w.send(batch,Mock(),post)
        self.assertEqual(post.call_count,1)

    def test_partial_digest_resumes_without_duplicate(self):
        batch={'parts':[{'text':'a','status':'sent','id':1},{'text':'b','status':'failed'}]}
        post=Mock(return_value=Mock(status_code=200,json=lambda:{'ok':True,'result':{'message_id':2}}))
        with patch.dict(os.environ,{'TG_BOT_TOKEN':'test','TG_CHAT_ID':'1'}):w.send(batch,Mock(),post)
        self.assertEqual(post.call_count,1)

    def test_intent_failure_prevents_send(self):
        post=Mock()
        with self.assertRaises(OSError):w.send({'parts':[{'text':'a','status':'ready'}]},Mock(side_effect=OSError()),post)
        post.assert_not_called()

    def test_duplicate_and_recross_do_not_alert_again(self):
        class Clock(dt.datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,9,13,10,tzinfo=dt.timezone.utc)
        original=os.getcwd()
        def save(path,state):path.write_text(json.dumps(state))
        def sent(batch,save):
            for part in batch['parts']:part['status']='sent'
            save()
        with tempfile.TemporaryDirectory() as root:
            os.chdir(root)
            try:
                with patch.object(w.dt,'datetime',Clock),patch.object(sys,'argv',['watch','--mode','alerts']),patch.object(w.requests,'get',return_value=Mock(json=lambda:{})),patch.object(w,'persist',side_effect=save),patch.object(w,'send',side_effect=sent) as send,patch.object(w,'review'),patch.object(w,'extract') as extract:
                    for current in [101,102,95,103]:
                        extract.return_value=('2026-09-14T00:00:00+01:00','2026-09-13T10:00:00Z',[row(current=current)])
                        w.main()
                    self.assertEqual(send.call_count,1)
                    extract.return_value=('2026-09-14T00:00:00+01:00','2026-09-13T10:00:00Z',[row(current=-101,projected=-110)])
                    w.main()
                    self.assertEqual(send.call_count,3)  # Withdrawal of rise, then new fall alert.
            finally:os.chdir(original)

    def test_awoniyi_withdrawal_recovery_and_deduplication(self):
        original=row(492,-100.3,-103.7)
        state={'batches':[{'mode':'alerts','rows':[original],'parts':[{'status':'sent'}]}]}
        now=dt.datetime(2026,9,13,15,30,tzinfo=dt.timezone.utc)
        self.assertIsNone(w.reassess(state,[row(492,-104.7,-164.4)],now))
        current=dict(row(492,-39.1,-57.5),status='s',news='Suspended')
        update=w.reassess(state,[current],now)
        self.assertIn('no longer supported',update['parts'][0]['text'])
        self.assertIn('Suspended',update['parts'][0]['text'])
        state['batches'].append(update)
        self.assertIsNone(w.reassess(state,[current],now))
        self.assertIsNone(w.reassess(state,[row(492,-95,-99)],now))
        recovery=w.reassess(state,[row(492,-99,-101)],now)
        self.assertIn('back at the threshold',recovery['parts'][0]['text'])
        state['batches'].append(recovery)
        self.assertIsNone(w.reassess(state,[row(492,-101,-110)],now))

    def test_reassessment_requires_evidence_and_delivered_alert(self):
        state={'batches':[{'mode':'alerts','rows':[row()],'parts':[{'status':'sent'}]}]}
        now=dt.datetime.now(dt.timezone.utc)
        for candidate in [row(current=89,projected=101),row(current=89,projected=90),row(current=None,projected=20)]:
            self.assertIsNone(w.reassess(state,[candidate],now))
        self.assertIsNotNone(w.reassess(state,[row(eligible=False)],now))
        state['batches'][0]['parts'][0]['status']='uncertain'
        self.assertIsNone(w.reassess(state,[row(eligible=False)],now))

    def test_extract_records_status_without_excluding_suspended_player(self):
        data={'game_config':{'settings':{'price_change_deadlines':['2026-09-13T23:00:00Z']},
                            'status':{'price_change_last_updated':'2026-09-13T14:20:00Z'}},
              'teams':[{'id':1,'short_name':'COV'}],
              'elements':[{'id':492,'web_name':'Awoniyi','team':1,'now_cost':55,
                           'status':'s','news':'Suspended','news_added':'2026-09-13T14:00:00Z',
                           'chance_of_playing_next_round':0,'price_change_percent':-100.3}]}
        _,_,rows=w.extract(data,dt.datetime(2026,9,13,14,30,tzinfo=dt.timezone.utc))
        self.assertTrue(rows[0]['eligible'])
        self.assertEqual(rows[0]['status'],'s')
        self.assertEqual(rows[0]['chance_of_playing_next_round'],0)
        self.assertEqual(rows[0]['news_added'],'2026-09-13T14:00:00Z')

if __name__=='__main__':unittest.main()
