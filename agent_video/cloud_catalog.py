"""Read-only final-output adapters; never initialize another workflow's database."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def catalog(root):
    root = Path(root)
    items, errors = [], []

    def add(workflow, job, path, completed=None, product=''):
        file = Path(path)
        if file.suffix.lower() not in {'.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v'} or not file.is_file():
            return
        file = file.resolve()
        stat = file.stat()
        stamp = completed or datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
        ident = hashlib.sha256(f'{workflow}:{job["id"]}:{file}'.encode()).hexdigest()
        items.append({'id': ident, 'workflow': workflow, 'local_id': job['id'],
                      'title': job.get('title') or file.stem, 'filename': file.name, 'size': stat.st_size,
                      'product_name': product, 'completed_at': stamp, 'time_source': 'job' if completed else 'file',
                      'path': str(file), 'content_url': f'/api/cloud/catalog/{ident}/content'})

    sources = [('legacy','data/agent.db'), ('viral-v2','data/viral_v2/viral_v2.db'),
               ('smart-v3','data/smart_v3/smart_v3.db'), ('editor','data/editor/editor.db'),
               ('postprocess','data/postprocess/editor.db')]
    for workflow, relative in sources:
        path = root / relative
        if not path.is_file():
            continue
        con = None
        try:
            con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
            con.row_factory = sqlite3.Row
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if workflow == 'smart-v3':
                for r in con.execute('SELECT body FROM smart_jobs'):
                    job = json.loads(r[0])
                    if job.get('status') == 'completed':
                        add(workflow, job, root / 'data/smart_v3/jobs' / job['id'] / 'film.mp4',
                            job.get('updated_at'), job.get('input', {}).get('product_name',''))
            elif workflow in {'editor','postprocess'}:
                for r in con.execute('SELECT data FROM exports'):
                    job = json.loads(r[0])
                    if job.get('status') == 'completed' and job.get('output'):
                        add(workflow, job, job['output'])
            else:
                table = 'jobs' if workflow == 'legacy' else 'viral_jobs'
                artifact_table = 'artifacts' if workflow == 'legacy' else 'viral_job_outputs'
                if table not in tables or artifact_table not in tables:
                    continue
                for raw in con.execute(f"SELECT * FROM {table} WHERE status='completed'"):
                    job = dict(raw)
                    for art in con.execute(f'SELECT * FROM {artifact_table} WHERE job_id=?', (job['id'],)):
                        artifact = dict(art)
                        if artifact.get('kind') == 'video' or artifact.get('stage_id') == 'render':
                            add(workflow, job, artifact.get('path',''), job.get('finished_at'), job.get('product_name',''))
        except (sqlite3.Error, ValueError, KeyError, OSError) as exc:
            errors.append({'workflow': workflow, 'error': str(exc)})
        finally:
            if con:
                con.close()
    return {'items': sorted(items, key=lambda x: x['completed_at'], reverse=True), 'errors': errors}


def output_file(root, ident):
    for item in catalog(root)['items']:
        if item['id'] == ident:
            return Path(item['path'])
    raise KeyError('成片不存在或已移除')
