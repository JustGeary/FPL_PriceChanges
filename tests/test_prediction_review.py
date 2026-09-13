import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.github/scripts'))
import prediction_review as r
from price_watch import UK,START,END


def row(pid,current,projected):
    return {'id':pid,'name':f'Player {pid}','team':'ARS','price':50,'current':current,'projected':projected,'eligible':True}


def state():
    rows=[row(1,101,98),row(2,90,110),row(3,95,98),row(4,20,30),row(5,-99,-110)]
    return {'deadline':'2026-09-13T23:00:00+00:00','batches':[
        {'mode':'alerts','rows':[row(2,101,110)],'parts':[{'status':'sent'}]},
        {'mode':'roundup','rows':rows,'parts':[{'status':'sent'}]}]}


class ReviewTests(unittest.TestCase):
    def test_correct_false_missed_watch_and_wrong_direction(self):
        result=r.compare(state(),{'1':51,'2':50,'3':51,'4':49,'5':51},'2026-09-13')
        self.assertEqual(result['predicted'],3)
        self.assertEqual([p['id'] for p in result['correct']],[1])
        self.assertEqual([p['id'] for p in result['false_alarms']],[2,5])
        self.assertEqual([p['id'] for p in result['missed']],[3,4,5])
        self.assertTrue(result['missed'][0]['on_watch'])
        self.assertFalse(result['early'][0]['correct'])
        text='\n'.join(r.render(result))
        self.assertIn('predicted fall; rose',text)
        self.assertIn('on watch',text)

    def test_missing_evidence_pending(self):
        self.assertEqual(r.compare(state(),None,'night')['status'],'pending')
        self.assertEqual(r.compare(state(),{'1':51},'night')['status'],'pending')
        s=state();s['batches'][1]['parts'][0]['status']='uncertain'
        self.assertEqual(r.compare(s,{'1':51,'2':50,'3':50,'4':50,'5':50},'night')['status'],'pending')

    def test_no_actual_changes_are_false_alarms_if_evidence_complete(self):
        result=r.compare(state(),{str(i):50 for i in range(1,6)},'night')
        self.assertEqual(result['actual'],0)
        self.assertEqual(len(result['false_alarms']),3)

    def test_duplicate_morning_and_pending_then_final(self):
        old=os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                root=Path('data/watch'); night=root/'2026-09-13'; night.mkdir(parents=True)
                (night/'ledger.json').write_text(json.dumps(state()))
                def save(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
                def send(batch,save):
                    for part in batch['parts']:
                        if part['status']!='sent':
                            sent.append(part['text']);part['status']='sent'
                    save()
                sent=[]
                now=dt.datetime(2026,9,14,7,tzinfo=dt.timezone.utc)
                for _ in range(2):r.morning(now,root,START,END,UK,save,send)
                self.assertEqual(len(sent),1);self.assertIn('pending',sent[0])
                save(Path('data/snapshots/2026-09-14.json'),{str(i):51 for i in range(1,6)})
                save(Path('data/delivery/2026-09-14.json'),{'status':'complete'})
                for _ in range(2):r.morning(now,root,START,END,UK,save,send)
                self.assertEqual(len(sent),2)
                r.morning(dt.datetime(2026,9,21,7,tzinfo=dt.timezone.utc),root,START,END,UK,save,send)
                self.assertEqual(len(sent),2)
            finally:os.chdir(old)

    def test_snapshot_without_complete_delivery_is_pending(self):
        old=os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                root=Path('data/watch');night=root/'2026-09-13';night.mkdir(parents=True)
                (night/'ledger.json').write_text(json.dumps(state()))
                Path('data/snapshots').mkdir();Path('data/delivery').mkdir()
                Path('data/snapshots/2026-09-14.json').write_text(json.dumps({str(i):50 for i in range(1,6)}))
                Path('data/delivery/2026-09-14.json').write_text(json.dumps({'status':'no_change_unconfirmed'}))
                self.assertEqual(r.collect(root,UK)[0]['status'],'pending')
            finally:os.chdir(old)

if __name__=='__main__':unittest.main()
