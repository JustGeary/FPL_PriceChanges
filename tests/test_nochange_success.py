import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.github/scripts'))
import reliable_run as run

class NoChangeTests(unittest.TestCase):
    def exercise(self, state=None, check=False, observed=({}, {'1': 50})):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = root / 'delivery'
            ledger.mkdir()
            snapshots = root / 'snapshots'
            snapshots.mkdir()
            (snapshots / '2026-10-01.json').write_text('{"1": 50}')
            if state is not None:
                (ledger / '2026-10-02.json').write_text(json.dumps(state))
            with patch.object(run, 'STATE_DIR', ledger), patch.object(run.diff, 'SNAP_DIR', snapshots), patch.object(run, 'now', return_value=dt.datetime(2026, 10, 2, 5, tzinfo=dt.timezone.utc)), patch.object(sys, 'argv', ['bot', '--check'] if check else ['bot']), patch.object(run, 'observe', return_value=observed) as observe, patch.object(run, 'persist') as save, patch.object(run, 'deliver') as deliver, patch.object(run, 'build_state', return_value={'messages': []}):
                run.main()
                return observe.call_count, save.call_args, deliver.call_count

    def test_unchanged_run_succeeds_without_posting(self):
        calls, saved, posts = self.exercise()
        self.assertEqual(calls, 1)
        self.assertEqual(saved.args[1]['status'], 'no_change_unconfirmed')
        self.assertEqual(posts, 0)

    def test_check_accepts_empty_nochange_ledger(self):
        self.assertEqual(self.exercise({'status': 'no_change_unconfirmed', 'messages': []}, check=True)[0], 0)

    def test_fallback_rechecks_and_delivers_later_changes(self):
        calls, _, posts = self.exercise({'status': 'no_change_unconfirmed', 'messages': []}, observed=({}, {'1': 51}))
        self.assertEqual((calls, posts), (1, 1))

    def test_real_errors_still_fail(self):
        with self.assertRaises(RuntimeError):
            self.exercise(observed=(None, None))
        for state in [None, {'status': 'needs_attention', 'messages': []}, {'status': 'no_change_unconfirmed', 'messages': [{'channel': 'x', 'status': 'uncertain'}]}]:
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                self.exercise(state, check=True)
