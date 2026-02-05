# ZAI GLM Models - OpenAI Compatible Setup Guide

This guide explains how to configure OpenAI-compatible clients to use ZAI's GLM models (including GLM-4.7 and GLM-4) by changing the base URL and authentication endpoint.

## Overview

ZAI provides OpenAI-compatible API endpoints for their GLM (General Language Model) series. This allows you to use standard OpenAI client libraries with minimal configuration changes.

**Key Models:**
- `glm-4.7` - Latest high-performance model (recommended)
- `glm-4` - Standard GLM model
- `glm-4-flash` - Faster, lower-latency variant
- `glm-4-air` - Lightweight model for simple tasks

## Quick Start

### Method 1: Environment Variables (Recommended)

Set these environment variables before running your application:

```bash
# ZAI API Configuration
export OPENAI_API_KEY="your-zai-api-key-here"
export OPENAI_BASE_URL="https://api.z.ai/api/paas/v4"
```

Then use the standard OpenAI client:

```python
from openai import OpenAI

# Client will automatically pick up env vars
client = OpenAI()

response = client.chat.completions.create(
    model="glm-4.7",
    messages=[
        {"role": "user", "content": "Hello, GLM!"}
    ]
)

print(response.choices[0].message.content)
```

### Method 2: Manual Configuration

Configure the client explicitly in code:

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-zai-api-key-here",
    base_url="https://api.z.ai/api/paas/v4"  # ZAI OpenAI-compatible endpoint
)

response = client.chat.completions.create(
    model="glm-4.7",
    messages=[
        {"role": "user", "content": "Explain quantum computing"}
    ],
    temperature=0.7,
    max_tokens=1000
)

print(response.choices[0].message.content)
```

## Endpoint Configuration

### Chat Completions (OpenAI Compatible)

**Base URL:** `https://api.z.ai/api/paas/v4`

**Endpoint:** `/chat/completions`

**Full URL:** `https://api.z.ai/api/paas/v4/chat/completions`

### Alternative: Anthropic-Compatible Endpoint

ZAI also provides an Anthropic-compatible API:

**Base URL:** `https://api.z.ai/api/anthropic`

**Endpoint:** `/v1/messages`

This is useful if you're using Anthropic's client library:

```python
from anthropic import Anthropic

client = Anthropic(
    api_key="your-zai-api-key-here",
    base_url="https://api.z.ai/api/anthropic"
)

response = client.messages.create(
    model="glm-4.7",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "Hello from Anthropic client!"}
    ]
)

print(response.content[0].text)
```

## Authentication

### API Key Setup

1. **Get your ZAI API key** from the ZAI platform dashboard
2. **Set the API key** via environment variable or directly in code:

```bash
# Environment variable (recommended)
export ZAI_API_KEY="your-zai-api-key-here"
# OR for OpenAI clients
export OPENAI_API_KEY="your-zai-api-key-here"
```

### Authentication Headers

When making raw HTTP requests, include these headers:

```python
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer your-zai-api-key-here",
    # OR
    "x-api-key": "your-zai-api-key-here",
}
```

## Supported Models

| Model | Description | Use Case |
|-------|-------------|----------|
| `glm-4.7` | Latest high-performance model | Complex reasoning, code generation |
| `glm-4` | Standard GLM model | General-purpose tasks |
| `glm-4-flash` | Fast inference | Low-latency applications |
| `glm-4-air` | Lightweight model | Simple tasks, cost optimization |

## Configuration Examples

### Python (OpenAI SDK)

```python
from openai import OpenAI

# Initialize client
client = OpenAI(
    api_key="your-zai-api-key",
    base_url="https://api.z.ai/api/paas/v4"
)

# Chat completion
response = client.chat.completions.create(
    model="glm-4.7",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Write a Python function to calculate fibonacci numbers."}
    ],
    temperature=0.3,
    max_tokens=2000
)

print(response.choices[0].message.content)
```

### Python (httpx - Raw HTTP)

```python
import httpx
import json

async def chat_with_glm(prompt: str):
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer your-zai-api-key",
    }

    payload = {
        "model": "glm-4.7",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 1000
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.z.ai/api/paas/v4/chat/completions",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        return response.json()

# Usage
result = await chat_with_glm("Explain machine learning")
print(result["choices"][0]["message"]["content"])
```

### cURL

```bash
curl -X POST "https://api.z.ai/api/paas/v4/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-zai-api-key" \
  -d '{
    "model": "glm-4.7",
    "messages": [
      {"role": "user", "content": "Hello, GLM!"}
    ],
    "temperature": 0.7,
    "max_tokens": 1000
  }'
```

### JavaScript/TypeScript (OpenAI SDK)

```javascript
import OpenAI from 'openai';

const client = new OpenAI({
  apiKey: 'your-zai-api-key',
  baseURL: 'https://api.z.ai/api/paas/v4',
});

async function chat(message) {
  const response = await client.chat.completions.create({
    model: 'glm-4.7',
    messages: [{ role: 'user', content: message }],
    temperature: 0.7,
    max_tokens: 1000,
  });

  return response.choices[0].message.content;
}

// Usage
const result = await chat('Explain async/await in JavaScript');
console.log(result);
```

## Advanced Configuration

### Streaming Responses

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-zai-api-key",
    base_url="https://api.z.ai/api/paas/v4"
)

stream = client.chat.completions.create(
    model="glm-4.7",
    messages=[{"role": "user", "content": "Count to 100"}],
    stream=True
)

for chunk in stream:
    if chunk.choices[0].delta.content is not None:
        print(chunk.choices[0].delta.content, end="")
```

### Function Calling

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-zai-api-key",
    base_url="https://api.z.ai/api/paas/v4"
)

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City and state, e.g. San Francisco, CA"
                    }
                },
                "required": ["location"]
            }
        }
    }
]

response = client.chat.completions.create(
    model="glm-4.7",
    messages=[
        {"role": "user", "content": "What's the weather in Boston?"}
    ],
    tools=tools
)

print(response.choices[0].message.tool_calls)
```

### JSON Mode

```python
response = client.chat.completions.create(
    model="glm-4.7",
    messages=[
        {"role": "user", "content": "List 3 programming languages as JSON"}
    ],
    response_format={"type": "json_object"}
)

print(response.choices[0].message.content)
```

## Environment Configuration (.env)

Create a `.env` file in your project root:

```bash
# ZAI Configuration for OpenAI-compatible clients
OPENAI_API_KEY=your-zai-api-key-here
OPENAI_BASE_URL=https://api.z.ai/api/paas/v4

# Alternative: Use ZAI-specific env vars
ZAI_API_KEY=your-zai-api-key-here
ZAI_API_BASE=https://api.z.ai/api/paas/v4

# Model selection
OPENAI_MODEL=glm-4.7
ANTHROPIC_DEFAULT_SONNET_MODEL=glm-4.7
ANTHROPIC_DEFAULT_HAIKU_MODEL=glm-4-flash
```

Load in Python:

```python
from dotenv import load_dotenv
import os
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL", "https://api.z.ai/api/paas/v4")
)
```

## Migration from OpenAI

If you're migrating from OpenAI to ZAI:

### Before (OpenAI)
```python
from openai import OpenAI

client = OpenAI(api_key="sk-...")
response = client.chat.completions.create(
    model="gpt-4",
    messages=[...]
)
```

### After (ZAI)
```python
from openai import OpenAI

client = OpenAI(
    api_key="your-zai-api-key",
    base_url="https://api.z.ai/api/paas/v4"  # Only change needed!
)
response = client.chat.completions.create(
    model="glm-4.7",  # Switch to ZAI model
    messages=[...]
)
```

## Common Issues & Solutions

### Issue 1: Authentication Errors

**Problem:** `401 Unauthorized` or `Invalid API key`

**Solution:**
- Verify your ZAI API key is correct
- Ensure you're using the correct header: `Authorization: Bearer <key>` or `x-api-key: <key>`
- Check that the API key has not expired

### Issue 2: Model Not Found

**Problem:** `Model 'glm-4.7' not found`

**Solution:**
- Verify the model name is correct (case-sensitive)
- Check that your API key has access to the specified model
- Try `glm-4` as a fallback

### Issue 3: Connection Timeout

**Problem:** Requests timeout or fail to connect

**Solution:**
```python
client = OpenAI(
    api_key="your-zai-api-key",
    base_url="https://api.z.ai/api/paas/v4",
    timeout=30.0  # Increase timeout
)
```

### Issue 4: Rate Limiting

**Problem:** `429 Too Many Requests`

**Solution:**
- Implement exponential backoff
- Use batch processing for multiple requests
- Consider upgrading your ZAI plan for higher rate limits

## Testing Your Setup

Verify your configuration with this test script:

```python
#!/usr/bin/env python3
"""Test ZAI GLM connectivity via OpenAI-compatible API"""

import os
from openai import OpenAI

def test_zai_connection():
    # Initialize client
    client = OpenAI(
        api_key=os.getenv("ZAI_API_KEY", "your-zai-api-key"),
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.z.ai/api/paas/v4")
    )

    # Test simple completion
    print("Testing ZAI GLM connection...")
    try:
        response = client.chat.completions.create(
            model="glm-4.7",
            messages=[
                {"role": "user", "content": "Reply with 'Connection successful!' and nothing else."}
            ],
            max_tokens=50
        )

        result = response.choices[0].message.content
        print(f"✅ Success: {result}")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    test_zai_connection()
```

## Summary

| Configuration | Value |
|--------------|-------|
| **Base URL** | `https://api.z.ai/api/paas/v4` |
| **Endpoint** | `/chat/completions` |
| **Auth Header** | `Authorization: Bearer <key>` or `x-api-key: <key>` |
| **Models** | `glm-4.7`, `glm-4`, `glm-4-flash`, `glm-4-air` |
| **Environment Var** | `OPENAI_BASE_URL` |
| **API Key Var** | `OPENAI_API_KEY` or `ZAI_API_KEY` |

## Additional Resources

- **ZAI Platform:** [https://z.ai](https://z.ai)
- **GLM Documentation:** Check ZAI platform for latest model docs
- **OpenAI SDK Docs:** [https://github.com/openai/openai-python](https://github.com/openai/openai-python)

## Notes

- ZAI's OpenAI-compatible API supports most standard OpenAI features
- Some advanced features may have limited support
- Always test your specific use case before production deployment
- Model availability depends on your ZAI subscription plan

---

**Last Updated:** February 5, 2026
**Tested With:** GLM-4.7, OpenAI Python SDK v1.x
