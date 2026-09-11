"""
Every key the code uses must exist in both translation files.

hassfest validates translations for core integrations only, so nothing
else catches a key that reaches the user as a bare slug.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

TRANSLATIONS = Path("custom_components/elevenlabs_agent/translations")
SOURCE = Path("custom_components/elevenlabs_agent")


def _load(language: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((TRANSLATIONS / f"{language}.json").read_text())
    return loaded


def _flat(node: dict[str, Any], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in node.items():
        keys.add(f"{prefix}{key}")
        if isinstance(value, dict):
            keys |= _flat(value, f"{prefix}{key}.")
    return keys


def test_english_and_spanish_carry_the_same_keys() -> None:
    """A missing translation shows the English string, or the raw key."""
    assert _flat(_load("en")) == _flat(_load("es"))


@pytest.mark.parametrize("language", ["en", "es"])
def test_every_flow_error_and_abort_has_text(language: str) -> None:
    """Codes the config flow can produce must all resolve to a sentence."""
    source = (SOURCE / "config_flow.py").read_text()
    strings = _load(language)["config"]

    used = set(re.findall(r'errors\["base"\] = "([a-z_]+)"', source))
    used |= set(re.findall(r'async_abort\(\s*reason="([a-z_]+)"', source))

    missing = {
        code
        for code in used
        if code not in strings.get("error", {}) and code not in strings.get("abort", {})
    }
    assert not missing, f"{language}.json has no text for {sorted(missing)}"


@pytest.mark.parametrize("language", ["en", "es"])
def test_every_repair_issue_has_text(language: str) -> None:
    """An issue without text shows its key in the repairs panel."""
    const = (SOURCE / "const.py").read_text()
    issues = set(
        re.findall(r'^ISSUE_[A-Z_]+: Final = "([a-z_]+)"', const, re.MULTILINE)
    )
    documented = set(_load(language).get("issues", {}))

    assert issues <= documented, (
        f"{language}.json has no text for {sorted(issues - documented)}"
    )


@pytest.mark.parametrize("language", ["en", "es"])
def test_every_flow_step_has_a_title(language: str) -> None:
    """A step without a title renders with none at all."""
    source = (SOURCE / "config_flow.py").read_text()
    steps = set(re.findall(r'step_id="([a-z_]+)"', source))
    documented = _load(language)["config"]["step"]

    for step in steps:
        assert step in documented, f"{language}.json has no step {step}"
        assert documented[step].get("title"), (
            f"{language}.json step {step} has no title"
        )


@pytest.mark.parametrize("language", ["en", "es"])
def test_an_issue_is_fixable_or_informative_but_not_both(language: str) -> None:
    """
    Hassfest treats description and fix_flow as mutually exclusive.

    MEASURED 12 September 2026: shipping both raised "two or more values in
    the same group of exclusion 'fixable'". A fixable issue puts its text in
    the fix flow's own step, where the user reads it before confirming.
    """
    for name, issue in _load(language).get("issues", {}).items():
        assert not ("description" in issue and "fix_flow" in issue), (
            f"{language}.json issue {name} carries both"
        )
        assert "description" in issue or "fix_flow" in issue, (
            f"{language}.json issue {name} carries neither"
        )
