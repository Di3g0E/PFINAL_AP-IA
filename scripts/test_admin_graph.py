import json
import requests

BASE = 'http://127.0.0.1:8000'
ADMIN_EMAIL = 'admin@pfinal.local'
ADMIN_PASSWORD = 'aKmUIgija!G0-kHs0c.'

print('POST /auth/login-admin')
resp = requests.post(f'{BASE}/auth/login-admin', json={'email': ADMIN_EMAIL, 'passphrase': ADMIN_PASSWORD})
print('status', resp.status_code)
print(resp.text)
if resp.status_code != 200:
    raise SystemExit('Login failed')

token = resp.json()['access_token']
print('\nGET /admin/langfuse/graph')
resp = requests.get(f'{BASE}/admin/langfuse/graph', headers={'Authorization': f'Bearer {token}'})
print('status', resp.status_code)
print(resp.text)
if resp.status_code != 200:
    raise SystemExit('Graph endpoint failed')

with open('artifacts/admin_graph_test.json', 'w', encoding='utf-8') as f:
    json.dump(resp.json(), f, indent=2, ensure_ascii=False)
print('\nWrote artifacts/admin_graph_test.json')
