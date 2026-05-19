"""Built-in pipeline invariants.

Importing this package registers every invariant declared in its submodules.
The watchdog runner (`pipeline_invariants.run_invariants`) walks the registry
and executes each probe; nothing in this package is invoked at import time
beyond the registration calls.

Authoring a new invariant:
    1. Add a module under this package (e.g. `csi_invariants.py`).
    2. Use the `@invariant(...)` decorator from
       `universal_agent.services.pipeline_invariants`.
    3. Import the new module from this `__init__` so it registers on import.
"""

from __future__ import annotations

from universal_agent.services.invariants import (  # noqa: F401
    proactive_pipeline_invariants,
    youtube_invariants,
)
