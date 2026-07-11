"""_tokens_match is the constant-time replacement for `token == SECRET` in the
gateway auth guards (CWE-208). It must accept the exact token, reject anything
else, and — preserving the old `if SECRET and token == SECRET` guard — reject
when the expected secret is unset (so an empty token never authenticates)."""

import universal_agent.gateway_server as gs


def test_exact_match_accepted():
    assert gs._tokens_match("s3cret-token", "s3cret-token") is True


def test_wrong_token_rejected():
    assert gs._tokens_match("wrong", "s3cret-token") is False


def test_empty_expected_never_matches():
    # The old guard was `if SESSION_API_TOKEN and token == SESSION_API_TOKEN`;
    # an unconfigured secret must not authenticate an empty supplied token.
    assert gs._tokens_match("", "") is False
    assert gs._tokens_match("anything", "") is False


def test_empty_supplied_rejected_against_real_secret():
    assert gs._tokens_match("", "s3cret-token") is False


def test_none_supplied_is_safe():
    assert gs._tokens_match(None, "s3cret-token") is False
