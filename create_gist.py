"""创建 GitHub Gist 存储押注数据（无需账号，匿名公开）"""
import json, sqlite3, requests

conn = sqlite3.connect('data.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute('SELECT * FROM stats_cache ORDER BY id DESC LIMIT 1')
row = c.fetchone()
raw = json.loads(row['raw_stats'])
c.execute('SELECT nickname, prediction, created_at FROM bets ORDER BY id')
bets = [{'nickname': r[0], 'prediction': r[1], 'created_at': r[2] or ''} for r in c.fetchall()]
conn.close()

payload = {
    'stats': {
        'total_species': raw.get('total_species', 0),
        'new_species': None,
        'computed_at': row['computed_at'] or '',
    },
    'bets': bets,
    'trip_status': 'before'
}

gist_data = {
    'description': 'HemLeu wawushan bets data',
    'public': True,
    'files': {
        'data.json': {
            'content': json.dumps(payload, ensure_ascii=False, indent=2)
        }
    }
}

resp = requests.post(
    'https://api.github.com/gists',
    json=gist_data,
    headers={'Accept': 'application/vnd.github+json'},
    timeout=15
)
print('Status:', resp.status_code)
if resp.ok:
    d = resp.json()
    gist_id = d['id']
    raw_url = d['files']['data.json']['raw_url']
    print('Gist ID:', gist_id)
    print('Raw URL:', raw_url)
    # 保存 gist_id 到配置文件
    with open('gist_config.json', 'w') as f:
        json.dump({'gist_id': gist_id, 'raw_url': raw_url}, f)
    print('已保存到 gist_config.json')
else:
    print('Error:', resp.text[:300])
