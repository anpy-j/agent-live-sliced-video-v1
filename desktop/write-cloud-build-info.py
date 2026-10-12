"""Record only explicitly named current-version deliverables and their checksums."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

root = Path(__file__).resolve().parent.parent
version = json.loads((root/'package.json').read_text(encoding='utf-8'))['version']
output = root/'desktop/dist'
files = [output/f'LiveCut-Setup-{version}-x64.exe', output/f'qianchuan-livecut-server-{version}.zip']
apk = output/f'LiveCut-{version}-android-debug.apk'
if apk.is_file():
    files.append(apk)
for file in files:
    if not file.is_file():
        raise SystemExit(f'Missing deliverable: {file}')
records = []
for file in files:
    with file.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    records.append({'name':file.name,'bytes':file.stat().st_size,'sha256':digest})
(output/f'SHA256SUMS-{version}.txt').write_text(''.join(f'{r["sha256"]}  {r["name"]}\n' for r in records),encoding='utf-8')
info = {'version':version,'built_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
        'source_base_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),
        'source_state':'uncommitted working tree candidate, includes existing local changes',
        'artifacts':records,'windows':'packaged startup and cloud catalog verified; installer not installed',
        'android':'debug build; physical device and real Qiniu tests pending' if apk.is_file() else 'native project prepared; APK not built',
        'mac':'build configuration retained; no macOS build host available',
        'deployment':'source bundle prepared; server deployment and real Qiniu tests pending'}
(output/f'BUILD-INFO-{version}.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(records,ensure_ascii=False,indent=2))
