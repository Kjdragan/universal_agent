import sys
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

try:
    conn = connect_runtime_db(get_activity_db_path())
    print("Success")
except Exception as e:
    print(e)
