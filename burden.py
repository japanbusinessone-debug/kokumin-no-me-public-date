"""MOF national burden series: official PDF -> validated SQLite + public JSON."""
import datetime as dt
import hashlib
import io
import json
import pathlib
import re
import sqlite3
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
import urllib.robotparser
from pypdf import PdfReader

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / 'public'
INDEX = 'https://www.mof.go.jp/policy/budget/topics/futanritsu/index.htm'
TERMS = 'https://www.mof.go.jp/about_mof/notice/index.html'
def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None

def fetch(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme != 'https' or u.hostname != 'www.mof.go.jp' or u.username or u.password:
        raise ValueError('unapproved_source')
    for attempt in range(3):
        time.sleep(3)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'KokuminNoMe-PublicData/1.0'})
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
                value = response.read(3_000_001)
                if len(value) > 3_000_000:
                    raise ValueError('response_too_large')
                return value
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500 or attempt == 2:
                raise RuntimeError('official_HTTP_' + str(e.code)) from None
            retry = e.headers.get('Retry-After', '')
            delay = int(retry) if retry.isdigit() else 3 * 2 ** attempt
            if delay > 30:
                raise RuntimeError('rate_limit_deferred') from None
            time.sleep(max(3, delay))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError('official_network_error') from None
            time.sleep(3 * 2 ** attempt)

def discover(index, page=None):
    if page is None:
        candidates = []
        for href, label in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', index, re.S | re.I):
            url = urllib.parse.urljoin(INDEX, href)
            if '国民負担率' in re.sub('<[^>]+>', '', label) and re.fullmatch(r'https://www\.mof\.go\.jp/policy/budget/topics/futanritsu/\d{8}\.html', url):
                candidates.append(url)
        if not candidates:
            raise ValueError('release_link_not_found')
        return max(candidates)
    for href, label in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', index, re.S | re.I):
        if '国民負担率の推移' in re.sub('<[^>]+>', '', label):
            url = urllib.parse.urljoin(page, href)
            if re.fullmatch(r'https://www\.mof\.go\.jp/policy/budget/topics/futanritsu/[a-zA-Z0-9_-]+\.pdf', url):
                return url
    raise ValueError('series_pdf_not_found')

def parse(text, release, pdf):
    text = unicodedata.normalize('NFKC', text)
    status = re.search(r'令和(\d+)年度までは実績[、,]\s*令和(\d+)年度は実績見込み[、,]\s*令和(\d+)年度は見通し', text)
    if not status or '対国民所得比' not in text:
        raise ValueError('unrecognized_status_or_denominator')
    actual, provisional, forecast = [2018 + int(y) for y in status.groups()]
    if (provisional, forecast) != (actual + 1, actual + 2):
        raise ValueError('inconsistent_status_years')
    date = re.search(r'/(\d{4})(\d{2})(\d{2})\.html$', release)
    published = dt.date(*map(int, date.groups())).isoformat()
    records = []
    pattern = r'\b((?:19|20)\d{2})\s+((?:\d+\.\d+\s+){10}\d+\.\d+)'
    for year, cols in re.findall(pattern, text):
        v = list(map(float, cols.split()))
        # NI percentages: tax, social, total, deficit, potential. Keep published rounding.
        tax, social, national, deficit, potential = v[3:8]
        if not all(0 <= x <= 100 for x in [tax, social, national, deficit, potential]) or abs(tax + social - national) > .151 or abs(national + deficit - potential) > .151 or v[8] <= 0:
            raise ValueError('series_consistency_error')
        y = int(year)
        records.append({'id': str(y), 'fiscal_year': y, 'name': str(y) + '年度 国民負担率',
                        'tax': tax, 'social': social, 'national': national, 'deficit': deficit,
                        'potential': potential, 'national_income_trillion_yen': v[8],
                        'status': '実績' if y <= actual else '実績見込み' if y == provisional else '見通し' if y == forecast else '未確認',
                        'unit': '%', 'denominator': '国民所得（NI）', 'published_at': published,
                        'source_url': pdf, 'release_url': release, 'checked_at': now()})
    years = [r['fiscal_year'] for r in records]
    if len(years) < 10 or years != list(range(min(years), forecast + 1)) or len(set(years)) != len(years):
        raise ValueError('series_years_missing_or_duplicated')
    return records

def main():
    OUT.mkdir(exist_ok=True)
    started = now()
    try:
        robots = urllib.robotparser.RobotFileParser()
        try:
            robots.parse(fetch('https://www.mof.go.jp/robots.txt').decode('utf-8').splitlines())
        except RuntimeError as e:
            if str(e) != 'official_HTTP_404':
                raise
            robots.parse([])
        if not robots.can_fetch('KokuminNoMe-PublicData', INDEX):
            raise ValueError('robots_disallow')
        release = discover(fetch(INDEX).decode('utf-8'))
        if not robots.can_fetch('KokuminNoMe-PublicData', release):
            raise ValueError('robots_disallow')
        pdf = discover(fetch(release).decode('utf-8'), release)
        if not robots.can_fetch('KokuminNoMe-PublicData', pdf):
            raise ValueError('robots_disallow')
        raw = fetch(pdf)
        text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(raw)).pages)
        records = parse(text, release, pdf)
        db = sqlite3.connect(OUT / 'burden.sqlite')
        db.executescript('CREATE TABLE IF NOT EXISTS records(year INTEGER PRIMARY KEY,payload TEXT,hash TEXT,checked_at TEXT); CREATE TABLE IF NOT EXISTS versions(year INTEGER,hash TEXT,payload TEXT,observed_at TEXT,UNIQUE(year,hash));')
        changed = 0
        with db:
            for r in records:
                stable = json.dumps({k:v for k,v in r.items() if k != 'checked_at'}, sort_keys=True, ensure_ascii=False)
                digest = hashlib.sha256(stable.encode()).hexdigest()
                old = db.execute('SELECT hash FROM records WHERE year=?', (r['fiscal_year'],)).fetchone()
                if not old or old[0] != digest:
                    changed += 1
                    db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?)', (r['fiscal_year'], digest, stable, now()))
                db.execute('INSERT INTO records VALUES(?,?,?,?) ON CONFLICT(year) DO UPDATE SET payload=excluded.payload,hash=excluded.hash,checked_at=excluded.checked_at', (r['fiscal_year'], json.dumps(r, ensure_ascii=False), digest, r['checked_at']))
        db.close()
        document = {'schema_version': 1, 'generated_at': now(), 'published_at': records[0]['published_at'],
                    'source_url': pdf, 'release_url': release, 'terms_url': TERMS,
                    'credit': '出典：財務省「国民負担率（対国民所得比）の推移」を加工して作成。',
                    'pdf_sha256': hashlib.sha256(raw).hexdigest(), 'records': records,
                    'notes': ['公表値は小数第1位に丸められているため、内訳の合計と国民負担率等が一致しない場合があります。', '国税には特別会計等を含みます。一般会計の税収だけではありません。', '財政赤字は国・地方の財政収支の赤字で、一時的な特殊要因を除きます。', 'SNA基準の改定等があるため、単純な年度比較には注意が必要です。', '潜在的国民負担率の差分は、個人の将来負担額を示すものではありません。']}
        tmp = OUT / 'burden.json.tmp'
        tmp.write_text(json.dumps(document, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        tmp.replace(OUT / 'burden.json')
        result = {'status': 'succeeded', 'started_at': started, 'checked_at': now(), 'fetched': len(records), 'changed': changed, 'error': None}
    except Exception as e:
        result = {'status': 'failed', 'started_at': started, 'checked_at': now(), 'error': type(e).__name__ + ': ' + str(e)[:100]}
    (OUT / 'burden-status.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
    if result['status'] == 'failed' and not (OUT / 'burden.json').exists():
        raise SystemExit(1)

if __name__ == '__main__':
    main()
