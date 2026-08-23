from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from selfboss.core.models import Task, TaskKind, TaskStatus  # noqa: E402
from selfboss.ui.task_dialog import TaskDialog  # noqa: E402


def test_task_dialog_defaults_to_new_normal_task() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = TaskDialog()
    values = dialog.values()

    assert app is not None
    assert values.title == ""
    assert values.kind is TaskKind.NORMAL
    assert values.reward_minutes_override == 0
    assert values.allowed_url is None
    assert dialog.planning_note_label.text() == "This task will be part of today's plan."
    assert dialog.reward_preview_label.text() == "15 min"
    assert not hasattr(dialog, "reward_override_input")


def test_task_dialog_returns_entered_values() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = TaskDialog()

    dialog.title_input.setText("  Main focus  ")
    dialog.kind_input.setCurrentIndex(dialog.kind_input.findData(TaskKind.MAIN.value))
    dialog.allowed_url_input.setText("  https://example.test  ")

    values = dialog.values()

    assert app is not None
    assert values.title == "Main focus"
    assert values.kind is TaskKind.MAIN
    assert values.reward_minutes_override == 0
    assert values.allowed_url == "https://example.test"
    assert dialog.reward_preview_label.text() == "30 min"


def test_task_dialog_prefills_existing_task_in_read_only_mode() -> None:
    app = QApplication.instance() or QApplication([])
    task = Task(
        id=7,
        title="Existing task",
        description="",
        status=TaskStatus.PENDING,
        reward_minutes=30,
        allowed_url="https://example.test",
        created_at="2026-05-07T09:00:00+00:00",
        updated_at="2026-05-07T09:00:00+00:00",
        completed_at=None,
        kind=TaskKind.IMPORTANT,
    )

    dialog = TaskDialog(task=task, read_only=True)

    assert app is not None
    assert dialog.title_input.text() == "Existing task"
    assert dialog.kind_input.currentData() == TaskKind.IMPORTANT.value
    assert dialog.reward_preview_label.text() == "30 min"
    assert dialog.allowed_url_input.text() == "https://example.test"
    assert dialog.title_input.isReadOnly()
    assert not dialog.kind_input.isEnabled()
    assert dialog.allowed_url_input.isReadOnly()


def test_task_dialog_labels_allowed_url_as_advanced_optional() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = TaskDialog()

    assert app is not None
    assert dialog.advanced_group.title() == "Advanced"
    assert dialog.allowed_url_input.placeholderText() == "Optional allowed URL"


def test_task_dialog_explains_unplanned_tasks_after_day_start() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = TaskDialog(day_started=True)

    assert app is not None
    assert dialog.planning_note_label.text() == (
        "Day already started. New tasks are unplanned and do not grant "
        "reward in this MVP."
    )
    assert dialog.reward_preview_label.text() == "0 min (unplanned)"
    assert not dialog.allowed_url_input.isEnabled()
    assert dialog.allowed_url_input.placeholderText() == (
        "URL exceptions are locked after Start Day"
    )
    assert dialog.allowed_url_lock_label.text() == (
        "URL exceptions are locked after Start Day."
    )
