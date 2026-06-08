---
name: Replit OpenAI Python integration
description: How to use Replit AI Integrations proxy with the Python openai package (not just JS)
---

## Setup
Run `setupReplitAIIntegrations({ providerSlug: "openai", ... })` in the JS sandbox to provision:
- `AI_INTEGRATIONS_OPENAI_BASE_URL`
- `AI_INTEGRATIONS_OPENAI_API_KEY`

These env vars are then accessible in Python via `os.environ.get(...)`.

## Usage in Python
```python
from openai import OpenAI

base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL") or None
api_key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")

client = OpenAI(api_key=api_key, base_url=base_url)
resp = client.chat.completions.create(model="gpt-5.4", ...)
```

**Why:** The Replit AI Integrations skill documents JS/TS templates, but the underlying
env vars work identically with the Python SDK — just pass them as constructor kwargs.

**How to apply:** Any Python (Streamlit, Flask, etc.) app needing OpenAI access without
user API keys — provision via the JS sandbox callback, then read env vars in Python.

## Model notes (as of June 2026)
- gpt-5.4: best general-purpose text model
- response_format={"type": "json_object"}: works with gpt-5.4, gives clean JSON
- max_completion_tokens instead of max_tokens for gpt-5+ series
