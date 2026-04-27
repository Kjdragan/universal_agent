import asyncio
import os

import pytest

from universal_agent.lossless_memory.dag_condenser import run_compaction_sweep
from universal_agent.lossless_memory.db import LosslessDB
from universal_agent.lossless_memory.history_adapter import LosslessMessageHistory


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_lcm.db"
    return LosslessDB(str(db_file))

def test_db_inserts(temp_db):
    conv_id = temp_db.get_or_create_conversation("test_session_1")
    # Insert some messages
    temp_db.insert_message(conv_id, "user", "Hello", [], 5)
    temp_db.insert_message(conv_id, "assistant", "Hi there", [], 8)

    items = temp_db.get_context_items(conv_id)
    assert len(items) == 2
    assert items[0]["type"] == "message"
    assert items[0]["data"]["content"] == "Hello"

@pytest.mark.asyncio
async def test_compaction_sweep(temp_db):
    conv_id = temp_db.get_or_create_conversation("test_session_2")
    
    # Needs to be > FRESH_TAIL_COUNT to compact.
    # We set FRESH_TAIL_COUNT to 2 for this test via env
    os.environ["UA_LCM_FRESH_TAIL"] = "2"
    
    # Insert 8 messages
    for i in range(8):
        temp_db.insert_message(conv_id, "user", f"Msg {i}", [], 10)
        
    items = temp_db.get_context_items(conv_id)
    assert len(items) == 8
    
    # Run condenser
    compacted = await run_compaction_sweep(temp_db, conv_id)
    assert compacted is True
    
    # After compaction, we had 8 msgs. Tail is 2. 6 are evictable.
    # The condenser takes a chunk of 5 and summarizes it.
    items_after = temp_db.get_context_items(conv_id)
    # The 5 msgs became 1 summary. The remaining 3 msgs are still messages.
    # Total items should be 4
    assert len(items_after) == 4
    
    # First item should be the summary
    assert items_after[0]["type"] == "summary"
    assert "Depth 0 Summary" in items_after[0]["data"]["content"]

def test_history_adapter(tmp_path):
    os.environ["UA_LOSSLESS_DB_PATH"] = str(tmp_path / "adapter.db")
    history = LosslessMessageHistory(system_prompt_tokens=1000, session_id="test_adapter")
    
    # Add messages
    history.add_message("user", "Adapter Test", None)
    history.add_message("assistant", [{"type": "text", "text": "OK"}], None)
    
    api_msgs = history.get_messages()
    assert len(api_msgs) == 2
    assert api_msgs[1]["role"] == "assistant"
    assert isinstance(api_msgs[1]["content"], list)
    assert api_msgs[1]["content"][0]["text"] == "OK"
