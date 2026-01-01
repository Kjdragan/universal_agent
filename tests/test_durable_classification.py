from universal_agent.durable.classification import classify_tool


def test_classify_composio_email():
    assert classify_tool("GMAIL_SEND_EMAIL", "composio") == "external"


def test_classify_mcp_upload():
    assert classify_tool("upload_to_composio", "mcp") == "external"


def test_classify_mcp_memory_append():
    assert classify_tool("core_memory_append", "mcp") == "memory"


def test_classify_mcp_write_local():
    assert classify_tool("write_local_file", "mcp") == "local"


def test_classify_composio_search():
    assert classify_tool("COMPOSIO_SEARCH_WEB", "composio") == "read_only"
