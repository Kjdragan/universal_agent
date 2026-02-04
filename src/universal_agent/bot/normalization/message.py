
from dataclasses import dataclass, field
from typing import List, Optional
from telegram import Update

@dataclass
class NormalizedAttachment:
    type: str # 'photo', 'document', 'voice'
    file_id: str
    file_name: Optional[str] = None
    mime_type: Optional[str] = None

@dataclass
class NormalizedMessage:
    text: str
    chat_id: int
    sender_id: int
    sender_name: str
    message_id: int
    is_group: bool
    attachments: List[NormalizedAttachment] = field(default_factory=list)
    raw_update: Optional[Update] = None

def normalize_update(update: Update) -> Optional[NormalizedMessage]:
    """
    Convert a Telegram Update into a NormalizedMessage.
    Returns None if the update is not a message (e.g. edited message, poll, etc - depends on policy).
    """
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    
    if not msg:
        return None
    
    # Basic text
    text = msg.text or msg.caption or ""
    
    # Attachments
    attachments = []
    
    if msg.photo:
        # Get largest photo
        photo = msg.photo[-1]
        attachments.append(NormalizedAttachment(
            type="photo",
            file_id=photo.file_id,
            mime_type="image/jpeg"
        ))
        
    if msg.document:
        attachments.append(NormalizedAttachment(
            type="document",
            file_id=msg.document.file_id,
            file_name=msg.document.file_name,
            mime_type=msg.document.mime_type
        ))
        
    return NormalizedMessage(
        text=text,
        chat_id=chat.id,
        sender_id=user.id if user else 0,
        sender_name=user.full_name if user else "Unknown",
        message_id=msg.message_id,
        is_group=chat.type in ["group", "supergroup"],
        attachments=attachments,
        raw_update=update
    )
