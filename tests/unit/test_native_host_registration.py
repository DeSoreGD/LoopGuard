from __future__ import annotations

import json
import importlib.util
import sys
from io import StringIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = ROOT / "packaging" / "native_messaging" / "register_native_host.py"
SPEC = importlib.util.spec_from_file_location("register_native_host", HELPER_PATH)
assert SPEC is not None
registration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = registration
SPEC.loader.exec_module(registration)


VALID_EXTENSION_ID = "abcdefghijklmnopabcdefghijklmnop"
EXPECTED_EXTENSION_ID = "mcpljcfiphfoapmohiahhfjgcenhckkh"


class MockRegistry:
    def __init__(self) -> None:
        self.set_calls: list[tuple[str, str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []

    def set_default_value(self, root: str, subkey: str, value: str) -> None:
        self.set_calls.append((root, subkey, value))

    def delete_key(self, root: str, subkey: str) -> None:
        self.delete_calls.append((root, subkey))


@pytest.fixture
def fake_repo_root(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    template_dir = repo_root / "packaging" / "native_messaging"
    template_dir.mkdir(parents=True)
    for browser in ("chrome", "edge"):
        (template_dir / f"selfboss.{browser}.json.template").write_text(
            "{}\n",
            encoding="utf-8",
        )
    return repo_root


def test_default_dry_run_does_not_write_registry_or_files(fake_repo_root: Path) -> None:
    registry = MockRegistry()
    stdout = StringIO()
    manifest_output = fake_repo_root / "out" / "selfboss.chrome.json"

    exit_code = registration.run(
        [
            "--browser",
            "chrome",
            "--extension-id",
            VALID_EXTENSION_ID,
            "--repo-root",
            str(fake_repo_root),
            "--manifest-output",
            str(manifest_output),
        ],
        registry=registry,
        stdout=stdout,
    )

    assert exit_code == 0
    assert registry.set_calls == []
    assert registry.delete_calls == []
    assert not manifest_output.exists()
    assert "DRY RUN" in stdout.getvalue()
    assert "No registry or file changes were made." in stdout.getvalue()


def test_browser_registry_paths_are_hkcu_selfboss_keys() -> None:
    assert registration.registry_subkey_for_browser("chrome") == (
        r"Software\Google\Chrome\NativeMessagingHosts\com.selfboss.native_host"
    )
    assert registration.registry_subkey_for_browser("edge") == (
        r"Software\Microsoft\Edge\NativeMessagingHosts\com.selfboss.native_host"
    )


def test_install_generates_manifest_launcher_and_hkcu_registration(
    fake_repo_root: Path,
) -> None:
    registry = MockRegistry()
    manifest_output = fake_repo_root / "generated-test" / "selfboss.chrome.json"

    exit_code = registration.run(
        [
            "--browser",
            "chrome",
            "--extension-id",
            VALID_EXTENSION_ID,
            "--repo-root",
            str(fake_repo_root),
            "--manifest-output",
            str(manifest_output),
            "--install",
        ],
        registry=registry,
        stdout=StringIO(),
    )

    manifest = json.loads(manifest_output.read_text(encoding="utf-8"))
    launcher_path = Path(manifest["path"])
    launcher_text = launcher_path.read_text(encoding="utf-8")

    assert exit_code == 0
    assert manifest["name"] == "com.selfboss.native_host"
    assert manifest["description"] == "LoopGuard Native Messaging host"
    assert manifest["type"] == "stdio"
    assert manifest["allowed_origins"] == [
        f"chrome-extension://{VALID_EXTENSION_ID}/"
    ]
    assert launcher_path.is_absolute()
    assert ".venv\\Scripts\\python.exe" in launcher_text
    assert "-m selfboss_native_host" in launcher_text
    assert "PYTHONPATH" in launcher_text
    assert "@echo off" in launcher_text
    assert "echo " not in launcher_text.lower().replace("@echo off", "")
    assert registry.set_calls == [
        (
            "HKCU",
            r"Software\Google\Chrome\NativeMessagingHosts\com.selfboss.native_host",
            str(manifest_output.resolve()),
        )
    ]
    assert registry.delete_calls == []


def test_install_defaults_to_stable_loopguard_extension_id(
    fake_repo_root: Path,
) -> None:
    registry = MockRegistry()
    manifest_output = fake_repo_root / "generated-test" / "selfboss.chrome.json"

    exit_code = registration.run(
        [
            "--browser",
            "chrome",
            "--repo-root",
            str(fake_repo_root),
            "--manifest-output",
            str(manifest_output),
            "--install",
        ],
        registry=registry,
        stdout=StringIO(),
    )

    manifest = json.loads(manifest_output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert manifest["allowed_origins"] == [
        f"chrome-extension://{EXPECTED_EXTENSION_ID}/"
    ]


def test_install_can_generate_production_manifest_for_native_host_exe(
    fake_repo_root: Path,
) -> None:
    registry = MockRegistry()
    native_host = fake_repo_root / "dist" / "LoopGuard" / "LoopGuardNativeHost.exe"
    native_host.parent.mkdir(parents=True)
    native_host.write_text("binary placeholder", encoding="utf-8")
    manifest_output = fake_repo_root / "generated-test" / "selfboss.chrome.json"

    exit_code = registration.run(
        [
            "--browser",
            "chrome",
            "--repo-root",
            str(fake_repo_root),
            "--manifest-output",
            str(manifest_output),
            "--native-host-path",
            str(native_host),
            "--install",
        ],
        registry=registry,
        stdout=StringIO(),
    )

    manifest = json.loads(manifest_output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert manifest["path"] == str(native_host.resolve())
    assert manifest["allowed_origins"] == [
        f"chrome-extension://{EXPECTED_EXTENSION_ID}/"
    ]
    assert not (fake_repo_root / "packaging" / "native_messaging" / "generated" / "selfboss_native_host_dev.cmd").exists()
    assert registry.set_calls == [
        (
            "HKCU",
            r"Software\Google\Chrome\NativeMessagingHosts\com.selfboss.native_host",
            str(manifest_output.resolve()),
        )
    ]


def test_uninstall_targets_only_selected_selfboss_hkcu_key(fake_repo_root: Path) -> None:
    registry = MockRegistry()

    exit_code = registration.run(
        [
            "--browser",
            "edge",
            "--repo-root",
            str(fake_repo_root),
            "--uninstall",
        ],
        registry=registry,
        stdout=StringIO(),
    )

    assert exit_code == 0
    assert registry.set_calls == []
    assert registry.delete_calls == [
        (
            "HKCU",
            r"Software\Microsoft\Edge\NativeMessagingHosts\com.selfboss.native_host",
        )
    ]


@pytest.mark.parametrize("extension_id", ["", "not-valid", "abcdefghijklmnopabcdefghijklmnoq"])
def test_invalid_extension_id_is_rejected(
    fake_repo_root: Path,
    extension_id: str,
) -> None:
    registry = MockRegistry()
    stderr = StringIO()

    exit_code = registration.run(
        [
            "--browser",
            "chrome",
            "--extension-id",
            extension_id,
            "--repo-root",
            str(fake_repo_root),
        ],
        registry=registry,
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert registry.set_calls == []
    assert registry.delete_calls == []
    assert "extension ID" in stderr.getvalue() or "--extension-id" in stderr.getvalue()
