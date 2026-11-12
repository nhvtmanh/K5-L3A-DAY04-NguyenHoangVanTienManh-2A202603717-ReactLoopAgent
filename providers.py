"""Các provider cùng một interface. Không chứa agent loop."""
import json
import os
import urllib.error
import urllib.request
from request_policy import request, settings

CONFIG = {
    'openai': ('OPENAI_API_KEY', 'https://api.openai.com/v1/chat/completions'),
    'gemini': ('GEMINI_API_KEY', 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'),
    'openrouter': ('OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1/chat/completions'),
    'anthropic': ('ANTHROPIC_API_KEY', 'https://api.anthropic.com/v1/messages'),
    'groq': ('GROQ_API_KEY', 'https://api.groq.com/openai/v1/chat/completions'),
}


def post_json(url, headers, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        'Content-Type': 'application/json', 'User-Agent': 'StatusAgentLab/1.0', **headers,
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Chỉ nhận diện các mã đã biết; không ghi body/message của provider.
        provider_code = None
        if exc.code == 403 and url == CONFIG['groq'][1]:
            try:
                body = exc.read(8192)
                if body.strip().lower() == b'error code: 1010':
                    provider_code = 'client_signature_blocked'
                else:
                    data = json.loads(body)
                    detail = data.get('error') if isinstance(data, dict) else None
                    code = detail.get('code') if isinstance(detail, dict) else None
                    if code in ('model_permission_blocked_org', 'model_permission_blocked_project'):
                        provider_code = code
            except (ValueError, OSError):
                pass
        failure = RuntimeError(f'Provider trả HTTP {exc.code}; kiểm tra model, key và quyền truy cập.')
        if provider_code:
            failure.provider_code = provider_code
        raise failure from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'Không kết nối được provider: {exc.reason}') from exc


class Provider:
    """complete(messages, schemas) -> {message, calls, finish, usage}.

    messages: system/user/assistant/tool theo format Chat Completions. message trả về
    cần được append nguyên vẹn; tool result có role, tool_call_id, content (JSON string).
    """
    def __init__(self, name, model, json_mode=False):
        if name not in CONFIG:
            raise ValueError(f'Provider không hợp lệ: {name}')
        if not model:
            raise ValueError('Cần đặt MODEL là model có hỗ trợ tool calling.')
        self.name, self.model = name, model
        key_name, self.url = CONFIG[name]
        self.key = os.getenv(key_name)
        if not self.key:
            raise ValueError(f'Thiếu {key_name}; xem README.md.')
        self.policy = settings()
        self.json_mode = json_mode
        self.transport_trace = []

    def _post(self, headers, payload):
        return request(lambda: post_json(self.url, headers, payload),
                       (self.name, self.model), self.policy, self.transport_trace)

    def complete(self, messages, schemas):
        self.transport_trace = []
        if self.name == 'anthropic':
            return self._anthropic(messages, schemas)
        payload = {'model': self.model, 'messages': messages}
        if schemas:
            payload['tools'] = schemas
        if self.json_mode:
            payload['response_format'] = {'type': 'json_object'}
        if self.name == 'groq':
            cap = os.getenv('GROQ_MAX_COMPLETION_TOKENS')
            if cap:
                if int(cap) < 1:
                    raise ValueError('GROQ_MAX_COMPLETION_TOKENS phải >= 1.')
                payload['max_completion_tokens'] = int(cap)
            effort = os.getenv('GROQ_REASONING_EFFORT')
            if effort and self.model in ('openai/gpt-oss-20b', 'openai/gpt-oss-120b'):
                if effort not in ('low', 'medium', 'high'):
                    raise ValueError('GROQ_REASONING_EFFORT phải là low, medium hoặc high.')
                payload['reasoning_effort'] = effort
        data = self._post({'Authorization': f'Bearer {self.key}'}, payload)
        choice = data['choices'][0]
        message = choice['message']
        calls = [{'id': c['id'], 'name': c['function']['name'], 'arguments': c['function']['arguments']} for c in message.get('tool_calls', [])]
        return {'message': message, 'calls': calls, 'finish': choice.get('finish_reason'),
                'usage': data.get('usage', {}), 'transport': list(self.transport_trace)}

    def _anthropic(self, messages, schemas):
        system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
        native = []
        for m in messages:
            if m['role'] == 'system':
                continue
            if m['role'] == 'assistant':
                blocks = []
                if m.get('content'):
                    blocks.append({'type': 'text', 'text': m['content']})
                for call in m.get('tool_calls', []):
                    blocks.append({'type': 'tool_use', 'id': call['id'], 'name': call['function']['name'], 'input': json.loads(call['function']['arguments'])})
                native.append({'role': 'assistant', 'content': blocks})
            elif m['role'] == 'tool':
                block = {'type': 'tool_result', 'tool_use_id': m['tool_call_id'], 'content': m['content']}
                if native and native[-1]['role'] == 'user' and isinstance(native[-1]['content'], list):
                    native[-1]['content'].append(block)
                else:
                    native.append({'role': 'user', 'content': [block]})
            else:
                native.append({'role': 'user', 'content': m['content']})
        tools = [{'name': x['function']['name'], 'description': x['function']['description'], 'input_schema': x['function']['parameters']} for x in schemas]
        payload = {'model': self.model, 'max_tokens': 1024, 'system': system, 'messages': native}
        if tools:
            payload['tools'] = tools
        data = self._post({'x-api-key': self.key, 'anthropic-version': '2023-06-01'}, payload)
        blocks = data['content']
        calls = [{'id': b['id'], 'name': b['name'], 'arguments': json.dumps(b['input'], ensure_ascii=False)} for b in blocks if b['type'] == 'tool_use']
        text = '\n'.join(b['text'] for b in blocks if b['type'] == 'text')
        message = {'role': 'assistant', 'content': text or None}
        if calls:
            message['tool_calls'] = [{'id': c['id'], 'type': 'function', 'function': {'name': c['name'], 'arguments': c['arguments']}} for c in calls]
        reason = {'end_turn': 'stop', 'tool_use': 'tool_calls', 'max_tokens': 'length'}.get(data.get('stop_reason'), data.get('stop_reason'))
        return {'message': message, 'calls': calls, 'finish': reason, 'usage': data.get('usage', {}),
                'transport': list(self.transport_trace)}
