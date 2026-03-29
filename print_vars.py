import asyncio
from universal_agent.infisical_loader import bootstrap_runtime_secrets

async def main():
    bootstrap_runtime_secrets()
    import os
    print("MODEL:", os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL"))
    print("UA_CODE:", os.getenv("UA_CLAUDE_CODE_MODEL"))
    print("BASE:", os.getenv("ANTHROPIC_BASE_URL"))

asyncio.run(main())
