from __future__ import annotations

import selfboss
from selfboss import main as main_module


def test_package_exposes_version() -> None:
    assert selfboss.__version__ == "0.1.0"


def test_main_delegates_to_gui_runner(monkeypatch) -> None:
    def fake_run() -> int:
        return 0

    monkeypatch.setattr(main_module, "run", fake_run)

    exit_code = main_module.main()
    assert exit_code == 0
