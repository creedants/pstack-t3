from ..support import CLAUDE, ROOT


CATALOG = ROOT / "tests/fixtures/catalog.json"
PARENT = CLAUDE
XHIGH = '{"instanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "xhigh"}}'
XHIGH_SEAT = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "xhigh"}}
MEDIUM = '{"instanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "medium"}}'
LOW = '{"instanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "low"}}'
FAST = '{"instanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"fastMode": false, "effort": "medium"}}'
BARE = '{"instanceId": "grok", "model": "grok-4.7", "options": {}}'
