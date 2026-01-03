# Letta Learning SDK with Z.AI Integration

# .env
LETTA_API_KEY=sk-let-xxx...          # From letta.com
ANTHROPIC_API_KEY=xxx...              # Z.AI API key
ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic  # Z.AI proxy
```

## How It Works

1. **Install SDK**: `uv add agentic-learning --prerelease=allow`

2. **Wrap LLM calls** in `learning()` context:
```python
from agentic_learning import learning
import anthropic

client = anthropic.Anthropic()  # Uses z.ai via ANTHROPIC_BASE_URL

with learning(agent="my_agent", memory=["human", "context"]):
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": "My name is Kevin"}]
    )
```

3. **Memory is automatically**:
   - Captured from conversations
   - Processed by Letta's sleeptime agent (background)
`   - Injected into future prompts

## Key Points

- **Works with z.ai**: The SDK intercepts at the Anthropic client level, so z.ai proxy continues to work
- **Sleeptime processing**: Memory updates happen asynchronously (5-30 seconds delay)
- **Memory blocks**: Customizable blocks like `human`, `context`, `project` etc.
- **Letta Cloud**: Memory stored on Letta's servers (or self-hosted option available)

## Test Results

```
✅ Agent creation with custom memory blocks
✅ Anthropic calls through z.ai proxy
✅ Message history captured
✅ Memory updated by sleeptime agent
✅ Memory retrieval in subsequent calls
```

## Dependencies Added

```
agentic-learning==0.4.3
letta-client==1.0.0a20
```
