"""Regression tests: a signal-raced WhatsApp bridge exit during gateway shutdown.

Issue #127047 — when a supervisor (s6, systemd, ``docker stop``) SIGTERMs the
gateway, the signal is delivered to the whole process group: the Node bridge
child dies with ``-15`` *before* the gateway's stop flow reaches
``WhatsAppAdapter.disconnect()`` (the only place ``_shutting_down`` flips).
``_check_managed_bridge_exit()`` therefore read the race-window ``-15`` as a
fatal adapter error and the gateway crash-looped, taking Telegram down too.

The runner's signal handler flips ``_stop_requested_by_signal`` before any
stop work runs (``gateway/run.py``), so the adapter can consult it to cover
the window between signal delivery and ``disconnect()`` — without blanket
ignoring ``-15`` (a bridge killed while the gateway keeps running must stay
a retryable fatal so the reconnect watcher revives it).
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform


def _make_adapter():
    """Create a WhatsAppAdapter with test attributes (bypass __init__)."""
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

    adapter = WhatsAppAdapter.__new__(WhatsAppAdapter)
    adapter.platform = Platform.WHATSAPP
    adapter.config = MagicMock()
    adapter._bridge_port = 19876
    adapter._bridge_script = "/tmp/test-bridge.js"
    adapter._session_path = Path("/tmp/test-wa-session")
    adapter._bridge_log_fh = None
    adapter._bridge_log = None
    adapter._bridge_process = None
    adapter._running = False
    adapter._message_handler = None
    adapter._fatal_error_code = None
    adapter._fatal_error_message = None
    adapter._fatal_error_retryable = True
    adapter._fatal_error_handler = None
    adapter._message_queue = asyncio.Queue()
    adapter._http_session = None
    return adapter


def _race_window_adapter(returncode, *, runner_signal_stop=False):
    """Adapter as the poll loop sees it in the race window: bridge already
    reaped with ``returncode``, ``disconnect()`` not yet run (no
    ``_shutting_down`` attribute), runner flag per ``runner_signal_stop``."""
    adapter = _make_adapter()
    mock_proc = MagicMock()
    mock_proc.poll.return_value = returncode
    adapter._bridge_process = mock_proc
    adapter.gateway_runner = SimpleNamespace(_stop_requested_by_signal=runner_signal_stop)
    return adapter


class TestSignalRacedBridgeExit:
    @pytest.mark.asyncio
    async def test_sigterm_during_runner_signal_stop_is_not_fatal(self):
        """The race window itself: -15 observed after the runner's signal
        handler ran but before disconnect() flips _shutting_down."""
        adapter = _race_window_adapter(-15, runner_signal_stop=True)
        fatal_handler = AsyncMock()
        adapter.set_fatal_error_handler(fatal_handler)

        result = await adapter._check_managed_bridge_exit()

        assert result is None
        assert adapter.fatal_error_code is None
        fatal_handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sigterm_after_disconnect_stays_nonfatal(self):
        """Original ordered path preserved: _shutting_down alone still covers."""
        adapter = _race_window_adapter(-15, runner_signal_stop=False)
        adapter._shutting_down = True
        fatal_handler = AsyncMock()
        adapter.set_fatal_error_handler(fatal_handler)

        assert await adapter._check_managed_bridge_exit() is None
        assert adapter.fatal_error_code is None
        fatal_handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sigterm_without_any_shutdown_signal_stays_fatal(self):
        """Genuine crash preserved: bridge killed while the gateway keeps
        running is still a retryable fatal so the watcher revives it."""
        adapter = _race_window_adapter(-15, runner_signal_stop=False)
        fatal_handler = AsyncMock()
        adapter.set_fatal_error_handler(fatal_handler)

        result = await adapter._check_managed_bridge_exit()

        assert result is not None
        assert adapter.fatal_error_code == "whatsapp_bridge_exited"
        assert adapter.fatal_error_retryable is True
        fatal_handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_nonzero_exit_during_signal_stop_stays_fatal(self):
        """The runner flag only covers intentional signal exits (0/-2/-15);
        a real crash code during shutdown is still reported."""
        adapter = _race_window_adapter(1, runner_signal_stop=True)
        fatal_handler = AsyncMock()
        adapter.set_fatal_error_handler(fatal_handler)

        result = await adapter._check_managed_bridge_exit()

        assert result is not None
        assert adapter.fatal_error_code == "whatsapp_bridge_exited"
        fatal_handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sigint_during_runner_signal_stop_is_not_fatal(self):
        """Ctrl-C / SIGINT (-2) races the same way as SIGTERM."""
        adapter = _race_window_adapter(-2, runner_signal_stop=True)
        fatal_handler = AsyncMock()
        adapter.set_fatal_error_handler(fatal_handler)

        assert await adapter._check_managed_bridge_exit() is None
        assert adapter.fatal_error_code is None
        fatal_handler.assert_not_awaited()
