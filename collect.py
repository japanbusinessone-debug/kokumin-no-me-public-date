"""Bounded public metadata collector. Python stdlib only; no accounts or API keys."""
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / 'public'
ALLOWED = {'laws.e-gov.go.jp', 'dashboard.e-stat.go.jp', 'kokkai.ndl.go.jp'}
CREDIT = 'このサービスは、統計ダッシュボードのAPI機能を使用していますが、サービスの内容は国によって保証されたものではありません。'

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def get_json(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme != 'https' or u.hostname not in ALLOWED or u.username or u.password:
        raise ValueError('unapproved_source')
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(3):
        time.sleep(3)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'KokuminNoMe-PublicData/1.0', 'Accept': 'application/json'})
            with opener.open(req, timeout=30) as response:
                raw = response.read(3_000_001)
                if len(raw) > 3_000_000:
                    raise ValueError('response_too_large')
                return json.loads(raw.decode('utf-8'))
        except urllib.error.HTTPError as error:
            if error.code != 429 and error.code < 500 or attempt == 2:
                raise RuntimeError('official_HTTP_' + str(error.code)) from None
            retry = error.headers.get('Retry-After', '')
            delay = int(retry) if retry.isdigit() else 3 * 2 ** attempt
            if delay > 30:
                raise RuntimeError('rate_limit_deferred') from None
            time.sleep(max(3, delay))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError('official_network_error') from None
            time.sleep(3 * 2 ** attempt)

def arr(v):
    return v if isinstance(v, list) else [] if v is None else [v]

def parse_laws(data, title, url):
    if not isinstance(data.get('laws'), list):
        raise ValueError('law_schema_changed')
    records = []
    for item in data['laws']:
        law, revision = item['law_info'], item['revision_info']
        if revision['law_title'] != title:
            continue
        law_id = law['law_id']
        if not re.fullmatch(r'[0-9A-Z_]+', law_id):
            raise ValueError('invalid_law_id')
        records.append({'id': law_id, 'name': title, 'law_id': law_id,
                        'law_number': law['law_num'], 'law_type': law['law_type'],
                        'revision_id': revision['law_revision_id'],
                        'promulgation_date': law['promulgation_date'],
                        'enforcement_date': revision.get('amendment_enforcement_date'),
                        'source_updated_at': revision.get('updated'),
                        'status': revision.get('current_revision_status'),
                        'repeal_status': revision.get('repeal_status'),
                        'source_url': 'https://laws.e-gov.go.jp/law/' + law_id,
                        'api_url': url, 'checked_at': now()})
    return records

def parse_statistics(data, url):
    root = data['GET_STATS']
    if str(root['RESULT']['status']) != '0':
        raise ValueError('statistics_API_error')
    table = root['STATISTICAL_DATA']
    classes = {x['@id']: {c['@code']: c.get('@name', c.get('$')) for c in arr(x['CLASS'])}
               for x in arr(table['CLASS_INF']['CLASS_OBJ'])}
    stat_names = {x['@code']: x['$'] for x in arr(table['TABLE_INF']['STAT_NAME'])}
    records = []
    for obj in arr(table['DATA_INF']['DATA_OBJ']):
        v = obj['VALUE']
        if not re.fullmatch(r'\d{4}CY00', v['@time']) or int(v['@time'][:4]) < 2015:
            continue
        unit = classes['unit'].get(v['@unit'])
        if not unit:
            raise ValueError('missing_unit')
        raw_value = v.get('$')
        value = raw_value if re.fullmatch(r'-?\d+(?:\.\d+)?', str(raw_value)) else None
        key = ':'.join(v[k] for k in ['@indicator', '@regionCode', '@time', '@cycle', '@isSeasonal', '@unit', '@stat'])
        records.append({'id': key, 'name': classes['indicator'][v['@indicator']],
                        'indicator': v['@indicator'], 'region_code': v['@regionCode'],
                        'region': classes['regionCode'][v['@regionCode']],
                        'period': classes['time'][v['@time']], 'period_code': v['@time'],
                        'cycle': classes['cycle'][v['@cycle']], 'unit': unit,
                        'value': value, 'raw_value': raw_value,
                        'seasonal_adjustment': classes['isSeasonal'][v['@isSeasonal']],
                        'provisional': classes.get('isProvisional', {}).get(v.get('@isProvisional'), '未確認'),
                        'statistic_name': stat_names.get(v['@stat']),
                        'notes': obj.get('CELL_ANNOTATIONS', {}).get('$'),
                        'source_url': url, 'checked_at': now()})
    if not records:
        raise ValueError('statistics_empty')
    return records

def parse_meetings(data):
    if 'meetingRecord' not in data and int(data.get('numberOfRecords', -1)) != 0:
        raise ValueError('meeting_schema_changed')
    records = []
    for m in arr(data.get('meetingRecord')):
        if not m['meetingURL'].startswith('https://kokkai.ndl.go.jp/') or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', m['date']):
            raise ValueError('invalid_meeting')
        records.append({'id': m['issueID'], 'name': m['nameOfHouse'] + ' ' + m['nameOfMeeting'],
                        'date': m['date'], 'house': m['nameOfHouse'], 'session': m['session'],
                        'issue': m['issue'], 'source_url': m['meetingURL'], 'checked_at': now()})
    return records

def collect_laws():
    result = []
    for title in ['日本国憲法', '公職選挙法', '地方自治法', '教育基本法', '財政法']:
        offset, found = 0, []
        for _ in range(3):
            url = 'https://laws.e-gov.go.jp/api/2/laws?' + urllib.parse.urlencode({'law_title': title, 'limit': 20, 'offset': offset, 'response_format': 'json'})
            data = get_json(url)
            found.extend(parse_laws(data, title, url))
            offset += data['count']
            if found or offset >= data['total_count']:
                break
        if not found:
            raise ValueError('law_not_found:' + title)
        result.extend(found)
    return result

def collect_statistics():
    result = []
    for rank, regions in [('2', '00000'), ('3', '13000,27000,14000,28000,12000,11000')]:
        url = 'https://dashboard.e-stat.go.jp/api/1.0/Json/getData?' + urllib.parse.urlencode({
            'Lang': 'JP', 'IndicatorCode': '0201010000000010000', 'Cycle': '3',
            'RegionalRank': rank, 'RegionCode': regions, 'IsSeasonalAdjustment': '1',
            'MetaGetFlg': 'Y', 'SectionHeaderFlg': '1'})
        result.extend(parse_statistics(get_json(url), url))
    return result

def collect_meetings():
    today = dt.datetime.now(dt.timezone.utc).date()
    query = {'from': str(today - dt.timedelta(days=60)), 'until': str(today),
             'recordPacking': 'json', 'maximumRecords': 10, 'startRecord': 1}
    result = []
    for _ in range(2):
        data = get_json('https://kokkai.ndl.go.jp/api/meeting_list?' + urllib.parse.urlencode(query))
        result.extend(parse_meetings(data))
        nxt = data.get('nextRecordPosition')
        if not nxt:
            break
        if int(nxt) <= query['startRecord']:
            raise ValueError('non_advancing_cursor')
        query['startRecord'] = int(nxt)
    return result

def stable(record):
    return json.dumps({k: v for k, v in record.items() if k != 'checked_at'}, ensure_ascii=False, sort_keys=True)

def save(db, dataset, records):
    seen, changed = set(), 0
    for record in records:
        if record['id'] in seen:
            raise ValueError('duplicate_source_id')
        seen.add(record['id'])
        content = stable(record)
        digest = hashlib.sha256(content.encode()).hexdigest()
        old = db.execute('SELECT content_hash FROM records WHERE dataset=? AND id=?', (dataset, record['id'])).fetchone()
        if not old or old[0] != digest:
            changed += 1
            db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?)', (dataset, record['id'], digest, content, now()))
        db.execute('INSERT INTO records VALUES(?,?,?,?,?,?) ON CONFLICT(dataset,id) DO UPDATE SET payload=excluded.payload,content_hash=excluded.content_hash,checked_at=excluded.checked_at',
                   (dataset, record['id'], json.dumps(record, ensure_ascii=False), digest, record['checked_at'], now()))
    return changed

def main():
    OUT.mkdir(exist_ok=True)
    db = sqlite3.connect(OUT / 'records.sqlite')
    db.executescript('''CREATE TABLE IF NOT EXISTS records(dataset TEXT,id TEXT,payload TEXT,content_hash TEXT,checked_at TEXT,first_seen TEXT,PRIMARY KEY(dataset,id));
    CREATE TABLE IF NOT EXISTS versions(dataset TEXT,id TEXT,content_hash TEXT,payload TEXT,observed_at TEXT,UNIQUE(dataset,id,content_hash));
    CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,source TEXT,started_at TEXT,ended_at TEXT,status TEXT,fetched INTEGER,changed INTEGER,error TEXT);
    CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,payload TEXT);''')
    jobs = [('egov', 'laws', collect_laws), ('estat_dashboard', 'statistics', collect_statistics), ('ndl', 'meetings', collect_meetings)]
    sources = {k: json.loads(v) for k, v in db.execute('SELECT id,payload FROM sources')}
    for source, dataset, fn in jobs:
        started, records, changed, error = now(), [], 0, None
        try:
            records = fn()
            with db:
                changed = save(db, dataset, records)
            sources[source] = {'id': source, 'status': 'active', 'last_success': now(), 'scope': {'egov': '主要5法令の現行改訂メタデータ。提出法案とは別。', 'estat_dashboard': '総人口の年次値。全国と東京・大阪・神奈川・兵庫・千葉・埼玉。2015年以降。', 'ndl': '直近60日を最大20会議ずつ確認。本文は再配布しない。'}[source]}
        except Exception as exc:
            error = type(exc).__name__ + ': ' + str(exc)[:120]
            sources[source] = {**sources.get(source, {'id': source}), 'status': 'failed', 'error': error}
        db.execute('INSERT INTO runs(source,started_at,ended_at,status,fetched,changed,error) VALUES(?,?,?,?,?,?,?)',
                   (source, started, now(), 'failed' if error else 'succeeded', len(records), changed, error))
        sources[source]['last_attempt'] = now()
        db.execute('INSERT INTO sources VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (source, json.dumps(sources[source], ensure_ascii=False)))
        db.commit()
        print(json.dumps({'source': source, 'fetched': len(records), 'changed': changed, 'error': error}, ensure_ascii=False))
    tables = {dataset: [json.loads(x[0]) for x in db.execute('SELECT payload FROM records WHERE dataset=? ORDER BY id', (dataset,))] for dataset in ['laws', 'statistics', 'meetings']}
    if not any(tables.values()):
        raise RuntimeError('no_valid_data_preserved')
    runs = [dict(zip(['source', 'started_at', 'ended_at', 'status', 'fetched', 'changed', 'error'], r)) for r in db.execute('SELECT source,started_at,ended_at,status,fetched,changed,error FROM runs ORDER BY id DESC LIMIT 20')]
    document = {'schema_version': 1, 'generated_at': now(), 'tables': tables, 'sources': list(sources.values()), 'runs': runs, 'credit': CREDIT, 'estat_standard': {'status': 'not_connected', 'reason': '標準APIのappIdは未発行。登録不要の統計ダッシュボードAPIを使用。'}}
    if sum(len(x) for x in tables.values()) > 3000:
        raise RuntimeError('record_limit_reached')
    temp = OUT / 'data.json.tmp'
    temp.write_text(json.dumps(document, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    os.replace(temp, OUT / 'data.json')
    db.close()

if __name__ == '__main__':
    main()
