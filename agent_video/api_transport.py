"""Direct OpenAI-compatible JSON transport shared by legacy and V3."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


def validate_base_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    if (parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('API 地址必须是完整的 HTTP/HTTPS 地址，不能包含凭据或查询参数')
    if parsed.scheme == 'http' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('远程 API 请使用 HTTPS；本机模型服务可以使用 HTTP')
    return url.strip().rstrip('/')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        # Never forward bearer credentials to a redirected host.
        return None


def json_completion(*, base_url: str, api_key: str, model: str, prompt: str,
                    timeout: int = 180) -> tuple[dict, dict]:
    if not api_key or not model or model == 'auto':
        raise ValueError('请先配置 API Key 和具体模型名称')
    base = validate_base_url(base_url)
    body = {'model': model, 'messages': [
        {'role': 'system', 'content': 'Return only one valid JSON object. Follow the supplied contract.'},
        {'role': 'user', 'content': prompt}], 'response_format': {'type': 'json_object'}}
    request = urllib.request.Request(base + '/chat/completions', data=json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            envelope = json.loads(response.read(8 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        # Provider error bodies may reflect secrets; expose status only.
        raise RuntimeError(f'模型服务返回 HTTP {exc.code}，请检查地址、模型、密钥或额度') from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError('无法连接模型服务或请求超时，请检查网络和服务地址') from None
    try:
        content = envelope['choices'][0]['message']['content']
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError()
        return data, envelope.get('usage') or {}
    except (KeyError, IndexError, TypeError, ValueError):
        raise RuntimeError('模型没有返回符合要求的 JSON 对象') from None
