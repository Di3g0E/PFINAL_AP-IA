import json
data = json.load(open('docs/results/p5_baseline.json', encoding='utf-8'))
for row in data['per_sample']:
    if row.get('accept') and row.get('kind') == 'spoof_screen':
        print(f"SPOOF_SCREEN ACEPTADO: {row['path']} - Sim: {row['similarity']:.3f}, Liveness: {row['liveness']:.3f}, Antispoof: {row['antispoof']:.3f}")
    if row.get('accept') and row.get('kind') == 'real' and row.get('user_label') == '0':
        print(f"OTRA PERSONA ACEPTADA: {row['path']} - Sim: {row['similarity']:.3f}")
