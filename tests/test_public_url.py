"""Tests for the URL ElevenLabs uses to reach the webhook."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.helpers.network import NoURLAvailableError

from custom_components.elevenagents.const import OPT_PUBLIC_URL
from custom_components.elevenagents.public_url import (
    INVALID_PUBLIC_URL,
    NO_PUBLIC_URL,
    PublicUrlError,
    entry_webhook_url,
    ha_origin,
    normalize_public_url,
    public_url_option,
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
    """The public URL replaces the external URL of Home Assistant."""
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
    """No URL means no tool, as before."""
    await hass.config.async_update(
        internal_url="http://192.168.1.10:8123", external_url=None
    )

    with pytest.raises(NoURLAvailableError):
        webhook_url(hass, "abc", None)


async def test_the_entry_supplies_both_values(hass, config_entry):
    """The webhook id comes from data and the URL from options."""
    hass.config_entries.async_update_entry(
        config_entry, options={OPT_PUBLIC_URL: "https://hooks.example.com"}
    )

    assert (
        entry_webhook_url(hass, config_entry)
        == "https://hooks.example.com/api/webhook/webhook_test"
    )


async def test_ha_origin_is_the_external_url(hass):
    """The webhook uses the external URL when no public URL is set."""
    await hass.config.async_update(external_url="https://home.example")

    assert ha_origin(hass) == "https://home.example"


async def test_ha_origin_is_none_without_an_external_url(hass):
    """An internal URL never reaches ElevenLabs."""
    await hass.config.async_update(
        internal_url="http://192.168.1.10:8123", external_url=None
    )

    assert ha_origin(hass) is None


async def test_ha_origin_asks_for_the_url_the_webhook_uses(hass):
    """A cloud URL is never written into the tools, so it does not count."""
    with patch(
        "custom_components.elevenagents.public_url.get_url",
        return_value="https://home.example/",
    ) as get_url:
        assert ha_origin(hass) == "https://home.example"

    assert get_url.call_args.kwargs == {
        "allow_internal": False,
        "allow_cloud": False,
        "prefer_external": True,
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", None),
        ("   ", None),
        ("https://home.example", None),
        ("https://Home.example/", None),
        ("https://hooks.example.com/", "https://hooks.example.com"),
    ],
)
async def test_public_url_option(hass, value, expected):
    """Empty and the Home Assistant URL store nothing; another origin is stored."""
    await hass.config.async_update(external_url="https://home.example")

    assert public_url_option(hass, value) == expected


async def test_an_http_home_assistant_url_left_as_it_is_stores_nothing(hass):
    """The prefilled value passes even when it is not https."""
    await hass.config.async_update(external_url="http://home.example:8123")

    assert public_url_option(hass, "http://home.example:8123") is None


async def test_a_value_with_a_path_is_refused(hass):
    """The webhook path is appended, so a path of our own would break it."""
    await hass.config.async_update(external_url="https://home.example")

    with pytest.raises(PublicUrlError) as err:
        public_url_option(hass, "https://hooks.example.com/ha")

    assert err.value.code == INVALID_PUBLIC_URL


async def test_an_empty_value_without_an_external_url_is_refused(hass):
    """Without either URL every tool call would fail."""
    await hass.config.async_update(
        internal_url="http://192.168.1.10:8123", external_url=None
    )

    with pytest.raises(PublicUrlError) as err:
        public_url_option(hass, "  ")

    assert err.value.code == NO_PUBLIC_URL


async def test_another_origin_needs_no_external_url(hass):
    """A proxy URL alone is enough."""
    await hass.config.async_update(
        internal_url="http://192.168.1.10:8123", external_url=None
    )

    assert public_url_option(hass, "https://hooks.example.com") == (
        "https://hooks.example.com"
    )
