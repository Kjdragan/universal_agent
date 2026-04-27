from datetime import datetime
import os

import pytz

now = datetime.now(pytz.timezone("America/Chicago"))
print("Chicago time:", now.isoformat())
print("Chicago hour:", now.hour)
