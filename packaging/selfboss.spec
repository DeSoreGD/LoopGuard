# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path.cwd()
SRC = ROOT / "src"
PACKAGING = ROOT / "packaging"
ICON = ROOT / "assets" / "icons" / "loopguard.ico"
VERSION_INFO = PACKAGING / "version_info.txt"

datas = [
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "docs"), "docs"),
    (str(ROOT / "scripts" / "recovery_status.ps1"), "scripts"),
    (str(ROOT / "scripts" / "recovery_unlock.ps1"), "scripts"),
    (str(ROOT / "scripts" / "reset_test_mode.ps1"), "scripts"),
    (str(ROOT / "browser_extension" / "chrome_mv3"), "browser_extension/chrome_mv3"),
    (
        str(PACKAGING / "native_messaging" / "register_native_host.py"),
        "packaging/native_messaging",
    ),
    (
        str(PACKAGING / "native_messaging" / "selfboss.chrome.json.template"),
        "packaging/native_messaging",
    ),
    (
        str(PACKAGING / "native_messaging" / "selfboss.edge.json.template"),
        "packaging/native_messaging",
    ),
    (str(VERSION_INFO), "packaging"),
]
if (ROOT / "assets").exists():
    datas.append((str(ROOT / "assets"), "assets"))


a = Analysis(
    [str(SRC / "selfboss" / "main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
native_a = Analysis(
    [str(SRC / "selfboss_native_host" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
native_pyz = PYZ(native_a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LoopGuard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
    version=str(VERSION_INFO),
)
native_exe = EXE(
    native_pyz,
    native_a.scripts,
    [],
    exclude_binaries=True,
    name="LoopGuardNativeHost",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
    version=str(VERSION_INFO),
)
coll = COLLECT(
    exe,
    native_exe,
    a.binaries,
    native_a.binaries,
    a.datas,
    native_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LoopGuard",
)
