from __future__ import annotations

from csi_ingester.store import token_usage
from csi_ingester.store.sqlite import connect, ensure_schema


def test_token_usage_insert_roundtrip(tmp_path):
    conn = connect(tmp_path / "csi.db")
    ensure_schema(conn)

    token_usage.insert_usage(
        conn,
        process_name="rss_digest_claude",
        model_name="claude-3-5-haiku-latest",
        prompt_tokens=120,
        completion_tokens=45,
        total_tokens=165,
        metadata={"source": "youtube_channel_rss"},
    )

    row = conn.execute(
        """
        SELECT process_name, model_name, prompt_tokens, completion_tokens, total_tokens
        FROM token_usage
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    assert row is not None
    assert str(row["process_name"]) == "rss_digest_claude"
    assert str(row["model_name"]) == "claude-3-5-haiku-latest"
    assert int(row["prompt_tokens"]) == 120
    assert int(row["completion_tokens"]) == 45
    assert int(row["total_tokens"]) == 165

