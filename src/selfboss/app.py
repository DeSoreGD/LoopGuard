"""Qt application runner for LoopGuard."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import BinaryIO

from PySide6.QtCore import QIODevice
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from selfboss.config import is_production_app_mode, load_settings
from selfboss.core.models import EnforcementMode
from selfboss.core.use_cases import SelfBossAppService
from selfboss.data.db import initialize_from_settings
from selfboss.data.repositories import (
    AppSettingsRepository,
    DayStateRepository,
    HighSessionRepository,
    RewardLedgerRepository,
    RuleRepository,
    TaskRepository,
)
from selfboss.ui.main_window import MainWindow
from selfboss.ui.tray import TrayController
from selfboss.ui.window_chrome import (
    loopguard_app_icon,
    set_windows_app_user_model_id,
)

_SINGLE_INSTANCE_SHOW_MESSAGE = b"show\n"
_SINGLE_INSTANCE_TIMEOUT_MS = 500
_SINGLE_INSTANCE_LOCK_FILENAME = "loopguard.instance.lock"


def run(argv: list[str] | None = None) -> int:
    """Launch the PySide6 Widgets application."""
    args = argv if argv is not None else sys.argv
    set_windows_app_user_model_id()
    app = QApplication.instance()
    if app is None:
        app = QApplication(args)

    app.setApplicationName("LoopGuard")
    app.setQuitOnLastWindowClosed(False)
    icon = loopguard_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    settings = load_settings(create_dirs=True)
    single_instance_server = acquire_single_instance_server(settings.app_home)
    if single_instance_server is None:
        return 0

    connection = initialize_from_settings(settings)
    app_settings = AppSettingsRepository(connection)
    production_mode = is_production_app_mode()
    if production_mode:
        app_settings.ensure_enforcement_mode_if_missing(
            EnforcementMode.FULL_ENFORCEMENT.value
        )
    service = SelfBossAppService(
        settings=settings,
        tasks=TaskRepository(connection),
        day_state=DayStateRepository(connection),
        rewards=RewardLedgerRepository(connection),
        high_sessions=HighSessionRepository(connection),
        rules=RuleRepository(connection),
        app_settings=app_settings,
    )
    service.run_real_hosts_blocking_cycle(force=True)

    app.aboutToQuit.connect(connection.close)

    window = MainWindow(
        settings=settings,
        service=service,
        production_mode=production_mode,
    )
    window.database_connection = connection
    tray = TrayController(window=window, settings=settings, app=app)

    window.tray_controller = tray
    tray.show()
    window.show()
    _wire_single_instance_show(single_instance_server, window)
    app._loopguard_single_instance_server = single_instance_server

    return app.exec()


def single_instance_server_name(app_home: Path) -> str:
    """Return the profile-specific local server name for one LoopGuard instance."""
    normalized = str(app_home.expanduser().resolve()).casefold()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"loopguard-{digest}"


def acquire_single_instance_server(app_home: Path) -> QLocalServer | None:
    """Listen for this profile, or notify the already-running instance."""
    server_name = single_instance_server_name(app_home)
    profile_lock = _try_acquire_profile_lock(app_home)
    if profile_lock is None:
        _notify_existing_instance(server_name)
        return None

    server = QLocalServer()
    if server.listen(server_name):
        _attach_profile_lock(server, profile_lock)
        return server
    if _notify_existing_instance(server_name):
        _release_profile_lock(profile_lock)
        return None

    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    if server.listen(server_name):
        _attach_profile_lock(server, profile_lock)
        return server
    if _notify_existing_instance(server_name):
        _release_profile_lock(profile_lock)
        return None
    _release_profile_lock(profile_lock)
    raise RuntimeError(f"Could not create LoopGuard single-instance lock: {server.errorString()}")


def _try_acquire_profile_lock(app_home: Path) -> BinaryIO | None:
    """Acquire an OS lock that prevents split instances if socket notify fails."""
    app_home.mkdir(parents=True, exist_ok=True)
    lock_handle = (app_home / _SINGLE_INSTANCE_LOCK_FILENAME).open("a+b")
    lock_handle.seek(0, os.SEEK_END)
    if lock_handle.tell() == 0:
        lock_handle.write(b"0")
        lock_handle.flush()
    lock_handle.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_handle
    except OSError:
        lock_handle.close()
        return None


def _release_profile_lock(lock_handle: BinaryIO) -> None:
    try:
        lock_handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock_handle.close()


def _attach_profile_lock(server: QLocalServer, lock_handle: BinaryIO) -> None:
    server._loopguard_profile_lock = lock_handle


def _notify_existing_instance(server_name: str) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(
        server_name,
        QIODevice.OpenModeFlag.WriteOnly,
    )
    if not socket.waitForConnected(_SINGLE_INSTANCE_TIMEOUT_MS):
        return False
    socket.write(_SINGLE_INSTANCE_SHOW_MESSAGE)
    socket.flush()
    socket.waitForBytesWritten(_SINGLE_INSTANCE_TIMEOUT_MS)
    socket.disconnectFromServer()
    return True


def _wire_single_instance_show(server: QLocalServer, window: MainWindow) -> None:
    def show_existing_window() -> None:
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            socket.readAll()
            socket.disconnectFromServer()
        window.show_and_raise()

    server.newConnection.connect(show_existing_window)
    show_existing_window()
