"""The elevenlabs requirement agrees with the version Home Assistant pins."""

from __future__ import annotations

import json
from pathlib import Path

import homeassistant
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

MANIFEST = Path("custom_components/elevenlabs_agent/manifest.json")
TEST_REQUIREMENTS = Path("requirements_test.txt")
CORE_MANIFEST = (
    Path(homeassistant.__file__).parent / "components" / "elevenlabs" / "manifest.json"
)


def _elevenlabs(requirements: list[str]) -> Requirement:
    """Return the elevenlabs requirement from a list of requirement strings."""
    return next(
        Requirement(line)
        for line in requirements
        if Requirement(line).name == "elevenlabs"
    )


def _core_pin() -> Version:
    """Return the exact elevenlabs version the installed Home Assistant pins."""
    data = json.loads(CORE_MANIFEST.read_text())
    (specifier,) = _elevenlabs(data["requirements"]).specifier
    assert specifier.operator == "=="
    return Version(specifier.version)


def test_the_manifest_accepts_the_version_home_assistant_pins() -> None:
    """A requirement Home Assistant's own pin does not satisfy breaks one install."""
    ours = _elevenlabs(json.loads(MANIFEST.read_text())["requirements"])

    assert ours.specifier.contains(_core_pin())


def test_the_tests_run_on_the_version_home_assistant_pins() -> None:
    """CI tests the elevenlabs version users get with this Home Assistant."""
    lines = [
        line.strip()
        for line in TEST_REQUIREMENTS.read_text().splitlines()
        if line.strip().startswith("elevenlabs")
    ]

    assert _elevenlabs(lines).specifier == SpecifierSet(f"=={_core_pin()}")
