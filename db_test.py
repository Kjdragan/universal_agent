import sys, os
sys.path.insert(0, "./src")
from universal_agent.durable.db import connect_runtime_db

print(connect_runtime_db().execute("PRAGMA database_list").fetchall()[0]['file'])
