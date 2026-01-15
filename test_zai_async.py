import os
import asyncio
from anthropic import AsyncAnthropic

# Load env vars manually for local test if not loaded
# In real run, agent has these loaded.
from dotenv import load_dotenv
load_dotenv()

async def main():
    # Load config (simulating what the agent does based on .env)
    api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    # Use generic model name that ZAI supports
    model = os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "glm-4.7")

    print(f"Config: URL={base_url}, Model={model}, KeyPresent={bool(api_key)}")
    
    if not api_key:
        print("❌ Error: ANTHROPIC_AUTH_TOKEN not found in env")
        return

    client = AsyncAnthropic(api_key=api_key, base_url=base_url)
    
    try:
        print("Sending request...")
        resp = await client.messages.create(
            model=model,
            max_tokens=50,
            messages=[{"role": "user", "content": "Return the word 'Connectivity' and nothing else."}]
        )
        print("✅ Success! Response:")
        print(resp.content[0].text)
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
