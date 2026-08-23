"""Local browser setup helpers for explicit user-triggered actions."""

from __future__ import annotations

import re
import os
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from selfboss.packaging_support import app_resource_root


EXTENSION_ID_RE = re.compile(r"^[a-p]{32}$")
NATIVE_HOST_NAME = "com.selfboss.native_host"
LOOPGUARD_CHROME_EXTENSION_ID = "mcpljcfiphfoapmohiahhfjgcenhckkh"
LOOPGUARD_CHROME_EXTENSION_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAr8iSRMGoTnV/Cirr7AiFgQCzoB8Z8VB8BHUqW9qj7pK3Ul0nMrk2M3s2MzeZSoBBEQ+bRaIjotkDqWn9I2rg1wtjMIDwSctFV4KVgjvtvap5MJdZDDzL6l+8MSBJgQzHA+5bFHpCn7rVAa3kLEAWzgl2E38a90u3G7a0IcJFAeBCD36EIy6WHJoECBhQWC0a16eVs/3aOWgsSEu7DRjOnqePavCGLroB23qKk65AT+oBsEWwk4JekFulIVw92ULYeUG40oURDkxPBnf+SiHkm88cQTrDWnPvGTpaFjjWY60NQIDAQAB"
)


@dataclass(frozen=True)
class NativeHostRegistrationResult:
    """Compact result for Settings UI display."""

    ok: bool
    reason: str


@dataclass(frozen=True)
class BrowserSetupActionResult:
    """Compact result for local setup helper actions."""

    ok: bool
    reason: str
    copy_text: str = ""


Runner = Callable[..., Any]
Launcher = Callable[[list[str]], Any]
RegistrySetter = Callable[[str, str], None]
RegistryDeleter = Callable[[str], None]


def normalize_extension_id(value: str) -> str:
    """Return a normalized Chrome extension ID or raise ValueError."""
    normalized = value.strip().lower()
    if not EXTENSION_ID_RE.fullmatch(normalized):
        raise ValueError(
            "Extension ID must be 32 Chrome extension characters from a to p."
        )
    return normalized


def open_chrome_extensions_page(
    *,
    chrome_finder: Callable[[], Path | None] | None = None,
    launcher: Launcher | None = None,
) -> BrowserSetupActionResult:
    """Open Chrome's extensions page without using the OS chrome:// handler."""
    chrome_path = (chrome_finder or find_chrome_executable)()
    if chrome_path is None:
        return BrowserSetupActionResult(
            ok=False,
            reason="Open Chrome and paste: chrome://extensions",
            copy_text="chrome://extensions",
        )
    try:
        (launcher or _launch_process)([str(chrome_path), "chrome://extensions"])
    except Exception:
        return BrowserSetupActionResult(
            ok=False,
            reason="Open Chrome and paste: chrome://extensions",
            copy_text="chrome://extensions",
        )
    return BrowserSetupActionResult(
        ok=True,
        reason="Opened Chrome Extensions page.",
    )


def open_extension_folder(
    path: Path,
    *,
    launcher: Launcher | None = None,
) -> BrowserSetupActionResult:
    """Open the unpacked extension folder in File Explorer."""
    folder = path.resolve()
    if not folder.exists():
        return BrowserSetupActionResult(
            ok=False,
            reason=f"Open this folder manually: {folder}",
            copy_text=str(folder),
        )
    try:
        (launcher or _launch_process)(["explorer.exe", str(folder)])
    except Exception:
        return BrowserSetupActionResult(
            ok=False,
            reason=f"Open this folder manually: {folder}",
            copy_text=str(folder),
        )
    return BrowserSetupActionResult(
        ok=True,
        reason=f"Opened extension folder: {folder}",
    )


def find_chrome_executable() -> Path | None:
    """Return a likely Chrome executable path without launching it."""
    for candidate in _chrome_executable_candidates():
        if candidate.exists():
            return candidate
    chrome_from_path = shutil.which("chrome.exe") or shutil.which("chrome")
    if chrome_from_path:
        return Path(chrome_from_path)
    return None


class BrowserSetupRegistrar:
    """Run the explicit native-host registration helper on demand."""

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        runner: Runner | None = None,
        manifest_dir: Path | None = None,
        native_host_path: Path | None = None,
        registry_setter: RegistrySetter | None = None,
        registry_deleter: RegistryDeleter | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.repo_root = (repo_root or _repo_root()).resolve()
        self.runner = runner or subprocess.run
        self.manifest_dir = manifest_dir
        self.native_host_path = native_host_path or _default_native_host_path()
        self.registry_setter = registry_setter or _set_chrome_native_host_registry
        self.registry_deleter = registry_deleter or _delete_chrome_native_host_registry
        self.timeout_seconds = timeout_seconds

    def register_native_host(
        self,
        *,
        browser: str,
        extension_id: str | None = None,
    ) -> NativeHostRegistrationResult:
        return self.repair_native_host(browser=browser, extension_id=extension_id)

    def repair_native_host(
        self,
        *,
        browser: str,
        extension_id: str | None = None,
    ) -> NativeHostRegistrationResult:
        normalized_browser = browser.strip().lower()
        if normalized_browser != "chrome":
            return NativeHostRegistrationResult(
                ok=False,
                reason="Only Chrome native host registration is supported in this patch.",
            )
        try:
            normalized_extension_id = normalize_extension_id(
                extension_id or LOOPGUARD_CHROME_EXTENSION_ID
            )
        except ValueError as error:
            return NativeHostRegistrationResult(ok=False, reason=str(error))

        if self.native_host_path is not None:
            return self._repair_chrome_native_host_direct(
                extension_id=normalized_extension_id,
                native_host_path=self.native_host_path,
            )

        command = _registration_command(
            repo_root=self.repo_root,
            browser=normalized_browser,
            extension_id=normalized_extension_id,
        )
        try:
            completed = self.runner(
                command,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except Exception as error:
            return NativeHostRegistrationResult(
                ok=False,
                reason=f"Native host registration failed: {error}",
            )

        return_code = getattr(completed, "returncode", 1)
        if return_code == 0:
            return NativeHostRegistrationResult(
                ok=True,
                reason=(
                    "Native host registered. Reload the extension, "
                    "then Refresh status."
                ),
            )
        detail = _compact_process_output(
            getattr(completed, "stderr", ""),
            getattr(completed, "stdout", ""),
        )
        return NativeHostRegistrationResult(
            ok=False,
            reason=f"Native host registration failed: {detail or f'exit {return_code}'}",
            )

    def unregister_native_host(
        self,
        *,
        browser: str,
    ) -> NativeHostRegistrationResult:
        normalized_browser = browser.strip().lower()
        if normalized_browser != "chrome":
            return NativeHostRegistrationResult(
                ok=False,
                reason="Only Chrome native host registration is supported in this patch.",
            )
        if self.native_host_path is not None:
            try:
                self.registry_deleter(_chrome_registry_subkey())
            except Exception as error:
                return NativeHostRegistrationResult(
                    ok=False,
                    reason=f"Native host unregister failed: {error}",
                )
            return NativeHostRegistrationResult(
                ok=True,
                reason=(
                    "Native host unregistered. Browser integration will be "
                    "disconnected until registered again."
                ),
            )
        command = _unregistration_command(
            repo_root=self.repo_root,
            browser=normalized_browser,
        )
        try:
            completed = self.runner(
                command,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except Exception as error:
            return NativeHostRegistrationResult(
                ok=False,
                reason=f"Native host unregister failed: {error}",
            )

        return_code = getattr(completed, "returncode", 1)
        if return_code == 0:
            return NativeHostRegistrationResult(
                ok=True,
                reason=(
                    "Native host unregistered. Browser integration will be "
                    "disconnected until registered again."
                ),
            )
        detail = _compact_process_output(
            getattr(completed, "stderr", ""),
            getattr(completed, "stdout", ""),
        )
        return NativeHostRegistrationResult(
            ok=False,
            reason=f"Native host unregister failed: {detail or f'exit {return_code}'}",
        )

    def _repair_chrome_native_host_direct(
        self,
        *,
        extension_id: str,
        native_host_path: Path,
    ) -> NativeHostRegistrationResult:
        if not native_host_path.exists():
            return NativeHostRegistrationResult(
                ok=False,
                reason=f"Native host executable not found: {native_host_path}",
            )
        manifest_dir = (
            self.manifest_dir
            or _default_native_manifest_dir()
        )
        manifest_path = manifest_dir / f"{NATIVE_HOST_NAME}.chrome.json"
        manifest = build_native_host_manifest(
            extension_id=extension_id,
            native_host_path=native_host_path,
        )
        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=False) + "\n",
                encoding="utf-8",
            )
            self.registry_setter(_chrome_registry_subkey(), str(manifest_path))
        except Exception as error:
            return NativeHostRegistrationResult(
                ok=False,
                reason=f"Native host registration failed: {error}",
            )
        return NativeHostRegistrationResult(
            ok=True,
            reason="Native host registered. Reload the extension, then Refresh status.",
        )


def build_native_host_manifest(
    *,
    extension_id: str = LOOPGUARD_CHROME_EXTENSION_ID,
    native_host_path: Path,
) -> dict[str, object]:
    """Build the Chrome Native Messaging manifest for LoopGuard."""
    return {
        "name": NATIVE_HOST_NAME,
        "description": "LoopGuard Native Messaging host",
        "path": str(native_host_path.resolve()),
        "type": "stdio",
        "allowed_origins": [
            f"chrome-extension://{normalize_extension_id(extension_id)}/"
        ],
    }


def _registration_command(
    *,
    repo_root: Path,
    browser: str,
    extension_id: str,
) -> list[str]:
    return [
        sys.executable,
        str(repo_root / "packaging" / "native_messaging" / "register_native_host.py"),
        "--browser",
        browser,
        "--extension-id",
        extension_id,
        "--repo-root",
        str(repo_root),
        "--install",
    ]


def _unregistration_command(
    *,
    repo_root: Path,
    browser: str,
) -> list[str]:
    return [
        sys.executable,
        str(repo_root / "packaging" / "native_messaging" / "register_native_host.py"),
        "--browser",
        browser,
        "--repo-root",
        str(repo_root),
        "--uninstall",
    ]


def _chrome_executable_candidates() -> Iterable[Path]:
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            yield Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"


def _launch_process(command: list[str]) -> None:
    subprocess.Popen(command)


def _repo_root() -> Path:
    return app_resource_root()


def _default_native_host_path() -> Path | None:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).with_name("LoopGuardNativeHost.exe")
    return None


def _default_native_manifest_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "LoopGuard" / "native_messaging"
    return Path.home() / "AppData" / "Local" / "LoopGuard" / "native_messaging"


def _chrome_registry_subkey() -> str:
    return rf"Software\Google\Chrome\NativeMessagingHosts\{NATIVE_HOST_NAME}"


def _set_chrome_native_host_registry(subkey: str, value: str) -> None:
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, value)


def _delete_chrome_native_host_registry(subkey: str) -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
    except FileNotFoundError:
        return


def _compact_process_output(*parts: object) -> str:
    text = " ".join(str(part or "") for part in parts)
    return " ".join(text.split())[:200]
