import sqlite3
import time

t0 = time.time()
db_path = "/home/kjdragan/lrepos/universal_agent/artifacts/universal_agent_runtime.db" 
# actually wait, where is runtime_db_conn pointing? Let's import main and see.
import sys

sys.path.insert(0, '/home/kjdragan/lrepos/universal_agent/src')

import universal_agent.main as main_module

main_module.db_init_check() # or whatever initializes it

t1 = time.time()
print("Init took:", t1 - t0)

if main_module.runtime_db_conn:
    try:
        main_module.runtime_db_conn.execute("SELECT 1")
        print("db execute took:", time.time() - t1)
    except Exception as e:
        print("execute error:", e)
else:
    print("no runtime_db_conn")
