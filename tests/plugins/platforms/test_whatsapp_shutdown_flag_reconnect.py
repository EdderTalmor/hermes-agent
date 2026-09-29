"""Regression tests: ``_shutting_down`` must not survive a reconnect (#127047).

``disconnect()`` flips ``_shutting_down`` True, and reconnects reuse the same
adapter object (``run_adapters`` calls ``connect(is_reconnect=True)`` after the
fatal-recovery disconnect). ``connect()`` never cleared the flag, so every
``{0, -2, -15}`` bridge exit after the first reconnect was misread as an
intentional shutdown exit: no fatal queued, no reconnect, the platform silently
stayed dead. ``connect()`` now resets the flag when a new bridge lifecycle begins.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.platforms.whatsapp.adapter import WhatsAppAdapter


def _post_disconnect_adapter() -> WhatsAppAdapter:
    """Adapter in the state ``disconnect()`` leaves behind: flag set, no process."""
    adapter = WhatsAppAdapter.__new__(WhatsAppAdapter)
    adapter.platform = SimpleNamespace(value="whatsapp")
    adapter._bridge_script = "/tmp/test-bridge.js"
    adapter._session_path = Path("/tmp/test-wa-reconnect-session")
    adapter._shutting_down = True
    adapter._running = True
    adapter._bridge_process = None
    adapter._fatal_error_code = None
    adapter._fatal_error_message = None
    adapter._set_fatal_error = MagicMock()
    adapter._notify_fatal_error = AsyncMock()
    adapter._close_bridge_log = MagicMock()
    return adapter


def _reconnect_patches(adapter: WhatsAppAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    import plugins.platforms.whatsapp.adapter as wa_mod

    monkeypatch.setattr(wa_mod, "find_node_executable", lambda name: f"/pm/{name}")
    adapter._preflight = lambda: True
    adapter._acquire_platform_lock = lambda *args: True
    adapter._ensure_bridge_deps = lambda p: True
    adapter._reuse_running_bridge = AsyncMock(return_value=True)


@pytest.mark.asyncio
async def test_reconnect_clears_stale_shutdown_flag(monkeypatch):
    """The race-window complement: after disconnect + reconnect the flag is False."""
    adapter = _post_disconnect_adapter()
    _reconnect_patches(adapter, monkeypatch)
    assert await adapter.connect(is_reconnect=True) is True
    assert adapter._shutting_down is False


@pytest.mark.asyncio
async def test_post_reconnect_sigterm_exit_is_fatal(monkeypatch):
    """A genuine -15 after a reconnect queues the retryable fatal (revives the platform)."""
    adapter = _post_disconnect_adapter()
    _reconnect_patches(adapter, monkeypatch)
    assert await adapter.connect(is_reconnect=True) is True

    mock_proc = MagicMock()
    mock_proc.poll.return_value = -15
    adapter._bridge_process = mock_proc
    message = await adapter._check_managed_bridge_exit()
    assert message is not None and "exited unexpectedly" in message
    adapter._set_fatal_error.assert_called_once_with(
        "whatsapp_bridge_exited", message, retryable=True
    )
    adapter._notify_fatal_error.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_exit_still_suppressed_without_reconnect():
    """The original flag-gated path is unchanged: exit during shutdown stays silent."""
    adapter = _post_disconnect_adapter()
    mock_proc = MagicMock()
    mock_proc.poll.return_value = -15
    adapter._bridge_process = mock_proc
    assert await adapter._check_managed_bridge_exit() is None
    adapter._set_fatal_error.assert_not_called()
    adapter._notify_fatal_error.assert_not_awaited()
