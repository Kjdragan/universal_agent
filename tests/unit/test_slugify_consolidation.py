"""Equivalence tests for the _slugify consolidation (Workstream A2).

Five drifted copies now delegate to the canonical
``wiki/core.py::_slugify`` for charset/case/separator work; truncation
bounds stay local (40 for the directed/tutorial demo pair, 48 for the
dispatch_direct_demo CLI, none elsewhere).

The load-bearing invariant: ``directed_demo_builds.directed_demo_slug`` and
``tutorial_demo_finalize.proactive_demo_slug``'s underlying ``_slugify``
must stay BYTE-IDENTICAL for the same input — ``vp/worker_loop.py``'s
legacy fallback recomputes a directed-demo on-disk dir name with the
proactive-lane function, which only resolves correctly while the two
implementations agree.
"""

from __future__ import annotations

import pytest

from universal_agent.scripts.dispatch_direct_demo import _slugify as direct_cli_slug
from universal_agent.services.claude_code_intel_rollup import _slugify as rollup_slug
from universal_agent.services.cody_scaffold import _slugify as cody_slug
from universal_agent.services.directed_demo_builds import (
    _slugify as directed_slugify,
    directed_demo_slug,
)
from universal_agent.services.tutorial_demo_finalize import (
    _slugify as tutorial_slugify,
    proactive_demo_slug,
)
from universal_agent.wiki.core import _slugify as canonical

LONG_TITLE = "A Very Long Video Title That Keeps Going And Going Beyond Forty Characters!"
SAMPLE_INPUTS = [
    "Hello, World!",
    "  spaces  and---dashes  ",
    "Ünïcödé stripped",
    "already-clean-slug",
    "",
    LONG_TITLE,
]


@pytest.mark.parametrize("value", SAMPLE_INPUTS)
def test_directed_and_tutorial_stay_byte_identical(value):
    assert directed_slugify(value, fallback="x") == tutorial_slugify(value, fallback="x")


def test_forty_char_bound_preserved():
    # Pre-consolidation behavior: truncate to 40 then strip trailing dashes.
    slug = proactive_demo_slug(LONG_TITLE)
    assert len(slug) <= 40
    assert not slug.endswith("-")
    assert slug == directed_demo_slug(LONG_TITLE)


def test_fallbacks_survive():
    assert directed_demo_slug("") == "directed"
    assert proactive_demo_slug("") == "tutorial"
    assert cody_slug("!!!", fallback="entity") == "entity"
    assert rollup_slug("", fallback="bundle") == "bundle"
    assert direct_cli_slug("") == "direct-demo"


def test_canonical_charset_equivalence():
    # The old copies' regex/case/strip pipeline must match canonical output
    # for untruncated inputs.
    for value in SAMPLE_INPUTS:
        expected = canonical(value, fallback="f")
        assert cody_slug(value, fallback="f") == expected
        assert rollup_slug(value, fallback="f") == expected
        if len(expected) <= 40:
            assert directed_slugify(value, fallback="f") == expected


def test_direct_cli_48_bound():
    slug = direct_cli_slug(LONG_TITLE)
    assert len(slug) <= 48
    assert slug == canonical(LONG_TITLE, fallback="direct-demo")[:48]
