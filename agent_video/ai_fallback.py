"""Ordered, explicit model failover without changing process-wide provider settings."""
from __future__ import annotations

import json
import os
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar

PROVIDERS = {'api', 'codex', 'workbuddy', 'opencode', 'antigravity'}
_reporter = None
_response_check = ContextVar('ai_response_check', default=None)


@contextmanager
def response_check(validator):
    """Keep stage-specific ID/ordering checks inside failover, isolated per S3 thread."""
    token = _response_check.set(validator)
    try:
        yield
    finally:
        _response_check.reset(token)


def validate_stage_response(data):
    validator = _response_check.get()
    if validator:
        validator(data)


def normalize_fallbacks(value):
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError('备用模型必须是列表，最多添加 20 项')
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'provider', 'model'}:
            raise ValueError('备用模型必须包含提供方和模型')
        provider, model = item['provider'], item['model']
        if not isinstance(provider, str) or provider not in PROVIDERS or not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError('备用模型的提供方或模型无效')
        entry = {'provider': provider, 'model': model.strip()}
        if provider == 'api' and entry['model'] == 'auto':
            raise ValueError('API 备用模型需要填写具体模型名称')
        if entry in result:
            raise ValueError('备用模型列表不能包含重复项')
        result.append(entry)
    return result


def configured_fallbacks():
    return normalize_fallbacks(json.loads(os.environ.get('PIPELINE_AI_FALLBACKS') or '[]'))


def set_reporter(reporter):
    global _reporter
    _reporter = reporter


def validate_response(value, schema, path='$'):
    """Validate the JSON Schema subset used by S3/S4 before accepting a provider."""
    kind = schema.get('type')
    types = {'object': dict, 'array': list, 'string': str, 'integer': int,
             'boolean': bool, 'number': (int, float)}
    if kind in types and (not isinstance(value, types[kind])
                          or kind in {'integer', 'number'} and isinstance(value, bool)):
        raise ValueError(f'{path} 返回类型不符合模型契约')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path} 返回枚举不符合模型契约')
    if kind == 'object':
        properties = schema.get('properties', {})
        if any(key not in value for key in schema.get('required', [])):
            raise ValueError(f'{path} 返回缺少必需字段')
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise ValueError(f'{path} 返回包含多余字段')
        for key, spec in properties.items():
            if key in value:
                validate_response(value[key], spec, f'{path}.{key}')
    elif kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', float('inf')):
            raise ValueError(f'{path} 返回数组长度不符合模型契约')
        for item in value:
            validate_response(item, schema.get('items', {}), path + '[]')
    elif kind == 'string':
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', float('inf')):
            raise ValueError(f'{path} 返回文本长度不符合模型契约')
    elif kind in {'integer', 'number'}:
        if not schema.get('minimum', -float('inf')) <= value <= schema.get('maximum', float('inf')):
            raise ValueError(f'{path} 返回数值不符合模型契约')


def run_with_fallbacks(primary, invoke, error_type, *, fatal_errors=()):
    """Try each configured model once per request; preserve the original error if disabled."""
    backups = configured_fallbacks()
    candidates = [primary]
    for item in backups:
        candidate = {'engine': 'llm', **item}
        if candidate not in candidates:
            candidates.append(candidate)
    failures = []
    for index, candidate in enumerate(candidates):
        try:
            result = invoke(candidate)
        except fatal_errors:
            raise
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
            if len(candidates) == 1:
                raise
            failures.append(f"{candidate['provider']} / {candidate['model']}: {exc}")
            if index + 1 < len(candidates) and _reporter:
                _reporter({'failed': candidate, 'next': candidates[index + 1],
                           'error': str(exc)[:1000], 'status': 'switching'})
            if index + 1 == len(candidates):
                error = error_type('主模型及备用模型均调用失败：' + '；'.join(failures))
                error.fallback_exhausted = True
                raise error from exc
        else:
            if index and _reporter:
                _reporter({'selected': candidate, 'status': 'recovered'})
            return result
