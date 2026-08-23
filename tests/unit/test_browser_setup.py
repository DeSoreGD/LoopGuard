from __future__ import annotations

import subprocess
import sys
import json
import zipfile
from pathlib import Path

import pytest

from selfboss.platform.browser_setup import (
    BrowserSetupRegistrar,
    LOOPGUARD_CHROME_EXTENSION_ID,
    LOOPGUARD_CHROME_EXTENSION_KEY,
    build_native_host_manifest,
    open_chrome_extensions_page,
    open_extension_folder,
    normalize_extension_id,
)
from selfboss.packaging_support import (
    app_resource_path,
    browser_extension_folder,
    recovery_scripts_folder,
)


VALID_EXTENSION_ID = "ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP"
NORMALIZED_EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"


def test_chrome_extension_manifest_has_stable_key() -> None:
    manifest = json.loads(
        (Path.cwd() / "browser_extension" / "chrome_mv3" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["key"] == LOOPGUARD_CHROME_EXTENSION_KEY
    assert LOOPGUARD_CHROME_EXTENSION_ID == "mcpljcfiphfoapmohiahhfjgcenhckkh"


def test_chrome_extension_manifest_is_web_store_metadata_ready() -> None:
    extension_root = Path.cwd() / "browser_extension" / "chrome_mv3"
    manifest = json.loads((extension_root / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 3
    assert manifest["name"] == "LoopGuard"
    assert manifest["short_name"] == "LoopGuard"
    assert manifest["description"] == (
        "Local LoopGuard companion for website blocking and desktop app status."
    )
    assert manifest["version"] == "0.1.0"
    assert "host_permissions" not in manifest
    assert manifest["permissions"] == [
        "nativeMessaging",
        "tabs",
        "alarms",
        "declarativeNetRequest",
    ]
    assert manifest["background"]["service_worker"] == "background.js"
    assert (extension_root / manifest["background"]["service_worker"]).is_file()
    assert manifest["content_scripts"] == [
        {
            "matches": ["*://youtube.com/*", "*://*.youtube.com/*"],
            "js": ["content_scripts/youtube_spa.js"],
            "run_at": "document_idle",
        }
    ]
    for script in manifest["content_scripts"][0]["js"]:
        assert (extension_root / script).is_file()
    assert manifest["web_accessible_resources"][0]["resources"] == ["blocked.html"]
    assert (extension_root / "blocked.html").is_file()
    visible_metadata = " ".join(
        str(manifest[key])
        for key in ("name", "short_name", "description")
    )
    assert "SelfBoss" not in visible_metadata
    assert "skeleton" not in visible_metadata.lower()
    assert "file:" not in json.dumps(manifest).lower()
    assert "icons" not in manifest


def test_resource_paths_use_source_checkout_by_default() -> None:
    assert browser_extension_folder() == Path.cwd() / "browser_extension" / "chrome_mv3"
    assert recovery_scripts_folder() == Path.cwd() / "scripts"


def test_extension_package_script_creates_source_only_zip() -> None:
    output = Path("dist") / "extension" / "pytest" / "LoopGuardChromeExtensionTest.zip"
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(Path("scripts") / "package_extension.ps1"),
            "-OutputPath",
            str(output),
        ],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert output.is_file()
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())

    assert "manifest.json" in names
    assert "background.js" in names
    assert "blocked.html" in names
    assert "blocked.js" in names
    assert "content_scripts/youtube_spa.js" in names
    assert "LoopGuard.exe" not in names
    assert "LoopGuardNativeHost.exe" not in names
    assert not any(name.startswith("_internal/") for name in names)
    assert not any(name.endswith((".db", ".sqlite", ".sqlite3", ".log")) for name in names)


def test_web_store_prep_docs_state_manual_privacy_safe_flow() -> None:
    root_notes = Path("DEV_NOTES.md").read_text(encoding="utf-8")
    extension_notes = (Path("browser_extension") / "chrome_mv3" / "DEV_NOTES.md").read_text(
        encoding="utf-8"
    )
    combined = f"{root_notes}\n{extension_notes}"
    lower_combined = combined.lower()
    compact_combined = " ".join(combined.split())

    assert "Chrome Web Store extension prep" in root_notes
    assert "dist\\extension\\LoopGuardChromeExtension.zip" in combined
    assert "No official Web Store ID exists" in compact_combined
    assert "guided manual unpacked setup" in lower_combined
    assert "developer registration requires a fee" in lower_combined
    assert "Future installer work can use" in combined
    assert "no telemetry" in lower_combined
    assert "no cloud" in lower_combined
    assert "no browsing history upload" in lower_combined
    assert "no cookies/dom/form/screenshot/page" in lower_combined
    assert "nativeMessaging" in combined
    assert "declarativeNetRequest" in combined
    assert "icon assets are currently missing" in extension_notes
    assert "does not publish" in lower_combined
    assert "enterprise force-install" in lower_combined
    assert "install the extension silently" in lower_combined


def test_resource_paths_and_registrar_use_pyinstaller_meipass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert app_resource_path("scripts") == tmp_path / "scripts"
    assert browser_extension_folder() == tmp_path / "browser_extension" / "chrome_mv3"
    assert BrowserSetupRegistrar().repo_root == tmp_path.resolve()


def test_invalid_extension_id_is_rejected_without_runner_call(tmp_path: Path) -> None:
    calls: list[object] = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0)

    registrar = BrowserSetupRegistrar(repo_root=tmp_path, runner=runner)

    result = registrar.repair_native_host(
        browser="chrome",
        extension_id="not-valid",
    )

    assert result.ok is False
    assert "Extension ID" in result.reason
    assert calls == []


def test_valid_extension_id_calls_repair_helper_for_chrome(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="installed", stderr="")

    registrar = BrowserSetupRegistrar(repo_root=tmp_path, runner=runner)

    result = registrar.repair_native_host(
        browser="chrome",
        extension_id=VALID_EXTENSION_ID,
    )

    assert result.ok is True
    assert result.reason == (
        "Native host registered. Reload the extension, then Refresh status."
    )
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        sys.executable,
        str(tmp_path / "packaging" / "native_messaging" / "register_native_host.py"),
        "--browser",
        "chrome",
        "--extension-id",
        NORMALIZED_EXTENSION_ID,
        "--repo-root",
        str(tmp_path),
        "--install",
    ]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_repair_native_host_defaults_to_stable_loopguard_extension_id(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="installed", stderr="")

    registrar = BrowserSetupRegistrar(repo_root=tmp_path, runner=runner)

    result = registrar.repair_native_host(browser="chrome")

    assert result.ok is True
    command, _kwargs = calls[0]
    assert "--extension-id" in command
    assert command[command.index("--extension-id") + 1] == LOOPGUARD_CHROME_EXTENSION_ID


def test_direct_production_native_host_registration_writes_manifest_and_hkcu_value(
    tmp_path: Path,
) -> None:
    native_host = tmp_path / "LoopGuardNativeHost.exe"
    native_host.write_text("binary placeholder", encoding="utf-8")
    manifest_dir = tmp_path / "native_messaging"
    registry_calls: list[tuple[str, str]] = []

    registrar = BrowserSetupRegistrar(
        repo_root=tmp_path,
        manifest_dir=manifest_dir,
        native_host_path=native_host,
        registry_setter=lambda subkey, value: registry_calls.append((subkey, value)),
    )

    result = registrar.repair_native_host(browser="chrome")

    manifest_path = manifest_dir / "com.selfboss.native_host.chrome.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result.ok is True
    assert manifest == build_native_host_manifest(native_host_path=native_host)
    assert registry_calls == [
        (
            r"Software\Google\Chrome\NativeMessagingHosts\com.selfboss.native_host",
            str(manifest_path),
        )
    ]


def test_repair_failure_returns_compact_reason(tmp_path: Path) -> None:
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            2,
            stdout="",
            stderr="error: registry unavailable",
        )

    registrar = BrowserSetupRegistrar(repo_root=tmp_path, runner=runner)

    result = registrar.repair_native_host(
        browser="chrome",
        extension_id=VALID_EXTENSION_ID,
    )

    assert result.ok is False
    assert result.reason == "Native host registration failed: error: registry unavailable"


def test_unregister_calls_registration_helper_uninstall_for_chrome(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="removed", stderr="")

    registrar = BrowserSetupRegistrar(repo_root=tmp_path, runner=runner)

    result = registrar.unregister_native_host(browser="chrome")

    assert result.ok is True
    assert result.reason == (
        "Native host unregistered. Browser integration will be disconnected "
        "until registered again."
    )
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        sys.executable,
        str(tmp_path / "packaging" / "native_messaging" / "register_native_host.py"),
        "--browser",
        "chrome",
        "--repo-root",
        str(tmp_path),
        "--uninstall",
    ]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_unregister_failure_returns_compact_reason(tmp_path: Path) -> None:
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            2,
            stdout="",
            stderr="error: key unavailable",
        )

    registrar = BrowserSetupRegistrar(repo_root=tmp_path, runner=runner)

    result = registrar.unregister_native_host(browser="chrome")

    assert result.ok is False
    assert result.reason == "Native host unregister failed: error: key unavailable"


def test_edge_registration_remains_planned_without_runner_call(tmp_path: Path) -> None:
    calls: list[object] = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0)

    registrar = BrowserSetupRegistrar(repo_root=tmp_path, runner=runner)

    repair_result = registrar.repair_native_host(
        browser="edge",
        extension_id=VALID_EXTENSION_ID,
    )
    unregister_result = registrar.unregister_native_host(browser="edge")

    assert repair_result.ok is False
    assert repair_result.reason == (
        "Only Chrome native host registration is supported in this patch."
    )
    assert unregister_result.ok is False
    assert unregister_result.reason == (
        "Only Chrome native host registration is supported in this patch."
    )
    assert calls == []


@pytest.mark.parametrize("value", [VALID_EXTENSION_ID, f" {VALID_EXTENSION_ID} "])
def test_normalize_extension_id_lowercases_valid_ids(value: str) -> None:
    assert normalize_extension_id(value) == NORMALIZED_EXTENSION_ID


def test_open_chrome_extensions_page_uses_chrome_executable(tmp_path: Path) -> None:
    chrome_path = tmp_path / "chrome.exe"
    calls: list[list[str]] = []

    def launcher(command: list[str]) -> None:
        calls.append(command)

    result = open_chrome_extensions_page(
        chrome_finder=lambda: chrome_path,
        launcher=launcher,
    )

    assert result.ok is True
    assert result.reason == "Opened Chrome Extensions page."
    assert calls == [[str(chrome_path), "chrome://extensions"]]


def test_open_chrome_extensions_page_falls_back_when_chrome_missing() -> None:
    calls: list[list[str]] = []

    result = open_chrome_extensions_page(
        chrome_finder=lambda: None,
        launcher=lambda command: calls.append(command),
    )

    assert result.ok is False
    assert result.reason == "Open Chrome and paste: chrome://extensions"
    assert result.copy_text == "chrome://extensions"
    assert calls == []


def test_open_extension_folder_uses_file_explorer(tmp_path: Path) -> None:
    folder = tmp_path / "browser_extension" / "chrome_mv3"
    folder.mkdir(parents=True)
    calls: list[list[str]] = []

    result = open_extension_folder(
        folder,
        launcher=lambda command: calls.append(command),
    )

    assert result.ok is True
    assert result.reason == f"Opened extension folder: {folder.resolve()}"
    assert calls == [["explorer.exe", str(folder.resolve())]]


def test_open_extension_folder_falls_back_when_missing(tmp_path: Path) -> None:
    folder = tmp_path / "missing" / "chrome_mv3"
    calls: list[list[str]] = []

    result = open_extension_folder(
        folder,
        launcher=lambda command: calls.append(command),
    )

    assert result.ok is False
    assert result.reason == f"Open this folder manually: {folder.resolve()}"
    assert result.copy_text == str(folder.resolve())
    assert calls == []
