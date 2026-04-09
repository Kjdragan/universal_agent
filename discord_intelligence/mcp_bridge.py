import sqlite3
from typing import Optional
from mcp.server.fastmcp import FastMCP
from discord_intelligence.config import get_db_path

mcp = FastMCP("Discord Intelligence Bridge")

def get_connection():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn

@mcp.tool()
def search_messages(query: str, limit: int = 50, channel_tier: Optional[str] = None) -> list[dict]:
    """Search ingested Discord messages via simple text match.
    
    Args:
        query: The substring to search for in message content.
        limit: Max rows to return (default 50)
        channel_tier: Optional tier filter ('A', 'B', 'C') for channels
    """
    db = get_connection()
    try:
        sql = '''
            SELECT m.id, m.author_name, m.content, m.timestamp, c.name as channel_name 
            FROM messages m
            JOIN channels c ON m.channel_id = c.id
            WHERE m.content LIKE ?
        '''
        args = [f"%{query}%"]
        if channel_tier:
            sql += " AND c.tier = ?"
            args.append(channel_tier)
            
        sql += " ORDER BY m.timestamp DESC LIMIT ?"
        args.append(limit)
        
        rows = db.execute(sql, args).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()

@mcp.tool()
def get_signals(severity: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Get high-value triage signals extracted from messages.
    
    Args:
        severity: Optional filter ('critical', 'high', 'medium', 'low')
        limit: Max rows to return
    """
    db = get_connection()
    try:
        sql = '''
            SELECT s.layer, s.rule_matched, s.severity, s.action_taken, s.created_at, m.content as original_message 
            FROM signals s
            JOIN messages m ON s.message_id = m.id
        '''
        args = []
        if severity:
            sql += " WHERE s.severity = ?"
            args.append(severity)
            
        sql += " ORDER BY s.created_at DESC LIMIT ?"
        args.append(limit)
        
        rows = db.execute(sql, args).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()

@mcp.tool()
def get_insights(limit: int = 10) -> list[dict]:
    """Get high-level aggregated insights and summaries from the triage batches.
    
    Args:
        limit: Max rows to return
    """
    db = get_connection()
    try:
        sql = '''
            SELECT i.topic, i.summary, i.sentiment, i.urgency, i.confidence, i.created_at
            FROM insights i
            ORDER BY i.created_at DESC LIMIT ?
        '''
        rows = db.execute(sql, [limit]).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()

@mcp.tool()
def get_events(status: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Get formally scheduled Discord events in monitored servers.
    
    Args:
        status: Optional filter by status (e.g., 'active', 'scheduled', 'completed')
        limit: Max rows to return
    """
    db = get_connection()
    try:
        sql = "SELECT id, name, description, start_time, end_time, location, status FROM scheduled_events"
        args = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
            
        sql += " ORDER BY start_time DESC LIMIT ?"
        args.append(limit)
        
        rows = db.execute(sql, args).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()

if __name__ == "__main__":
    mcp.run(transport="stdio")
