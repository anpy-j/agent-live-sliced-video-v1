"""Create an explicit source deployment bundle; never include credentials or runtime DBs."""
import argparse
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

parser = argparse.ArgumentParser()
parser.add_argument('qianchuan_root', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
files = ['server/main.py','server/database.py','server/inspector.py','server/materials.py',
         'server/clipping.py','server/livecut.py','server/requirements.txt','server/Dockerfile',
         'server/docker-compose.yml','server/.dockerignore','server/.env.livecut.example',
         'server/deploy.sh','server/test_livecut.py','server/test_clipping.py','server/test_startup.py',
         'server/test_materials.py','LIVECUT_CLOUD.md']
for file in files:
    if not (args.qianchuan_root / file).is_file():
        raise SystemExit(f'Missing required deployment source: {file}')
args.output.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(args.output, 'w', ZIP_DEFLATED) as archive:
    for file in files:
        # Shell sources checked out on Windows must still execute on Linux.
        if file.endswith('.sh'):
            archive.writestr(file, (args.qianchuan_root / file).read_bytes().replace(b'\r\n', b'\n'))
        else:
            archive.write(args.qianchuan_root / file, file)
print(f'Deployment source archive: {args.output.resolve()} ({len(files)} files)')
