import importlib.util
import pathlib
import sqlite3
import unittest
import json
spec = importlib.util.spec_from_file_location('collect', pathlib.Path(__file__).parents[1] / 'collect.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

class CollectorTest(unittest.TestCase):
    def test_real_snapshot_attribution_and_coverage(self):
        d = json.loads((c.OUT / 'data.json').read_text(encoding='utf-8'))
        self.assertEqual(len(d['tables']['laws']), 5)
        self.assertEqual(len({r['region_code'] for r in d['tables']['statistics']}), 7)
        self.assertTrue(all(r['unit'] == '人' for r in d['tables']['statistics']))
        self.assertEqual(d['estat_standard']['status'], 'not_connected')
        db = sqlite3.connect(c.OUT / 'records.sqlite')
        self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(db.execute('SELECT count(*) FROM records').fetchone()[0], sum(map(len, d['tables'].values())))
        self.assertTrue(all(s['status'] in ['active', 'failed'] for s in d['sources']))
        db.close()

    def test_repeat_does_not_create_history_and_changed_record_does(self):
        db = sqlite3.connect(':memory:')
        db.executescript('CREATE TABLE records(dataset,id,payload,content_hash,checked_at,first_seen,PRIMARY KEY(dataset,id)); CREATE TABLE versions(dataset,id,content_hash,payload,observed_at,UNIQUE(dataset,id,content_hash));')
        r = {'id': 'TEST_ONLY', 'name': 'TEST_ONLY', 'checked_at': c.now()}
        self.assertEqual(c.save(db, 'test', [r]), 1)
        r['checked_at'] = c.now()
        self.assertEqual(c.save(db, 'test', [r]), 0)
        r['name'] = 'TEST_ONLY_CHANGED'
        self.assertEqual(c.save(db, 'test', [r]), 1)
        self.assertEqual(db.execute('SELECT count(*) FROM records').fetchone()[0], 1)
        self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 2)
        with self.assertRaises(ValueError):
            c.save(db, 'test', [r, r])
        db.close()

    def test_invalid_api_schemas_fail_closed(self):
        with self.assertRaises(ValueError):
            c.parse_laws({}, '教育基本法', 'https://laws.e-gov.go.jp/')
        with self.assertRaises(ValueError):
            c.parse_meetings({'unexpected': []})
        self.assertEqual(c.parse_meetings({'numberOfRecords': 0}), [])

if __name__ == '__main__':
    unittest.main()
