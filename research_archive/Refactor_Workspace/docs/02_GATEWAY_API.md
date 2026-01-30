# Gateway API Reference

## Overview

The Gateway API provides a unified interface for agent execution, supporting both in-process and remote modes. This document covers the Python API for programmatic access.

---

## Core Classes

### GatewaySession

Represents an active session with the gateway.

```python
@dataclass
class GatewaySession:
    session_id: str
    user_id: str
    workspace_dir: str
    created_at: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)
```

**Fields:**
- `session_id` — Unique identifier (UUID format)
- `user_id` — Identifier of the session owner
- `workspace_dir` — Absolute path to the session workspace
- `created_at` — UTC timestamp of session creation
- `metadata` — Extensible key-value store for custom data

### GatewayRequest

Encapsulates a query to be executed.

```python
@dataclass
class GatewayRequest:
    user_input: str
    context: Optional[Dict[str, Any]] = None
    max_iterations: int = 25
    stream: bool = True
```

**Fields:**
- `user_input` — The user's query or instruction
- `context` — Optional context injection (files, state, etc.)
- `max_iterations` — Maximum agent iterations before timeout
- `stream` — Whether to stream events (always True for execute())

### GatewayResponse

Result of a completed query.

```python
@dataclass
class GatewayResponse:
    session_id: str
    success: bool
    output: str
    tool_calls: List[Dict[str, Any]]
    error: Optional[str] = None
    events: List[AgentEvent] = field(default_factory=list)
```

---

## Gateway Protocol

All gateway implementations must satisfy this protocol:

```python
class Gateway(Protocol):
    async def create_session(
        self,
        user_id: str,
        workspace_dir: Optional[str] = None,
    ) -> GatewaySession:
        """Create a new session."""
        ...

    async def get_session(
        self,
        session_id: str,
    ) -> Optional[GatewaySession]:
        """Retrieve an existing session."""
        ...

    async def list_sessions(
        self,
        user_id: Optional[str] = None,
    ) -> List[GatewaySession]:
        """List sessions, optionally filtered by user."""
        ...

    async def execute(
        self,
        session: GatewaySession,
        request: GatewayRequest,
    ) -> AsyncIterator[AgentEvent]:
        """Execute a query and stream events."""
        ...

    async def run_query(
        self,
        session: GatewaySession,
        request: GatewayRequest,
    ) -> GatewayResponse:
        """Execute a query and return final result."""
        ...
```

---

## InProcessGateway

Executes queries in the same process using `UniversalAgent` directly.

### Usage

```python
from universal_agent.gateway import InProcessGateway, GatewayRequest

# Create gateway
gateway = InProcessGateway()

# Create session
session = await gateway.create_session(
    user_id="user_123",
    workspace_dir="/home/user/project",
)

# Execute query (streaming)
request = GatewayRequest(user_input="List all Python files")
async for event in gateway.execute(session, request):
    if event.type == EventType.TEXT:
        print(event.data.get("text", ""), end="")
    elif event.type == EventType.TOOL_CALL:
        print(f"\n[Tool: {event.data.get('name')}]")

# Execute query (blocking)
response = await gateway.run_query(session, request)
print(f"Success: {response.success}")
print(f"Output: {response.output}")
```

### Workspace Binding

Sessions are bound to a workspace directory. The agent operates within this directory for file operations:

```python
# Bind to specific directory
session = await gateway.create_session(
    user_id="user_123",
    workspace_dir="/path/to/project",
)

# Bind to temp directory (auto-created)
session = await gateway.create_session(user_id="user_123")
# workspace_dir will be something like /tmp/gateway_session_abc123/
```

### Session Reuse

Sessions maintain agent state across queries:

```python
# First query
await gateway.run_query(session, GatewayRequest(user_input="Create file.txt"))

# Second query (agent remembers context)
await gateway.run_query(session, GatewayRequest(user_input="Read file.txt"))
```

---

## ExternalGateway

Connects to a remote gateway server via HTTP/WebSocket.

### Usage

```python
from universal_agent.gateway import ExternalGateway, GatewayRequest

# Create gateway client
gateway = ExternalGateway(base_url="http://localhost:8002")

# Create session (HTTP POST)
session = await gateway.create_session(
    user_id="user_123",
    workspace_dir="/home/user/project",
)

# Execute query (WebSocket streaming)
request = GatewayRequest(user_input="Analyze code")
async for event in gateway.execute(session, request):
    print(f"{event.type}: {event.data}")
```

### Connection Management

```python
# Explicit close
await gateway.close()

# Context manager (recommended)
async with ExternalGateway("http://localhost:8002") as gateway:
    session = await gateway.create_session(user_id="user")
    # ... use gateway
# Automatically closed
```

---

## Event Handling

### Event Structure

```python
@dataclass
class AgentEvent:
    type: EventType
    data: Dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
```

### Common Event Patterns

```python
async for event in gateway.execute(session, request):
    match event.type:
        case EventType.TEXT:
            # Incremental text output
            print(event.data["text"], end="", flush=True)
        
        case EventType.TOOL_CALL:
            # Tool invocation
            tool_name = event.data["name"]
            tool_input = event.data["input"]
            print(f"\n🔧 Calling {tool_name}")
        
        case EventType.TOOL_RESULT:
            # Tool response
            result = event.data.get("result", "")
            print(f"   Result: {result[:100]}...")
        
        case EventType.ERROR:
            # Handle error
            print(f"❌ Error: {event.data.get('error')}")
            break
        
        case EventType.ITERATION_END:
            # Turn complete
            iteration = event.data.get("iteration", 0)
            print(f"\n--- Iteration {iteration} complete ---")
```

### Collecting All Events

```python
# Using run_query (collects internally)
response = await gateway.run_query(session, request)
for event in response.events:
    print(f"{event.type}: {event.data}")

# Manual collection
events = []
async for event in gateway.execute(session, request):
    events.append(event)
```

---

## Error Handling

### Exception Types

```python
from universal_agent.gateway import (
    GatewayError,
    SessionNotFoundError,
    ExecutionError,
)

try:
    session = await gateway.get_session("invalid_id")
except SessionNotFoundError:
    print("Session not found")

try:
    async for event in gateway.execute(session, request):
        pass
except ExecutionError as e:
    print(f"Execution failed: {e}")
```

### Timeout Handling

```python
import asyncio

try:
    async with asyncio.timeout(60):
        async for event in gateway.execute(session, request):
            pass
except asyncio.TimeoutError:
    print("Query timed out")
```

---

## Best Practices

1. **Reuse Sessions** — Create once, execute many times
2. **Handle Events Incrementally** — Don't buffer all events in memory for long queries
3. **Set Appropriate Timeouts** — Especially for external gateway connections
4. **Close Resources** — Use context managers or explicit close()
5. **Log Events for Debugging** — Event streams are your audit trail
