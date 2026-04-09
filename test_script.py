import asyncio
import sys

async def fake_dispatch(*args, **kwargs):
    print("MOCK DISPATCH")
    # print(args[0])  # keep it less noisy
    return {"content": [{"text": "{\"ok\": true, \"mission_id\": \"test\"}"}]}

async def run_test():
    from universal_agent.tools import vp_orchestration
    vp_orchestration._vp_dispatch_mission_impl = fake_dispatch
    import universal_agent.scripts.morning_briefing_agent as mba
    await mba.main()

asyncio.run(run_test())
