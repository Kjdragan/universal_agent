# dag_condenser.py
from datetime import datetime
import json
import os
import uuid


# Stub for the actual LLM call. In a full implementation, this uses ClaudeSDKClient
# to send chunks of messages and receive a summary.
async def _generate_summary(messages_chunk: list, depth: int) -> str:
    """Mock summarizer for MVP testing without burning Anthropic credits loop."""
    # In reality, you'd inject ClaudeSDKClient here, using a hardcoded small prompt.
    count = len(messages_chunk)
    return f"[Depth {depth} Summary of {count} previous interactions. Replaces messages from temporal context to save tokens.]"

async def run_compaction_sweep(db, conversation_id: str, threshold_tokens: int = 10000) -> bool:
    """
    Checks if the conversation context is too large.
    If so, takes oldest raw messages outside the fresh tail, and creates a Depth 0 summary.
    Then condenses Depth 0 summaries to Depth 1 if sufficient count exists.
    """
    # 1. Get current context items
    items = db.get_context_items(conversation_id)
    raw_message_items = [it for it in items if it["type"] == "message"]
    
    # 2. Fresh Tail Protection
    FRESH_TAIL_COUNT = int(os.getenv("UA_LCM_FRESH_TAIL", 10))
    if len(raw_message_items) <= FRESH_TAIL_COUNT:
        return False
        
    evictable_messages = raw_message_items[:-FRESH_TAIL_COUNT]
    if len(evictable_messages) < 2:
        return False # Not enough to summarize meaningfully
        
    # 3. Combine evictable items in DB layer
    # For now, simplistic compaction: 
    # Take the oldest N messages, replace them in `lcm_context_items` with a single summary
    
    to_summarize = evictable_messages[:5] # chunk of 5
    summary_text = await _generate_summary(to_summarize, depth=0)
    
    # Create Summary record
    now = datetime.now().isoformat()
    sum_id = f"sum_{uuid.uuid4().hex[:16]}"
    
    token_est = len(summary_text) // 4
    with db._lock:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO lcm_summaries 
                (id, conversation_id, depth, token_count, content, created_at, earliest_at, latest_at, descendant_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (sum_id, conversation_id, 0, token_est, summary_text, now, now, now, 0))
            
            # Link messages
            for msg_item in to_summarize:
                msg_id = msg_item["data"]["id"]
                cursor.execute("INSERT INTO lcm_summary_messages (summary_id, message_id) VALUES (?, ?)", (sum_id, msg_id))
            
            # Update Context List: replace the summarized messages with this one summary
            # We'll assign it the ordinal of the first replaced message
            first_ordinal = items[items.index(to_summarize[0])]["ordinal"]
            
            # Delete the summarized items from active context
            for msg_item in to_summarize:
                ctx_id = msg_item["id"] if "id" in msg_item else None
                # Actually, our get_context_items didn't return ctx_id, let's just delete by reference
                cursor.execute("DELETE FROM lcm_context_items WHERE conversation_id = ? AND reference_id = ?", 
                               (conversation_id, msg_item["data"]["id"]))
                               
            # Insert the newly formed summary
            cursor.execute("INSERT INTO lcm_context_items (id, conversation_id, ordinal, item_type, reference_id) VALUES (?, ?, ?, ?, ?)",
                           (f"ctx_{uuid.uuid4().hex[:12]}", conversation_id, first_ordinal, 'summary', sum_id))
                           
            conn.commit()
            
    return True
