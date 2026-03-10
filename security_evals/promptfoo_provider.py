import asyncio
import os
import sys

# Ensure universal_agent is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from universal_agent.gateway import ExternalGateway, GatewayRequest

def call_api(prompt, options, context):
    """
    Promptfoo provider entry point.
    """
    try:
        return asyncio.run(_run_eval(prompt, options, context))
    except Exception as e:
        return {"error": str(e)}

async def _run_eval(prompt, options, context):
    gateway_url = os.getenv("UA_GATEWAY_URL", "http://localhost:8002")
    gateway = ExternalGateway(base_url=gateway_url)
    user_id = "promptfoo_redteamer"
    
    try:
        # Create a new isolated session for this evaluation prompt
        session = await gateway.create_session(user_id=user_id)
        
        # Send the malicious prompt
        request = GatewayRequest(user_input=prompt)
        
        # run_query handles WebSocket streaming and aggregates the final text
        result = await gateway.run_query(session, request)
        
        return {
            "output": result.response_text,
            "metadata": {
                "session_id": session.session_id,
                "tool_calls": result.tool_calls
            }
        }
    finally:
        await gateway.close()
