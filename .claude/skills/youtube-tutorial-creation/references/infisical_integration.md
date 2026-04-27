# Infisical Integration & Secrets Management Guide

## What is Infisical?

Infisical is our canonical secrets management and environment configuration service. Instead of storing sensitive API keys, database credentials, or environment-specific configurations in local `.env` files that risk being committed or scattered across machines, Infisical securely stores and injects them directly into your application at runtime.

## The Golden Rule: Bootstrapping vs. Runtime Configuration

> [!IMPORTANT]
> **Do not store application secrets in `.env` files.**

The only variables that belong in a `.env` file are the **bare minimum bootstrap credentials** required for the machine to authenticate with the Infisical service itself (such as a Machine Identity Client ID/Secret or a Universal Auth Token). 

Once the machine is authenticated, Infisical provides all other application secrets (like your `GEMINI_API_KEY`) dynamically.

### The Bootstrapping Flow
1. **Machine Authentication:** A minimal `.env` file contains only the credentials needed to talk to Infisical.
2. **Secret Injection:** The application runs wrapped in the Infisical CLI, or uses the Infisical SDK, which securely pulls the actual secrets directly into the process environment memory.

---

## How to Use Infisical in a New Project

There are two primary ways to integrate Infisical into a new repository project. Both ensure that your code never contains hardcoded secrets.

### Method 1: The Infisical CLI (Recommended)

The cleanest way to use Infisical without modifying your application code is to use the `infisical run` command. This command fetches the secrets and injects them into the environment variables of your execution process seamlessly.

If your project uses `uv` for Python dependency management, you run your script like this:

```bash
# Instead of: uv run script.py
infisical run -- uv run script.py
```

Inside your code, you simply read the environment variable as usual. The code remains completely agnostic to Infisical:

```python
import os

# This will successfully read the key injected by the CLI
api_key = os.environ.get("GEMINI_API_KEY") 
```

### Method 2: Programmatic Access (Python SDK)

If you need to fetch secrets dynamically from within your application code, you can use the `infisical-sdk` Python package. 

Here is an example `load_env.py` helper you can include in your new project. It attempts to load the Gemini API key from Infisical programmatically, and falls back to standard environment variables if already injected via the CLI:

```python
import os

def load_env():
    """
    Attempts to load the GEMINI_API_KEY from Infisical programmatically.
    Falls back to os.environ if already injected via CLI or local environment.
    """
    try:
        from infisical_sdk import InfisicalClient
        
        # This automatically picks up authentication from the environment's bootstrap vars
        client = InfisicalClient()
        
        # Fetch the specific secret. 
        secret = client.get_secret("GEMINI_API_KEY")
        if secret and secret.secret_value:
            os.environ["GEMINI_API_KEY"] = secret.secret_value
            return
            
    except Exception as e:
        # If Infisical is unavailable, rely on the standard environment fallback
        pass

    if "GEMINI_API_KEY" not in os.environ:
        print("Warning: GEMINI_API_KEY not found in Infisical or environment.")
```

**Usage in your main script:**
```python
from load_env import load_env
from google import genai

# Inject the secret into the environment memory
load_env()

# The genai SDK automatically looks for os.environ["GEMINI_API_KEY"]
client = genai.Client() 
```

---

## Configuring Your Gemini Project

For your upcoming Gemini API project, follow these steps to ensure compliance with the secrets policy:

1. **Centralize the Key:** Ensure `GEMINI_API_KEY` is added to your Infisical project dashboard under the relevant environment (e.g., `dev` or `prod`).
2. **Keep `.env` Clean:** Do not place `GEMINI_API_KEY` into your local `.env`. Only include your Infisical bootstrap token.
3. **Provision at Runtime:** Use `infisical run -- uv run ...` or the `load_env()` helper pattern to securely provision the key to the `google-genai` SDK at execution time.
