"""The requirements agree with the Home Assistant versions the tests run on."""

from __future__ import annotations

import json
from pathlib import Path

import homeassistant
import pytest
from homeassistant.const import __version__ as ha_version
from packaging.requirements import Requirement
from packaging.version import Version

MANIFEST = Path("custom_components/elevenagents/manifest.json")
HACS = Path("hacs.json")
NEWEST_REQUIREMENTS = Path("requirements_test.txt")
MINIMUM_REQUIREMENTS = Path("requirements_test_minimum.txt")
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


def _lines(path: Path) -> list[str]:
    """Return the requirement lines of a requirements file."""
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith(("#", "-r"))
    ]


def _pin(path: Path, name: str) -> Version:
    """Return the exact version a requirements file pins for a package."""
    (requirement,) = [
        Requirement(line) for line in _lines(path) if Requirement(line).name == name
    ]
    (specifier,) = requirement.specifier
    assert specifier.operator == "=="
    return Version(specifier.version)


def _installed_requirements() -> Path:
    """Return the requirements file of the installed Home Assistant."""
    for path in (NEWEST_REQUIREMENTS, MINIMUM_REQUIREMENTS):
        if _pin(path, "homeassistant") == Version(ha_version):
            return path
    pytest.skip(f"no requirements file pins homeassistant {ha_version}")


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
    assert _pin(_installed_requirements(), "elevenlabs") == _core_pin()


def test_the_minimum_tests_run_on_the_version_hacs_declares() -> None:
    """CI tests the oldest Home Assistant a user may install this on."""
    declared = Version(json.loads(HACS.read_text())["homeassistant"])

    assert _pin(MINIMUM_REQUIREMENTS, "homeassistant") == declared


def test_the_newest_tests_run_on_the_development_version() -> None:
    """CI tests the Home Assistant version development runs."""
    assert _pin(NEWEST_REQUIREMENTS, "homeassistant") == _pin(
        Path("requirements_dev.txt"), "homeassistant"
    )
