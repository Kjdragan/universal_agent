
import asyncio
import importlib.util
import json
import logging
import os
import sys
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from fastapi import Request, Response
from pydantic import BaseModel, Field

from universal_agent.gateway import InProcessGateway, GatewayRequest
from universal_agent.ops_config import load_ops_config, resolve_ops_config_path

logger = logging.getLogger(__name__)

DEFAULT_HOOKS_PATH = "/hooks"
DEFAULT_HOOKS_MAX_BODY_BYTES = 256 * 1024

class HookMatchConfig(BaseModel):
    path: Optional[str] = None
    source: Optional[str] = None
    headers: Optional[Dict[str, str]] = None

class HookTransformConfig(BaseModel):
    module: str
    export: Optional[str] = None

class HookMappingConfig(BaseModel):
    id: Optional[str] = None
    match: Optional[HookMatchConfig] = None
    action: str = "agent"  # "wake" or "agent"
    wake_mode: str = "now"
    transform: Optional[HookTransformConfig] = None
    message_template: Optional[str] = None
    text_template: Optional[str] = None
    name: Optional[str] = None
    session_key: Optional[str] = None
    deliver: bool = True
    allow_unsafe_external_content: bool = False
    to: Optional[str] = None
    model: Optional[str] = None
    thinking: Optional[str] = None
    timeout_seconds: Optional[int] = None

class HooksConfig(BaseModel):
    enabled: bool = False
    token: Optional[str] = None
    base_path: str = DEFAULT_HOOKS_PATH
    max_body_bytes: int = DEFAULT_HOOKS_MAX_BODY_BYTES
    transforms_dir: Optional[str] = None
    mappings: List[HookMappingConfig] = Field(default_factory=list)

class HookAction(BaseModel):
    kind: str  # "wake" or "agent"
    text: Optional[str] = None
    message: Optional[str] = None
    mode: str = "now" # wake mode
    name: Optional[str] = None
    session_key: Optional[str] = None
    deliver: bool = True
    allow_unsafe_external_content: bool = False
    to: Optional[str] = None
    model: Optional[str] = None
    thinking: Optional[str] = None
    timeout_seconds: Optional[int] = None


class HooksService:
    def __init__(self, gateway: InProcessGateway):
        self.gateway = gateway
        self.config = self._load_config()
        self.transform_cache = {}

    def _load_config(self) -> HooksConfig:
        ops_config = load_ops_config()
        hooks_data = ops_config.get("hooks", {})
        
        # Env var overrides
        if os.getenv("UA_HOOKS_ENABLED") == "true":
            hooks_data["enabled"] = True
        if token := os.getenv("UA_HOOKS_TOKEN"):
            hooks_data["token"] = token
            
        return HooksConfig(**hooks_data)

    def is_enabled(self) -> bool:
        return self.config.enabled

    async def handle_request(self, request: Request, subpath: str) -> Response:
        if not self.config.enabled:
            return Response("Hooks disabled", status_code=404)

        # Auth check
        token = self._extract_token(request)
        if not token or token != self.config.token:
            return Response("Unauthorized", status_code=401)
        
        # Read body
        try:
            body_bytes = await request.body()
            if len(body_bytes) > self.config.max_body_bytes:
                return Response("Payload too large", status_code=413)
            
            payload = {}
            if body_bytes:
                try:
                    payload = json.loads(body_bytes)
                except json.JSONDecodeError:
                    return Response("Invalid JSON", status_code=400)
        except Exception as e:
            return Response(f"Error reading body: {str(e)}", status_code=400)

        # Context for matching/templating
        context = {
            "payload": payload,
            "headers": dict(request.headers),
            "path": subpath,
            "query": dict(request.query_params)
        }

        # Match and dispatch
        try:
            for mapping in self.config.mappings:
                if self._mapping_matches(mapping, context):
                    action = await self._build_action(mapping, context)
                    if action:
                        asyncio.create_task(self._dispatch_action(action))
                        return Response(json.dumps({"ok": True, "action": action.kind}), media_type="application/json", status_code=202)
            
            return Response("No matching hook found", status_code=404)
        except Exception as e:
            logger.exception("Error processing hook")
            return Response(json.dumps({"ok": False, "error": str(e)}), status_code=500, media_type="application/json")

    def _extract_token(self, request: Request) -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return request.headers.get("X-UA-Hooks-Token")

    def _mapping_matches(self, mapping: HookMappingConfig, context: Dict) -> bool:
        if mapping.match:
            if mapping.match.path and mapping.match.path != context["path"]:
                return False
            if mapping.match.source:
                payload_source = context["payload"].get("source")
                if payload_source != mapping.match.source:
                    return False
             # Add header matching if needed
        return True

    async def _build_action(self, mapping: HookMappingConfig, context: Dict) -> Optional[HookAction]:
        # base action
        base_action = self._create_base_action(mapping, context)
        
        # Apply transform
        if mapping.transform:
            transform_fn = self._load_transform(mapping.transform)
            if transform_fn:
                try:
                    if asyncio.iscoroutinefunction(transform_fn):
                        override = await transform_fn(context)
                    else:
                        override = transform_fn(context)

                    if override is None:
                         # Transform indicated skip
                         return None
                    # Merge logic could go here, for now simpler override
                    # If transform returns a dict, merge it into base_action
                    if isinstance(override, dict):
                         updated_data = base_action.model_dump()
                         updated_data.update(override)
                         base_action = HookAction(**updated_data)
                except Exception as e:
                    logger.error(f"Transform failed: {e}")
                    raise e

        return base_action

    def _create_base_action(self, mapping: HookMappingConfig, context: Dict) -> HookAction:
        if mapping.action == "wake":
            text = self._render_template(mapping.text_template or "", context)
            return HookAction(kind="wake", text=text, mode=mapping.wake_mode)
        else:
            message = self._render_template(mapping.message_template or "", context)
            return HookAction(
                kind="agent",
                message=message,
                name=self._render_template(mapping.name or "Hook", context),
                deliver=mapping.deliver,
                session_key=self._render_template(mapping.session_key or "", context),
                wake_mode=mapping.wake_mode
            )

    def _load_transform(self, transform_config: HookTransformConfig):
        # Resolve path
        config_path = resolve_ops_config_path()
        config_dir = config_path.parent
        
        # Use transforms_dir if set, else relative to config file
        base_dir = config_dir
        if self.config.transforms_dir:
             base_dir = (config_dir / self.config.transforms_dir).resolve()
             
        module_path = (base_dir / transform_config.module).resolve()
        
        # Check cache
        cache_key = str(module_path)
        if cache_key in self.transform_cache:
            return self.transform_cache[cache_key]

        if not module_path.exists():
            raise FileNotFoundError(f"Transform module not found: {module_path}")

        spec = importlib.util.spec_from_file_location("hook_transform", module_path)
        if not spec or not spec.loader:
             raise ImportError(f"Could not load spec for {module_path}")
        
        module = importlib.util.module_from_spec(spec)
        sys.modules["hook_transform_temp"] = module
        spec.loader.exec_module(module)
        
        export_name = transform_config.export or "transform"
        if not hasattr(module, export_name):
             raise ImportError(f"Module {module_path} does not export '{export_name}'")
             
        fn = getattr(module, export_name)
        self.transform_cache[cache_key] = fn
        return fn

    def _render_template(self, template: str, context: Dict) -> str:
        # Simple templating: {{ payload.x }} {{ headers.y }}
        # Can rely on python's str.format or a simple regex replacer
        # Clawdbot uses a custom replacer. Let's do a simple one for now.
        # Supporting dot notation is key.
        import re
        
        def getattr_deep(obj, path):
            parts = path.split('.')
            curr = obj
            for p in parts:
                if isinstance(curr, dict):
                    curr = curr.get(p)
                else:
                    return None
                if curr is None: return None
            return curr

        def replacer(match):
            expr = match.group(1).strip()
            val = getattr_deep(context, expr)
            return str(val) if val is not None else ""

        return re.sub(r'\{\{\s*([^}]+)\s*\}\}', replacer, template)

    async def _dispatch_action(self, action: HookAction):
        # This is where we interface with the gateway
        logger.info(f"Dispatching hook action: {action.kind}")
        
        if action.kind == "wake":
             # Use OpsService-like logic or direct gateway interaction
             pass # To impl: wake up sessions
             
        elif action.kind == "agent":
             session_key = action.session_key
             if not session_key:
                  logger.warning("No session key for agent hook")
                  return

             # Generate a safe session ID from the key if needed, or use as is if it's a valid ID
             # For now, simplistic approach: use session_key as session_id if it looks like one,
             # otherwise hash it or prefix it?
             # Clawdbot uses sessionKey as the ID directly.
             session_id = session_key.replace(":", "_").replace("-", "_")
             
             try:
                 # Check if session exists (to resume) or create
                 # InProcessGateway.resume_session checks existence
                 try:
                     session = await self.gateway.resume_session(session_id)
                 except ValueError:
                     # Create if not found
                     logger.info(f"Creating new session for hook: {session_id}")
                     session = await self.gateway.create_session(user_id="webhook", workspace_dir=None)
                     # If we want to force the ID, we'd need to modify create_session or use internal maps.
                     # But create_session generates a random ID. 
                     # Wait, InProcessGateway.create_session generates a random ID.
                     # To support deterministic IDs (like hook:gmail:123), we need to handle that.
                     # Since we can't easily force ID in create_session without changing it,
                     # we might just settle for creating a new session and mapping it, OR
                     # we modify create_session to accept an ID? 
                     # Looking at InProcessGateway.create_session:
                     # it generates ID.
                     pass 

                 # Actually, create_session doesn't accept ID.
                 # But we can pass workspace_dir!
                 # If we pass workspace_dir = AGENT_RUN_WORKSPACES/<session_key>, then session_id becomes <session_key>.
                 
                 workspace_base = Path("AGENT_RUN_WORKSPACES")
                 workspace_dir = workspace_base / session_id
                 session = await self.gateway.create_session(user_id="webhook", workspace_dir=str(workspace_dir))
                 
                 # Send message
                 request = GatewayRequest(
                     user_input=action.message,
                     metadata={"source": "webhook", "hook_name": action.name}
                 )
                 
                 # Execute
                 async for _ in self.gateway.execute(session, request):
                     pass
                     
             except Exception as e:
                 logger.error(f"Failed to dispatch to agent: {e}")

