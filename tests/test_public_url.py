"""Tests for the address ElevenLabs uses to reach the webhook."""

from __future__ import annotations

import pytest
from homeassistant.helpers.network import NoURLAvailableError

from custom_components.elevenagents.const import OPT_PUBLIC_URL
from custom_components.elevenagents.public_url import (
    entry_webhook_url,
    normalize_public_url,
    webhook_url,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://hooks.example.com", "https://hooks.example.com"),
        ("https://hooks.example.com/", "https://hooks.example.com"),
        ("  https://hooks.example.com  ", "https://hooks.example.com"),
        ("https://hooks.example.com:8443", "https://hooks.example.com:8443"),
        ("HTTPS://Hooks.Example.com", "https://hooks.example.com"),
    ],
)
def test_a_bare_https_origin_is_accepted(value, expected):
    """A scheme, a host and an optional port make an origin."""
    assert normalize_public_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "hooks.example.com",
        "http://hooks.example.com",
        "https://hooks.example.com/ha",
        "https://hooks.example.com/api/webhook/abc",
        "https://hooks.example.com?a=1",
        "https://hooks.example.com#top",
        "https://user:pw@hooks.example.com",
        "https://",
        "https://hooks.example.com:notaport",
    ],
)
def test_anything_but_a_bare_https_origin_is_refused(value):
    """The webhook path is appended, so a path of our own would break it."""
    assert normalize_public_url(value) is None


async def test_the_public_url_takes_the_webhook_path(hass):
    """The address replaces the external URL of Home Assistant."""
    await hass.config.async_update(external_url="https://home.example")

    url = webhook_url(hass, "abc", "https://hooks.example.com")

    assert url == "https://hooks.example.com/api/webhook/abc"


async def test_without_a_public_url_the_external_url_is_used(hass):
    """An empty field keeps the behaviour of earlier versions."""
    await hass.config.async_update(external_url="https://home.example")

    assert webhook_url(hass, "abc", None) == "https://home.example/api/webhook/abc"


async def test_a_public_url_needs_no_external_url(hass):
    """A Home Assistant reachable only on a private network still gets a URL."""
    await hass.config.async_update(external_url=None)

    url = webhook_url(hass, "abc", "https://hooks.example.com")

    assert url == "https://hooks.example.com/api/webhook/abc"


async def test_neither_address_raises(hass):
    """No address means no tool, as before."""
    await hass.config.async_update(
        internal_url="http://192.168.1.10:8123", external_url=None
    )

    with pytest.raises(NoURLAvailableError):
        webhook_url(hass, "abc", None)


async def test_the_entry_supplies_both_values(hass, config_entry):
    """The webhook id comes from data and the address from options."""
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_PUBLIC_URL: "https://hooks.example.com"}
    )

    assert (
        entry_webhook_url(hass, config_entry)
        == "https://hooks.example.com/api/webhook/webhook_test"
    )
