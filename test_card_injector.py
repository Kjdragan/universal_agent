import sqlite3
from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.proactive_signals import upsert_generated_card

conn = connect_runtime_db(get_runtime_db_path())
upsert_generated_card(conn, {
    "card_id": "youtube:test_hash_123",
    "source": "youtube",
    "title": "Anthropic Claude Code Demo - Agentic Workflows Explained",
    "summary": "Deep dive into how Claude code and agent MCP works under the hood for system automation.",
    "priority": 4,
    "confidence_score": 0.99,
})
conn.close()
