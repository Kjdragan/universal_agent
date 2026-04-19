from universal_agent.youtube_mode_utils import (
    MODE_EXPLAINER_ONLY,
    MODE_EXPLAINER_PLUS_CODE,
    infer_youtube_mode,
    youtube_explicitly_non_code,
    youtube_probably_code,
)


def test_infer_youtube_mode_defaults_to_explainer_only():
    assert infer_youtube_mode("") == MODE_EXPLAINER_ONLY
    assert youtube_probably_code("") is False


def test_infer_youtube_mode_detects_code_worthy_content():
    text = "How to build an MCP server with Claude Code and TypeScript"
    assert infer_youtube_mode(text) == MODE_EXPLAINER_PLUS_CODE
    assert youtube_probably_code(text) is True
    assert youtube_explicitly_non_code(text) is False


def test_infer_youtube_mode_preserves_non_code_override():
    text = "Recipe tutorial for charcoal souvlaki"
    assert infer_youtube_mode(text) == MODE_EXPLAINER_ONLY
    assert youtube_probably_code(text) is False
    assert youtube_explicitly_non_code(text) is True
