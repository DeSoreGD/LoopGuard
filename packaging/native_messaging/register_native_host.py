from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO


HOST_NAME = "com.selfboss.native_host"
HKCU = "HKCU"
VALID_BROWSERS = ("chrome", "edge")
EXTENSION_ID_RE = re.compile(r"^[a-p]{32}$")
DEFAULT_CHROME_EXTENSION_ID = "mcpljcfiphfoapmohiahhfjgcenhckkh"


class Registry(Protocol):
    def set_default_value(self, root: str, subkey: str, value: str) -> None:
        ...

    def delete_key(self, root: str, subkey: str) -> None:
        ...


class WindowsRegistry:
    def set_default_value(self, root: str, subkey: str, value: str) -> None:
        if root != HKCU:
            raise ValueError("LoopGuard native host registration only supports HKCU.")
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, value)

    def delete_key(self, root: str, subkey: str) -> None:
        if root != HKCU:
            raise ValueError("LoopGuard native host registration only supports HKCU.")
        import winreg

        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
        except FileNotFoundError:
            return


@dataclass(frozen=True)
class RegistrationPlan:
    browser: str
    operation: str
    dry_run: bool
    repo_root: Path
    manifest_path: Path | None
    launcher_path: Path | None
    write_dev_launcher: bool
    registry_subkey: str
    extension_id: str | None

    @property
    def registry_display_path(self) -> str:
        return f"{HKCU}\\{self.registry_subkey}"


def default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def registry_subkey_for_browser(browser: str) -> str:
    normalized = browser.lower()
    if normalized == "chrome":
        return rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"
    if normalized == "edge":
        return rf"Software\Microsoft\Edge\NativeMessagingHosts\{HOST_NAME}"
    raise ValueError(f"unsupported browser: {browser}")


def validate_extension_id(extension_id: str) -> str:
    normalized = extension_id.strip().lower()
    if not EXTENSION_ID_RE.fullmatch(normalized):
        raise ValueError(
            "extension ID must be 32 lowercase Chrome extension characters (a-p)."
        )
    return normalized


def template_path_for_browser(repo_root: Path, browser: str) -> Path:
    return repo_root / "packaging" / "native_messaging" / f"selfboss.{browser}.json.template"


def generated_dir(repo_root: Path) -> Path:
    return repo_root / "packaging" / "native_messaging" / "generated"


def launcher_path_for_repo(repo_root: Path) -> Path:
    return (generated_dir(repo_root) / "selfboss_native_host_dev.cmd").resolve()


def manifest_path_for_repo(
    repo_root: Path,
    browser: str,
    manifest_output: str | None,
) -> Path:
    if manifest_output:
        return Path(manifest_output).expanduser().resolve()
    return (generated_dir(repo_root) / f"selfboss.{browser}.json").resolve()


def build_launcher_content(repo_root: Path) -> str:
    repo_root_text = str(repo_root.resolve())
    return "\r\n".join(
        [
            "@echo off",
            "setlocal",
            f'set "SELF_BOSS_REPO_ROOT={repo_root_text}"',
            'set "SELF_BOSS_PYTHON=%SELF_BOSS_REPO_ROOT%\\.venv\\Scripts\\python.exe"',
            'set "PYTHONPATH=%SELF_BOSS_REPO_ROOT%\\src;%PYTHONPATH%"',
            'if exist "%SELF_BOSS_PYTHON%" (',
            '  "%SELF_BOSS_PYTHON%" -m selfboss_native_host %*',
            ") else (",
            "  python -m selfboss_native_host %*",
            ")",
            "",
        ]
    )


def build_manifest(extension_id: str, native_host_path: Path) -> dict[str, object]:
    return {
        "name": HOST_NAME,
        "description": "LoopGuard Native Messaging host",
        "path": str(native_host_path.resolve()),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{validate_extension_id(extension_id)}/"],
    }


def write_dev_files(
    *,
    repo_root: Path,
    manifest_path: Path,
    launcher_path: Path,
    manifest: dict[str, object],
) -> None:
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text(build_launcher_content(repo_root), encoding="utf-8", newline="")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def build_registration_plan(args: argparse.Namespace) -> RegistrationPlan:
    browser = args.browser.lower()
    repo_root = Path(args.repo_root).expanduser().resolve()
    if not repo_root.exists():
        raise ValueError(f"repo root does not exist: {repo_root}")

    template_path = template_path_for_browser(repo_root, browser)
    if not template_path.exists():
        raise ValueError(f"native host manifest template is missing: {template_path}")

    operation = "uninstall" if args.uninstall else "install"
    dry_run = bool(args.dry_run or not args.install and not args.uninstall)
    extension_id = None
    manifest_path = None
    launcher_path = None
    write_dev_launcher = False
    if operation == "install":
        extension_id = validate_extension_id(
            DEFAULT_CHROME_EXTENSION_ID
            if args.extension_id is None
            else args.extension_id
        )
        if args.native_host_path:
            launcher_path = Path(args.native_host_path).expanduser().resolve()
        else:
            launcher_path = launcher_path_for_repo(repo_root)
            write_dev_launcher = True
        manifest_path = manifest_path_for_repo(repo_root, browser, args.manifest_output)

    return RegistrationPlan(
        browser=browser,
        operation=operation,
        dry_run=dry_run,
        repo_root=repo_root,
        manifest_path=manifest_path,
        launcher_path=launcher_path,
        write_dev_launcher=write_dev_launcher,
        registry_subkey=registry_subkey_for_browser(browser),
        extension_id=extension_id,
    )


def install(plan: RegistrationPlan, registry: Registry) -> None:
    if plan.manifest_path is None or plan.launcher_path is None or plan.extension_id is None:
        raise ValueError("install plan is missing manifest, launcher, or extension ID.")
    manifest = build_manifest(plan.extension_id, plan.launcher_path)
    if plan.write_dev_launcher:
        write_dev_files(
            repo_root=plan.repo_root,
            manifest_path=plan.manifest_path,
            launcher_path=plan.launcher_path,
            manifest=manifest,
        )
    else:
        plan.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        plan.manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    registry.set_default_value(HKCU, plan.registry_subkey, str(plan.manifest_path))


def uninstall(plan: RegistrationPlan, registry: Registry) -> None:
    registry.delete_key(HKCU, plan.registry_subkey)


def describe_plan(plan: RegistrationPlan, stdout: TextIO) -> None:
    prefix = "DRY RUN: " if plan.dry_run else ""
    stdout.write(f"{prefix}browser: {plan.browser}\n")
    stdout.write(f"{prefix}operation: {plan.operation}\n")
    stdout.write(f"{prefix}registry key: {plan.registry_display_path}\n")
    if plan.manifest_path is not None:
        stdout.write(f"{prefix}manifest: {plan.manifest_path}\n")
    if plan.launcher_path is not None:
        stdout.write(f"{prefix}launcher: {plan.launcher_path}\n")
    if plan.extension_id is not None:
        stdout.write(
            f"{prefix}allowed origin: chrome-extension://{plan.extension_id}/\n"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Register the LoopGuard Native Messaging host for local development."
    )
    parser.add_argument("--browser", choices=VALID_BROWSERS, required=True)
    parser.add_argument("--extension-id")
    parser.add_argument("--repo-root", default=str(default_repo_root()))
    parser.add_argument("--manifest-output")
    parser.add_argument("--native-host-path")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.install and args.uninstall:
        parser.error("--install and --uninstall cannot be used together.")
    return args


def run(
    argv: list[str] | None = None,
    *,
    registry: Registry | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    registry = registry or WindowsRegistry()
    try:
        plan = build_registration_plan(parse_args(argv))
        describe_plan(plan, stdout)
        if plan.dry_run:
            stdout.write("No registry or file changes were made.\n")
            return 0
        if plan.operation == "install":
            install(plan, registry)
            stdout.write("Installed LoopGuard native host registration under HKCU.\n")
        else:
            uninstall(plan, registry)
            stdout.write("Removed LoopGuard native host registration from HKCU.\n")
        return 0
    except ValueError as exc:
        stderr.write(f"error: {exc}\n")
        return 2


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
