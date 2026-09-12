import datetime as dt
import json
import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / '.github/scripts'))
import reliable_run as run


def response(status=201, ident='123'):
    return Mock(status_code=status, json=lambda: {'data': {'id': ident}})


class DeliveryTests(unittest.TestCase):
    def state(self):
        return {'status': 'prepared', 'messages': [
            {'channel': 'x', 'group': 'fallers', 'text': 'one', 'status': 'ready'},
            {'channel': 'x', 'group': 'fallers', 'text': 'two', 'status': 'ready'},
            {'channel': 'x', 'group': 'risers', 'text': 'three', 'status': 'ready'}]}

    def test_resume_keeps_thread_and_skips_sent(self):
        state = self.state()
        state['messages'][0].update(status='sent', id='old')
        session = Mock()
        session.post.side_effect = [response(ident='second'), response(ident='third')]
        run.deliver(state, Mock(), session)
        self.assertEqual(session.post.call_count, 2)
        self.assertEqual(session.post.call_args_list[0].kwargs['json']['reply']['in_reply_to_tweet_id'], 'old')
        self.assertNotIn('reply', session.post.call_args_list[1].kwargs['json'])
        self.assertEqual(state['status'], 'complete')

    def test_payment_failure_preserves_ready_thread_and_other_group(self):
        state = self.state()
        session = Mock()
        session.post.side_effect = [response(402), response()]
        with self.assertRaises(RuntimeError):
            run.deliver(state, Mock(), session)
        self.assertEqual([m['status'] for m in state['messages']], ['failed', 'ready', 'sent'])
        session.post.side_effect = [response(ident='a'), response(ident='b')]
        run.deliver(state, Mock(), session)
        self.assertEqual(state['status'], 'complete')

    def test_unknown_result_is_never_reposted(self):
        state = self.state()
        session = Mock()
        session.post.side_effect = [run.requests.Timeout(), response()]
        with self.assertRaises(RuntimeError):
            run.deliver(state, Mock(), session)
        session.reset_mock()
        with self.assertRaises(RuntimeError):
            run.deliver(state, Mock(), session)
        session.post.assert_not_called()

    def test_intent_must_be_saved_before_post(self):
        session = Mock()
        with self.assertRaises(OSError):
            run.deliver(self.state(), Mock(side_effect=OSError('push failed')), session)
        session.post.assert_not_called()

    def test_receipt_failure_leaves_durable_sending(self):
        durable = []
        state = self.state()
        def save():
            if durable:
                raise OSError('push failed')
            durable.append(json.loads(json.dumps(state)))
        session = Mock()
        session.post.return_value = response()
        with self.assertRaises(OSError):
            run.deliver(state, save, session)
        self.assertEqual(durable[0]['messages'][0]['status'], 'sending')
        self.assertEqual(session.post.call_count, 1)

    def test_telegram_rejection_does_not_block_x(self):
        state = self.state()
        state['messages'].insert(0, {'channel': 'telegram', 'text': 'hello', 'status': 'ready'})
        session = Mock()
        session.post.return_value = response()
        with patch.dict('os.environ', {'TG_BOT_TOKEN':'test','TG_CHAT_ID':'1'}):
            with self.assertRaises(RuntimeError):
                run.deliver(state, Mock(), session, Mock(return_value=Mock(status_code=200, json=lambda: {'ok':False})))
        self.assertEqual(session.post.call_count, 3)
        self.assertEqual(state['messages'][0]['status'], 'failed')

    def test_waits_for_stable_changed_prices(self):
        fetch = Mock(side_effect=[({}, {'1':50}), ({}, {'1':51}), ({}, {'1':52}), ({}, {'1':52})])
        data, prices = run.observe({'1':50}, clock=lambda:dt.datetime(2026,9,12,23,5,tzinfo=dt.timezone.utc), sleep=Mock(), fetcher=fetch)
        self.assertEqual(prices, {'1':52})
        self.assertEqual(fetch.call_count, 4)

    def test_nochange_is_bounded_and_preserved(self):
        fetch = Mock(return_value=({}, {'1':50}))
        _, prices = run.observe({'1':50}, clock=lambda:dt.datetime(2026,9,13,3,tzinfo=dt.timezone.utc), sleep=Mock(), fetcher=fetch)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(prices, {'1':50})

    def test_fetch_failures_are_not_nochange(self):
        fetch = Mock(side_effect=run.requests.Timeout())
        self.assertEqual(run.observe({'1':50}, clock=lambda:dt.datetime(2026,9,13,3,tzinfo=dt.timezone.utc), sleep=Mock(), fetcher=fetch), (None,None))

    def test_snapshot_uses_uk_day_in_summer_and_winter(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root, patch.object(run.diff, 'SNAP_DIR', pathlib.Path(root)):
            for timestamp, expected in [(dt.datetime(2026,9,12,23,5,tzinfo=dt.timezone.utc), '2026-09-13.json'),
                                        (dt.datetime(2026,12,13,0,5,tzinfo=dt.timezone.utc), '2026-12-13.json')]:
                self.assertEqual(run.diff.save_snapshot(timestamp, {1:('name',50,1)}).name, expected)

    def test_prepare_uses_one_observed_payload_and_uk_date(self):
        import tempfile
        import os
        data = {'elements': [
            {'id':1,'web_name':'Riser','now_cost':51,'team':1,'selected_by_percent':'10'},
            {'id':2,'web_name':'Faller','now_cost':49,'team':1,'selected_by_percent':'20'}],
            'teams':[{'id':1,'short_name':'ARS'}], 'events':[{'id':3,'is_current':True}]}
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as root:
            os.chdir(root)
            try:
                snapshots = pathlib.Path(root) / 'snapshots'
                snapshots.mkdir()
                with patch.object(run.diff, 'SNAP_DIR', snapshots), patch.object(run, 'now', return_value=dt.datetime(2026,9,12,23,5,tzinfo=dt.timezone.utc)), patch.object(run.requests, 'get', side_effect=AssertionError('Unexpected network request')):
                    state = run.build_state('2026-09-13', data, {'1':51,'2':49}, {'1':50,'2':50})
                self.assertTrue((snapshots / '2026-09-13.json').exists())
                self.assertEqual(len(state['messages']), 3)
                self.assertIn('13-09-2026', state['messages'][1]['text'])
                self.assertEqual(state['messages'][1]['group'], 'fallers')
                self.assertIn('£4.9m', state['messages'][1]['text'])
                self.assertIn('£5.1m', state['messages'][2]['text'])
            finally:
                os.chdir(original)


if __name__ == '__main__':
    unittest.main()
