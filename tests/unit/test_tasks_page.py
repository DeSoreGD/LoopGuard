from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QLineEdit,
    QPushButton,
    QScrollArea,
)

from selfboss.core.models import (  # noqa: E402
    AppSettings,
    TaskKind,
    TaskPlanningStatus,
    TaskStatus,
)
from selfboss.core.use_cases import SelfBossAppService  # noqa: E402
from selfboss.data.db import initialize_database  # noqa: E402
from selfboss.data.repositories import (  # noqa: E402
    DayStateRepository,
    HighSessionRepository,
    RewardLedgerRepository,
    RuleRepository,
    TaskRepository,
)
from selfboss.ui.components import CardFrame  # noqa: E402
from selfboss.ui.task_dialog import TaskDialog, TaskDialogValues  # noqa: E402
import selfboss.ui.tasks_page as tasks_page_module  # noqa: E402
from selfboss.ui.tasks_page import TasksPage  # noqa: E402
from selfboss.ui.style import CARD_PADDING, CARD_SPACING  # noqa: E402


def _complete_task_after_claim_delay(service: SelfBossAppService, task_id: int):
    task = service.tasks.get(task_id)
    if (
        task is not None
        and task.planning_status is TaskPlanningStatus.PLANNED
        and task.status is TaskStatus.PENDING
    ):
        claimed = service.claim_task_done(task_id).task
        service.tasks.claim_completion(
            task_id,
            claimed_at=claimed.completion_claimed_at or service._now().isoformat(),
            available_at=service._now().isoformat(),
        )
    return service.confirm_task_done(task_id)


def test_tasks_page_loads_existing_tasks(test_settings: AppSettings) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(title="Stored task", kind=TaskKind.NORMAL)

        page = TasksPage(service)

        assert app is not None
        assert page.scroll_area.objectName() == "tasksScrollArea"
        assert page.scroll_area.widgetResizable() is True
        assert page.scroll_area.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert page.findChildren(QScrollArea) == [page.scroll_area]
        assert page.page_title_label.text() == "Tasks"
        assert page.page_title_label.objectName() == "CardTitle"
        assert page.summary_label.text() == "1 planned / 0 unplanned today"
        assert page.actions_card_title_label.text() == "Actions"
        assert page.table_card_title_label.text() == "Today's Tasks"
        cards = page.findChildren(CardFrame)
        assert len(cards) == 3
        for card in cards:
            margins = card.card_layout.contentsMargins()
            assert card.objectName() == "CardFrame"
            if card.title_label is page.page_title_label:
                assert card.card_layout.spacing() == 8
                assert margins.left() == 14
                assert margins.top() == 12
                assert margins.right() == 14
                assert margins.bottom() == 12
            else:
                assert card.card_layout.spacing() == CARD_SPACING
                assert margins.left() == CARD_PADDING
                assert margins.top() == CARD_PADDING
                assert margins.right() == CARD_PADDING
                assert margins.bottom() == CARD_PADDING
            assert bool(card.card_layout.alignment() & Qt.AlignmentFlag.AlignTop)
            assert card.title_label is not None
            assert card.title_label.objectName() == "CardTitle"
        assert page.tasks_table.rowCount() == 1
        assert page.tasks_table.objectName() == "tasksTable"
        assert page.tasks_table.wordWrap() is False
        assert page.tasks_table.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert page.tasks_table.columnCount() == 6
        assert [
            page.tasks_table.horizontalHeaderItem(column).text()
            for column in range(page.tasks_table.columnCount())
        ] == ["Title", "Kind", "Plan", "Reward", "URL", "Status"]
        assert page.tasks_table.item(0, 0).text() == "Stored task"
        assert page.tasks_table.item(0, 1).text() == "Normal"
        assert page.tasks_table.item(0, 2).text() == "Planned"
        assert page.tasks_table.item(0, 5).text() == "Pending"
        assert page.day_status_label.text() == "Day status: Planning"
        assert page.empty_state_label.text() == (
            "Create a MAIN task before starting the day."
        )
        assert page.findChildren(QLineEdit) == []
        assert page.new_task_button.text() == "New Task"
        assert page.new_task_button.objectName() == "newTaskButton"
        assert page.edit_task_button.text() == "View Task"
        assert page.edit_task_button.objectName() == "viewTaskButton"
        assert page.open_allowed_url_button.text() == "Open task URL"
        assert page.open_allowed_url_button.objectName() == "openAllowedUrlButton"
        assert page.open_allowed_url_button.isEnabled() is False
        assert page.mark_done_button.text() == "Claim Done"
        assert page.mark_done_button.objectName() == "markDoneButton"
        assert page.delete_task_button.text() == "Delete Task"
        assert page.delete_task_button.objectName() == "deleteTaskButton"
        assert page.delete_task_button.isHidden() is False


def test_task_dialog_wraps_long_content_without_behavior_changes() -> None:
    app = QApplication.instance() or QApplication([])
    long_title = ("Long task title " * 8).strip()
    long_url = "https://example.test/" + "long-path/" * 8
    dialog = TaskDialog(
        task=None,
        day_started=False,
    )
    dialog.title_input.setText(long_title)
    dialog.allowed_url_input.setText(long_url)

    values = dialog.values()

    assert app is not None
    assert dialog.minimumWidth() >= 420
    assert dialog.planning_note_label.wordWrap() is True
    assert dialog.title_input.minimumWidth() >= 260
    assert dialog.allowed_url_input.minimumWidth() >= 260
    assert values.title == long_title
    assert values.allowed_url == long_url


def test_tasks_page_creates_task_from_dialog(test_settings: AppSettings) -> None:
    app = QApplication.instance() or QApplication([])
    changed = []
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = TasksPage(service, on_tasks_changed=lambda: changed.append(True))
        page.dialog_class = _accepted_dialog(
            TaskDialogValues(
                title="New main task",
                kind=TaskKind.MAIN,
                reward_minutes_override=45,
                allowed_url="https://example.test",
            )
        )

        page.new_task_button.click()

        tasks = service.list_tasks()
        assert app is not None
        assert len(tasks) == 1
        assert tasks[0].title == "New main task"
        assert tasks[0].kind is TaskKind.MAIN
        assert tasks[0].reward_minutes == 45
        assert tasks[0].allowed_url == "https://example.test/"
        assert tasks[0].planning_status is TaskPlanningStatus.PLANNED
        assert page.tasks_table.rowCount() == 1
        assert page.tasks_table.item(0, 0).text() == "New main task"
        assert page.tasks_table.item(0, 2).text() == "Planned"
        assert page.tasks_table.item(0, 4).text() == "URL exception"
        assert page.tasks_table.item(0, 4).toolTip() == "https://example.test/"
        assert page.summary_label.text() == "1 planned / 0 unplanned today"
        assert changed == [True]


def test_anchor_new_task_prefills_main_but_global_new_task_stays_normal(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    calls: list[TaskKind] = []

    class RecordingDialog:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs["initial_kind"])

        def exec(self):
            return QDialog.DialogCode.Rejected

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        page = TasksPage(service)
        page.dialog_class = RecordingDialog

        page.new_task_button.click()
        page.findChild(QPushButton, "anchorNewTaskButton").click()

        assert app is not None
        assert calls == [TaskKind.NORMAL, TaskKind.MAIN]


def test_tasks_page_opens_selected_task_read_only_dialog(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    calls = []
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        task = service.create_task(
            title="Read existing task",
            kind=TaskKind.IMPORTANT,
            allowed_url="https://example.test",
        )
        page = TasksPage(service)
        page.dialog_class = _recording_dialog(calls)

        page.tasks_table.selectRow(0)
        page.edit_task_button.click()

        assert app is not None
        assert calls == [
            {
                "task_id": task.id,
                "read_only": True,
            }
        ]
        assert service.list_tasks()[0] == task


def test_tasks_page_opens_planned_task_allowed_url_only_when_active(
    test_settings: AppSettings,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    opened_urls: list[str] = []

    def fake_open_url(url) -> bool:
        opened_urls.append(url.toString())
        return True

    monkeypatch.setattr(tasks_page_module.QDesktopServices, "openUrl", fake_open_url)
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        task = service.create_task(
            title="Watch tutorial",
            kind=TaskKind.NORMAL,
            allowed_url="https://www.youtube.com/watch?v=abc123",
        )
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        page = TasksPage(service)

        page.tasks_table.selectRow(0)
        assert app is not None
        assert page.open_allowed_url_button.isEnabled() is False
        assert "Start the day" in page.open_allowed_url_button.toolTip()

        service.start_day()
        page.refresh_tasks()
        page.tasks_table.selectRow(0)
        assert page.open_allowed_url_button.isEnabled() is True

        page.open_allowed_url_button.click()

        assert opened_urls == [task.allowed_url]
        assert "Exact blocking requires extension-connected Chrome" in (
            page.status_label.text()
        )
        assert service.get_active_planned_use_pass() is None
        assert service.tasks.get(task.id).allowed_url == task.allowed_url

        _complete_task_after_claim_delay(service, task.id)
        page.refresh_tasks()
        page.tasks_table.selectRow(0)
        assert page.open_allowed_url_button.isEnabled() is False
        assert "Completed task URLs are no longer allowed" in (
            page.open_allowed_url_button.toolTip()
        )


def test_tasks_page_marks_selected_task_done(test_settings: AppSettings) -> None:
    app = QApplication.instance() or QApplication([])
    changed = []
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        task = service.create_task(title="Finish me", kind=TaskKind.TINY)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        page = TasksPage(service, on_tasks_changed=lambda: changed.append(True))

        page.tasks_table.selectRow(0)
        assert page.mark_done_button.isEnabled() is False
        assert page.mark_done_button.toolTip() == (
            "Start the day before completing planned tasks."
        )
        service.start_day()
        page.refresh_tasks()
        page.tasks_table.selectRow(0)
        page.mark_done_button.click()
        claimed = service.list_tasks()[0]

        assert claimed.status is TaskStatus.PENDING
        assert claimed.completion_claimed_at is not None
        assert claimed.completion_available_at is not None
        assert service.dashboard_snapshot().reward_balance_minutes == 0
        assert page.tasks_table.item(0, 5).text() == "Claim pending"
        assert page.mark_done_button.isEnabled() is False

        service.tasks.claim_completion(
            task.id,
            claimed_at=claimed.completion_claimed_at,
            available_at=service._now().isoformat(),
        )
        page.refresh_tasks()
        page.tasks_table.selectRow(0)
        assert page.mark_done_button.text() == "Confirm Done"
        page.mark_done_button.click()
        completed = service.list_tasks()[0]
        snapshot = service.dashboard_snapshot()

        assert app is not None
        assert completed.id == task.id
        assert completed.status is TaskStatus.DONE
        assert snapshot.reward_balance_minutes == 5
        assert page.tasks_table.item(0, 5).text() == "Done"
        assert changed == [True, True]
        assert page.mark_done_button.isEnabled() is False


def test_tasks_page_disables_mark_done_during_soft_start(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(
            test_settings,
            connection,
            now=current_now,
            soft_start_enabled=True,
        )
        service.create_task(title="Finish later", kind=TaskKind.TINY)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        page = TasksPage(service)

        page.tasks_table.selectRow(0)

        assert app is not None
        assert page.day_status_label.text() == (
            "Day status: Soft Start active - tasks unlock in 15m"
        )
        assert page.mark_done_button.isEnabled() is False
        assert page.mark_done_button.toolTip() == (
            "Soft Start active. Tasks unlock when the buffer ends."
        )

        now = start + timedelta(minutes=15)
        page.refresh_tasks()
        page.tasks_table.selectRow(0)

        assert page.day_status_label.text() == "Day status: Day started"
        assert page.mark_done_button.isEnabled() is True


def test_tasks_page_disables_mark_done_after_surrender(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    start = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    now = start

    def current_now() -> datetime:
        return now

    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(
            test_settings,
            connection,
            now=current_now,
        )
        service.set_surrender_strictness("low")
        task = service.create_task(title="Blocked by surrender", kind=TaskKind.TINY)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        now = start + timedelta(hours=3)
        service.activate_surrender()
        page = TasksPage(service)

        page.tasks_table.selectRow(0)

        assert app is not None
        assert page.mark_done_button.isEnabled() is False
        assert page.mark_done_button.toolTip() == (
            "Task completion is unavailable after Surrender."
        )
        assert service.tasks.get(task.id).status is TaskStatus.PENDING


def test_tasks_page_creates_unplanned_task_after_start_day(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        page = TasksPage(service)
        page.dialog_class = _accepted_dialog(
            TaskDialogValues(
                title="Unexpected work",
                kind=TaskKind.NORMAL,
                reward_minutes_override=45,
                allowed_url=None,
            )
        )

        page.new_task_button.click()
        task = service.list_tasks()[-1]

        assert app is not None
        assert task.planning_status is TaskPlanningStatus.UNPLANNED
        assert task.kind is TaskKind.NORMAL
        assert task.reward_minutes == 0
        assert page.day_status_label.text() == "Day status: Day started"
        assert page.summary_label.text() == "1 planned / 1 unplanned today"
        assert page.tasks_table.item(1, 2).text() == "Unplanned"
        assert page.tasks_table.item(1, 3).text() == "0"
        assert page.empty_state_label.text() == (
            "New tasks will be Unplanned and will not earn rewards."
        )


def test_tasks_page_rejects_main_after_start_day(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        page = TasksPage(service)
        page.dialog_class = _accepted_dialog(
            TaskDialogValues(
                title="Late main",
                kind=TaskKind.MAIN,
                reward_minutes_override=45,
                allowed_url=None,
            )
        )

        page.new_task_button.click()

        assert app is not None
        assert "MAIN must be planned before Start Day" in page.status_label.text()
        assert [task.title for task in service.list_tasks()] == ["Main task"]


def test_tasks_page_shows_today_tasks_by_default(
    test_settings: AppSettings,
    monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    day_one = datetime(2026, 5, 7, 9, 0, tzinfo=timezone.utc)
    day_two = datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        monkeypatch.setattr(service, "_now", lambda: day_one)
        day_one_task = service.create_task(title="Yesterday main", kind=TaskKind.MAIN)
        service.start_day()
        _complete_task_after_claim_delay(service, day_one_task.id)
        page = TasksPage(service)

        assert app is not None
        assert page.tasks_table.rowCount() == 1

        monkeypatch.setattr(service, "_now", lambda: day_two)
        page.refresh_tasks()

        assert page.tasks_table.rowCount() == 0
        assert page.empty_state_label.text() == (
            "No tasks planned yet. Create a MAIN task before starting the day."
        )
        assert service.list_all_tasks()[0].id == day_one_task.id

        service.create_task(title="Today main", kind=TaskKind.MAIN)
        page.refresh_tasks()

        assert page.tasks_table.rowCount() == 1
        assert page.tasks_table.item(0, 0).text() == "Today main"


def test_tasks_page_compacts_long_title_and_url(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    long_title = ("Write a very long proposal title " * 6).strip()
    long_url = "https://example.test/" + "very-long-path-segment/" * 8
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        service.create_task(
            title=long_title,
            kind=TaskKind.IMPORTANT,
            allowed_url=long_url,
        )
        page = TasksPage(service)

        title_item = page.tasks_table.item(0, 0)
        url_item = page.tasks_table.item(0, 4)

        assert app is not None
        assert title_item.text() == long_title
        assert title_item.toolTip() == long_title
        assert url_item.text() == "URL exception"
        assert url_item.toolTip() == long_url


def test_tasks_page_deletes_eligible_pending_tasks(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    changed = []
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        task = service.create_task(title="Remove me", kind=TaskKind.NORMAL)
        page = TasksPage(service, on_tasks_changed=lambda: changed.append(True))

        page.tasks_table.selectRow(0)
        assert page.delete_task_button.isHidden() is False
        assert page.delete_task_button.isEnabled() is True
        page.delete_task_button.click()

        assert app is not None
        assert service.tasks.get(task.id) is None
        assert page.tasks_table.rowCount() == 0
        assert page.status_label.text() == "Deleted: Remove me"
        assert changed == [True]


def test_tasks_page_disables_delete_for_completed_task_in_planning(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        task = service.create_task(title="Completed", kind=TaskKind.NORMAL)
        service.tasks.update_status(task.id, TaskStatus.DONE)
        page = TasksPage(service)

        page.tasks_table.selectRow(0)

        assert app is not None
        assert page.delete_task_button.isHidden() is False
        assert page.delete_task_button.isEnabled() is False
        try:
            service.delete_task(task.id)
        except ValueError as error:
            assert "Completed tasks cannot be deleted" in str(error)
        else:
            raise AssertionError("completed task deletion should be rejected")


def test_tasks_page_hides_delete_after_start_day_for_all_tasks(
    test_settings: AppSettings,
) -> None:
    app = QApplication.instance() or QApplication([])
    with initialize_database(test_settings.db_path) as connection:
        service = _make_service(test_settings, connection)
        planned = service.create_task(title="Planned", kind=TaskKind.NORMAL)
        service.create_task(title="Main task", kind=TaskKind.MAIN)
        service.start_day()
        service.create_task(title="Unplanned", kind=TaskKind.NORMAL)
        page = TasksPage(service)

        page.tasks_table.selectRow(0)

        assert app is not None
        assert service.tasks.get(planned.id) is not None
        assert page.delete_task_button.isHidden() is True
        assert page.delete_task_button.isEnabled() is False
        page.tasks_table.selectRow(1)
        assert page.delete_task_button.isHidden() is True
        assert page.delete_task_button.isEnabled() is False
        try:
            service.delete_task(planned.id)
        except ValueError as error:
            assert "Tasks cannot be deleted after Start Day" in str(error)
        else:
            raise AssertionError("post-Start-Day task deletion should be rejected")


def _make_service(
    settings: AppSettings,
    connection,
    now=None,
    *,
    soft_start_enabled: bool | None = False,
) -> SelfBossAppService:
    service = SelfBossAppService(
        settings=settings,
        tasks=TaskRepository(connection),
        day_state=DayStateRepository(connection),
        rewards=RewardLedgerRepository(connection),
        high_sessions=HighSessionRepository(connection),
        rules=RuleRepository(connection),
        now_provider=now,
    )
    if (
        soft_start_enabled is not None
        and service.day_state.get().day_started_at is None
    ):
        service.set_soft_start_enabled(soft_start_enabled)
    return service


def _accepted_dialog(values: TaskDialogValues):
    class AcceptedDialog:
        def __init__(self, **kwargs) -> None:
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self) -> TaskDialogValues:
            return values

    return AcceptedDialog


def _recording_dialog(calls: list[dict[str, object]]):
    class RecordingDialog:
        def __init__(self, *, task, read_only: bool, **kwargs) -> None:
            calls.append({"task_id": task.id, "read_only": read_only})

        def exec(self):
            return QDialog.DialogCode.Accepted

    return RecordingDialog
