import logging

from ..config import init_secrets
from universal_agent.services.agentmail_service import AgentMailService

logger = logging.getLogger(__name__)

async def send_simone_alert(subject: str, message: str, is_urgent: bool = False):
    init_secrets()
    service = AgentMailService()
    await service.startup()
    
    prefix = "[URGENT Discord Intel]" if is_urgent else "[Discord Intel]"
    full_subject = f"{prefix} {subject}"
    
    try:
        await service.send_email(
            to="oddcity216@agentmail.to",  # Default inbox for Simone triggers
            subject=full_subject,
            text=message,
            force_send=True
        )
        logger.info(f"Alert sent to Simone: {full_subject}")
    except Exception as e:
        logger.error(f"Failed to send alert to Simone: {e}")
    finally:
        await service.shutdown()
