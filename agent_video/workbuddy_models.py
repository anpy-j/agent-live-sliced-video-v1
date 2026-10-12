"""Read the active WorkBuddy model directory and align child CLI configuration.

Only model definitions and the CLI model allowlist cross the boundary. Auth,
endpoints, commands, and other client settings never enter our generated file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from .app_runtime import user_data_dir


def _read(path: Path) -> dict:
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def desktop_model_context() -> tuple[dict, str]:
    # Respect CLI configuration explicitly supplied by the operator.
    overrides = {key: os.environ[key] for key in ('ACC_PRODUCT_CONFIG_PATH', 'ACC_PRODUCT_CONFIG_V3',
                 'ACC_PRODUCT_CONFIG_V2', 'ACC_PRODUCT_CONFIG') if os.environ.get(key)}
    if overrides:
        path = overrides.get('ACC_PRODUCT_CONFIG_PATH')
        if path:
            try: overrides['file-stamp'] = str(Path(path).stat().st_mtime_ns)
            except OSError: pass
        return {}, hashlib.sha256(json.dumps(overrides, sort_keys=True).encode()).hexdigest()
    root = Path(os.environ.get('WORKBUDDY_CONFIG_DIR') or Path.home() / '.workbuddy').expanduser()
    primary = _read(root / 'storage/skeleton/account-snapshot.json').get('primary') or {}
    uid = str(primary.get('uid') or '') if isinstance(primary, dict) else ''
    if uid and not re.fullmatch(r'[A-Za-z0-9_.-]+', uid):
        return {}, 'invalid-account-identifier'
    folders = [p for p in (root / 'storage').glob('user-*') if p.is_dir()]
    if uid:
        personal = root / 'storage' / f'user-{uid}-personal'
        base = root / 'storage' / f'user-{uid}'
        if primary.get('type') == 'personal' and personal.is_dir():
            folders = [personal]
        else:
            folders = [base] if base.is_dir() else []
    elif len(folders) != 1:
        # Do not merge another account's catalog into the active account.
        return {}, 'no-unambiguous-client-account'
    candidates = [p for folder in folders for p in folder.glob('scoped/*/wb-product-config.json')]
    def modified(path):
        try: return path.stat().st_mtime_ns
        except OSError: return 0
    candidates.sort(key=modified, reverse=True)
    for path in candidates:
        snapshot = _read(path).get('product-config-snapshot') or {}
        data = snapshot.get('data') if isinstance(snapshot, dict) else None
        if not isinstance(data, dict):
            continue
        agents = data.get('agents')
        if not isinstance(agents, list):
            continue
        agent = next((a for a in agents if isinstance(a, dict) and a.get('name') == 'cli'), None)
        if not agent or not isinstance(agent.get('models'), list):
            continue
        allowed = [m for m in agent['models'] if isinstance(m, str) and m.strip()]
        rows = data.get('models')
        if not isinstance(rows, list):
            continue
        # Known model fields, never copy arbitrary nested provider/auth settings.
        fields = {'id', 'name', 'vendor', 'maxOutputTokens', 'maxInputTokens',
                  'supportsToolCall', 'supportsImages', 'maxAllowedSize',
                  'supportsReasoning', 'onlyReasoning', 'temperature', 'reasoning',
                  'isDefault', 'relatedModels'}
        models = []
        seen = set()
        for item in rows:
            if not isinstance(item, dict) or item.get('supportsToolCall') is not True:
                continue
            model_id = item.get('id')
            if not isinstance(model_id, str) or model_id not in allowed or model_id in seen:
                continue
            seen.add(model_id)
            models.append({key: value for key, value in item.items() if key in fields})
        if not models:
            continue
        by_id = {m['id']: m for m in models}
        ordered = [model_id for model_id in allowed if model_id in by_id]
        cli_agent = {'name': 'cli', 'models': ordered}
        # Tags select the default CLI agent; omitting them empties --help models.
        if isinstance(agent.get('tags'), list):
            cli_agent['tags'] = [tag for tag in agent['tags'] if isinstance(tag, str)]
        # Keep the application's existing generic auto choice usable in CLI.
        automatic = {'id': 'auto', 'name': '自动选择', 'supportsToolCall': True}
        cli_agent['models'] = ['auto', *[mid for mid in ordered if mid != 'auto']]
        overlay = {'models': [automatic, *[by_id[mid] for mid in ordered if mid != 'auto']],
                   'agents': [cli_agent]}
        fingerprint = hashlib.sha256(json.dumps([str(path), overlay], sort_keys=True,
                                                ensure_ascii=False).encode()).hexdigest()
        return overlay, fingerprint
    return {}, 'no-client-model-snapshot'


def model_environment() -> dict[str, str]:
    overlay, fingerprint = desktop_model_context()
    if not overlay:
        return {}
    folder = user_data_dir() / 'cache' / 'workbuddy-models'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = folder / f'{fingerprint}.json'
    if not output.is_file():
        descriptor, temporary = tempfile.mkstemp(prefix='models-', suffix='.json', dir=folder)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(overlay, stream, ensure_ascii=False)
            os.replace(temporary, output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {'ACC_PRODUCT_CONFIG_PATH': str(output)}
