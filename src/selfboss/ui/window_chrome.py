"""Native window chrome helpers for LoopGuard UI windows."""

from __future__ import annotations

import sys
from typing import Final

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget

from selfboss.packaging_support import app_resource_path


_DWMWA_USE_IMMERSIVE_DARK_MODE: Final[tuple[int, ...]] = (20, 19)
_DWMWA_BORDER_COLOR: Final[int] = 34
_DWMWA_CAPTION_COLOR: Final[int] = 35
_DWMWA_TEXT_COLOR: Final[int] = 36

_CAPTION_COLOR: Final[str] = "#10131A"
_BORDER_COLOR: Final[str] = "#2A303A"
_TEXT_COLOR: Final[str] = "#E6EAF0"
_APP_ICON_PATH: Final[tuple[str, ...]] = ("assets", "icons", "loopguard.png")
_APP_USER_MODEL_ID: Final[str] = "LoopGuard.App"


def set_windows_app_user_model_id(app_id: str = _APP_USER_MODEL_ID) -> bool:
    """Set the Windows taskbar AppUserModelID before creating top-level windows."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            app_id
        )
        return result == 0
    except Exception:
        return False


def loopguard_app_icon() -> QIcon:
    """Return the bundled LoopGuard icon, or a null icon if unavailable."""
    icon_path = app_resource_path(*_APP_ICON_PATH)
    if not icon_path.exists():
        return QIcon()
    return QIcon(str(icon_path))


def apply_loopguard_window_icon(widget: QWidget) -> bool:
    """Apply the LoopGuard window icon when the bundled asset is available."""
    icon = loopguard_app_icon()
    if icon.isNull():
        return False
    widget.setWindowIcon(icon)
    return True


def prepare_dialog_window(widget: QWidget) -> None:
    """Apply LoopGuard dialog icon and dark titlebar best-effort styling."""
    apply_loopguard_window_icon(widget)
    QTimer.singleShot(0, lambda: apply_dark_window_chrome(widget))


def apply_dark_window_chrome(widget: QWidget) -> bool:
    """Best-effort Windows DWM dark titlebar styling.

    The helper intentionally keeps the native frame and no-ops on unsupported
    platforms or Windows builds.
    """
    if sys.platform != "win32":
        return False

    try:
        import ctypes
        from ctypes import wintypes

        hwnd = int(widget.winId())
        if hwnd == 0:
            return False

        dwmapi = ctypes.windll.dwmapi
        hwnd_value = wintypes.HWND(hwnd)
        changed = False

        dark_enabled = ctypes.c_int(1)
        for attribute in _DWMWA_USE_IMMERSIVE_DARK_MODE:
            result = dwmapi.DwmSetWindowAttribute(
                hwnd_value,
                ctypes.c_uint(attribute),
                ctypes.byref(dark_enabled),
                ctypes.sizeof(dark_enabled),
            )
            changed = changed or result == 0

        for attribute, color in (
            (_DWMWA_CAPTION_COLOR, _colorref(_CAPTION_COLOR)),
            (_DWMWA_BORDER_COLOR, _colorref(_BORDER_COLOR)),
            (_DWMWA_TEXT_COLOR, _colorref(_TEXT_COLOR)),
        ):
            color_value = ctypes.c_int(color)
            result = dwmapi.DwmSetWindowAttribute(
                hwnd_value,
                ctypes.c_uint(attribute),
                ctypes.byref(color_value),
                ctypes.sizeof(color_value),
            )
            changed = changed or result == 0

        return changed
    except Exception:
        return False


def _colorref(hex_color: str) -> int:
    """Convert #RRGGBB to Windows COLORREF 0x00bbggrr."""
    value = hex_color.removeprefix("#")
    red = int(value[0:2], 16)
    green = int(value[2:4], 16)
    blue = int(value[4:6], 16)
    return red | (green << 8) | (blue << 16)
