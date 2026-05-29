"""Word-boundary scoring guard for the deterministic keyword classifier.

Regression: substring matching let short keywords like "ide" (ai_coding) and
"rag" (ai_models) match inside unrelated words ("video", "fragrant"), false-
positiving lifestyle videos into AI buckets and manufacturing phantom
convergence clusters. _score_category now uses \b word boundaries.
"""
from csi_ingester.analytics.categories import _score_category


def test_word_boundary_blocks_substring_false_positives():
    assert _score_category("5 ways to cook meat in this video", ["ide"]) == 0
    assert _score_category("a fragrant chickpea curry recipe", ["rag"]) == 0


def test_real_word_matches_still_score():
    assert _score_category("we use an ide for coding", ["ide"]) == 1
    assert _score_category("rag pipelines for retrieval", ["rag"]) == 1


def test_multiword_phrase_scores_two():
    assert _score_category("machine learning models discussed", ["machine learning"]) == 2


def test_no_match_scores_zero():
    assert _score_category("nothing relevant here", ["llm", "agent"]) == 0
