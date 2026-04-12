import asyncio
from universal_agent.gateway_server import _task_hub_open_conn
from universal_agent import task_hub

conn = _task_hub_open_conn()
try:
    print("Testing Ensure Schema")
    task_hub.ensure_schema(conn)
    print("Ensure schema ran successfully!")
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    conn.close()
