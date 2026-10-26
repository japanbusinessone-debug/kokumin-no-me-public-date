import unittest
import json
import pathlib
import importlib.util
from pypdf import PdfReader
spec=importlib.util.spec_from_file_location('burden',pathlib.Path(__file__).parents[1]/'burden.py')
b=importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

class BurdenTest(unittest.TestCase):
    def test_actual_official_series_and_vintage(self):
        d=json.loads((b.OUT/'burden.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(len(d['records']),57)
        self.assertTrue(all(r['denominator']=='国民所得（NI）' for r in d['records']))
        fixture=(pathlib.Path(__file__).parent/'fixtures/mof-202603-series.txt').read_text(encoding='utf-8')
        d={'records':b.parse(fixture,'https://www.mof.go.jp/policy/budget/topics/futanritsu/20260305.html','https://www.mof.go.jp/policy/budget/topics/futanritsu/sy202603a.pdf'),'published_at':'2026-03-05'}
        rows={r['fiscal_year']:r for r in d['records']}
        self.assertEqual(len(rows),12)
        self.assertEqual(rows[2026]['national'],45.7)
        self.assertEqual(rows[2026]['potential'],48.4)
        self.assertEqual(rows[2026]['tax'],28.0)
        self.assertEqual(rows[2026]['social'],17.6)
        self.assertEqual(rows[2026]['status'],'見通し')
        self.assertEqual(rows[2025]['status'],'実績見込み')
        self.assertEqual(rows[2024]['status'],'実績')
        self.assertEqual(d['published_at'],'2026-03-05')

    def test_bad_pdf_text_is_rejected_and_discovery_is_scoped(self):
        with self.assertRaises(ValueError):
            b.parse('2026 45.7 48.4','https://www.mof.go.jp/policy/budget/topics/futanritsu/20260305.html','x')
        with self.assertRaises(ValueError):
            b.discover('<a href="https://attacker.invalid/20260305.html">国民負担率</a>')
        self.assertEqual(b.discover('<a href="20260305.html">令和8年度の国民負担率</a>'),'https://www.mof.go.jp/policy/budget/topics/futanritsu/20260305.html')

if __name__=='__main__':unittest.main()
