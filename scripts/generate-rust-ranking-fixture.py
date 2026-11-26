"""Capture the unmodified Python rank contract offline, with no database/provider access."""
import ast
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'apps/api'), str(ROOT / 'apps/api/tests')]
from paperpilot import discovery
from test_research_context import inputs

tree = ast.parse((ROOT / 'apps/api/paperpilot/discovery.py').read_text(encoding='utf-8'))
rank = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'rank')
prompt = next(n.value for n in ast.walk(rank) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith('Rank up to 20 supplied'))

def immutable(path, data):
    if path.exists():
        assert path.read_bytes() == data, f'Frozen fixture differs: {path}'
    else:
        path.write_bytes(data)

immutable(ROOT / 'apps/backend-rust/assets/news-system-prompt.txt', prompt.encode())
candidates, articles, _ = inputs(2)
# Nonalphabetic acquired order must survive JSON parsing and request construction.
candidates = dict(reversed(list(candidates.items())))
articles = dict(reversed(list(articles.items())))
symbol = next(iter(candidates))
ref = next(k for k, v in articles.items() if symbol in v['symbols'])
pick = {'symbol': symbol, 'source_ids': [ref], 'stance': 'mixed', 'rationale': 'Visible sourced fixture'}
responses = [
    {'id': 'chatcmpl-fixture', 'usage': {'total_tokens': 42, 'secret': 'NEVER'}, 'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'picks': [pick]})}}]},
    {'choices': [{'finish_reason': 'length', 'message': {'content': '{'}}]},
    {'choices': [{'finish_reason': 'stop', 'message': {'content': '{'}}]},
    {'choices': []},
    {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'picks': [pick, pick]})}}]},
    {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'picks': []})}}]},
    None,
]
cases = []
class Response:
    status_code = 200
    def __init__(self, value): self.value = value
    def json(self): return self.value
class Client:
    def post(self, url, headers, json):
        self.body = json
        assert url == 'https://api.groq.com/openai/v1/chat/completions'
        return Response(response)

with patch.object(discovery.settings, 'groq_key', 'fixture-key'), patch.object(discovery.settings, 'groq_model', 'fixture-model'), patch.object(discovery, 'reserve_call'):
    for response in responses:
        client = Client()
        with patch.object(discovery, 'event') as event:
            try:
                picks, metadata = discovery.rank(candidates, articles, '2026-11-19', client=client)
                result = {'picks': picks, 'metadata': metadata}
            except discovery.ResearchOutputRejected as exc:
                result = {'error': str(exc), 'rejections': exc.rejections}
            cases.append({'response': response, 'expected': result, 'events': [list(c.args) for c in event.call_args_list]})
data = {'candidates': candidates, 'articles': articles, 'body': client.body, 'cases': cases}
immutable(ROOT / 'apps/backend-rust/tests/fixtures/ranking-parity.json', json.dumps(data, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode())
print('Saved frozen request and seven offline Python provider-response oracles')
