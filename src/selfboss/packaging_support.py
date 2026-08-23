"""Helpers for locating bundled resources in source and PyInstaller builds."""

from __future__ import annotations

import sys
from pathlib import Path


def app_resource_root() -> Path:
    """Return the root that contains bundled app resources."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass).resolve()
    return Path(__file__).resolve().parents[2]


def app_resource_path(*parts: str) -> Path:
    """Return a path under the source checkout or PyInstaller resource root."""
    return app_resource_root().joinpath(*parts)


def browser_extension_folder() -> Path:
    """Return the unpacked Chrome extension folder."""
    return app_resource_path("browser_extension", "chrome_mv3")


def recovery_scripts_folder() -> Path:
    """Return the folder containing emergency recovery scripts."""
    return app_resource_path("scripts")


def native_messaging_folder() -> Path:
    """Return the folder containing native messaging registration helpers."""
    return app_resource_path("packaging", "native_messaging")
