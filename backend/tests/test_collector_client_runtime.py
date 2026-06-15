from __future__ import annotations

import http.client
from pathlib import Path
import sys
import urllib.error


COLLECTOR_CLIENT_PATH = Path(__file__).resolve().parents[2] / "collector-client"
if str(COLLECTOR_CLIENT_PATH) not in sys.path:
    sys.path.insert(0, str(COLLECTOR_CLIENT_PATH))

import client as collector_client  # noqa: E402


def test_remote_disconnected_is_retryable_network_error() -> None:
    assert isinstance(
        http.client.RemoteDisconnected("server closed connection"),
        collector_client.NETWORK_RETRY_EXCEPTIONS,
    )


def test_http_error_is_handled_before_retryable_url_error() -> None:
    assert issubclass(urllib.error.HTTPError, collector_client.NETWORK_RETRY_EXCEPTIONS)
    assert collector_client.is_auth_http_error(
        urllib.error.HTTPError("http://server", 401, "unauthorized", {}, None)
    )
