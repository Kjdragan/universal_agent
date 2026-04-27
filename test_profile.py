import json
from pathlib import Path
import time


def test():
    t0 = time.time()
    try:
        owners_file = str(Path("config/dashboard_owners.json").resolve())
        path = Path(owners_file)
        if not path.exists() or path.stat().st_size <= 0:
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        pass
    print(f"took {time.time()-t0}")

test()
