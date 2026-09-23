"""Agent-side checks that need no network, audio device or COM."""

import pytest

from handlers.apps import _is_private_host
from handlers.process import validate_url


@pytest.mark.parametrize("url", [
    "https://web.telegram.org/a/",
    "http://localhost:8080/",
    "discord://",
    "steam://open/main",
    "ms-settings:sound",
    "https://example.com/?q=1&b=2",
])
def test_open_url_accepts(url):
    assert validate_url(url) is None


@pytest.mark.parametrize("url", [
    "",
    "file:///C:/Windows/System32/calc.exe",
    "javascript:alert(1)",
    "ms-msdt:/id",
    "C:\\Windows\\notepad.exe",
    "http://",
    "https://a b.com",
    'https://example.com/"x',
    "https://example.com/|x",
])
def test_open_url_refuses(url):
    assert validate_url(url) is not None


@pytest.mark.parametrize("host,private", [
    ("127.0.0.1", True),
    ("192.168.1.1", True),
    ("10.0.0.5", True),
    ("169.254.1.1", True),
    ("198.18.0.5", False),  # VPN fake-IP range: public sites resolve here
    ("8.8.8.8", False),
])
def test_lan_addresses_are_refused_for_icon_fetches(host, private):
    assert _is_private_host(host) is private
