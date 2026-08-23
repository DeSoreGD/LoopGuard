"""Tasks tab for the LoopGuard UI shell."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QDialog,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from selfboss.core.models import Task, TaskKind, TaskPlanningStatus, TaskStatus
from selfboss.core.use_cases import SelfBossAppService
from selfboss.ui.components import (
    CardFrame,
    make_muted_label,
    make_page_content,
    make_value_label,
    reset_layout,
)
from selfboss.ui.style import (
    CONTROL_HEIGHT,
    SMALL_GAP,
    TABLE_MAX_HEIGHT,
    TABLE_PAGE_MAX_WIDTH,
    common_stylesheet,
)
from selfboss.ui.task_dialog import TaskDialog
from selfboss.ui.theme import modern_common_stylesheet
from selfboss.ui.widgets import (
    EmptyState,
    configure_pill,
    make_subpanel,
    set_button_role,
    set_card_role,
)


class TasksPage(QWidget):
    """Tasks page bound to the application service."""

    dialog_class = TaskDialog

    def __init__(
        self,
        service: SelfBossAppService,
        *,
        on_tasks_changed: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.on_tasks_changed = on_tasks_changed
        self._tasks: list[Task] = []
        self._task_card_widgets: dict[int, QFrame] = {}

        self.setObjectName("tasksPage")
        self.setStyleSheet(_tasks_stylesheet())
        outer_layout = QVBoxLayout(self)
        reset_layout(outer_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("tasksScrollArea")
        self.scroll_area.viewport().setObjectName("tasksScrollViewport")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        outer_layout.addWidget(self.scroll_area)

        shell, content, layout = make_page_content(
            "tasksContent",
            max_width=TABLE_PAGE_MAX_WIDTH,
        )
        self.content_widget = content
        self.scroll_area.setWidget(shell)

        header_card, header_layout, self.page_title_label = _build_card("Tasks")
        set_card_role(header_card, "hero")
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(8)
        header_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.summary_label = make_value_label("")
        self.summary_label.setObjectName("ProductMetric")
        self.day_status_label = make_muted_label("")
        configure_pill(self.day_status_label, "focus")
        self.status_label = QLabel("")
        self.status_label.setObjectName("taskStatusMessage")
        self.status_label.setWordWrap(True)
        header_layout.addWidget(
            make_subpanel(
                self.summary_label,
                self.day_status_label,
                self.status_label,
                role="metric",
            )
        )
        layout.addWidget(header_card)

        actions_card, actions_layout, self.actions_card_title_label = _build_card(
            "Actions"
        )
        set_card_role(actions_card, "secondary")
        self.new_task_button = QPushButton("New Task")
        self.edit_task_button = QPushButton("View Task")
        self.open_allowed_url_button = QPushButton("Open task URL")
        self.mark_done_button = QPushButton("Mark Done")
        self.cancel_claim_button = QPushButton("Cancel claim")
        self.delete_task_button = QPushButton("Delete Task")
        self.new_task_button.setObjectName("newTaskButton")
        self.edit_task_button.setObjectName("viewTaskButton")
        self.open_allowed_url_button.setObjectName("openAllowedUrlButton")
        self.mark_done_button.setObjectName("markDoneButton")
        self.cancel_claim_button.setObjectName("cancelClaimButton")
        self.delete_task_button.setObjectName("deleteTaskButton")
        for button in (
            self.new_task_button,
            self.edit_task_button,
            self.open_allowed_url_button,
            self.mark_done_button,
            self.cancel_claim_button,
            self.delete_task_button,
        ):
            button.setMinimumHeight(CONTROL_HEIGHT)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        set_button_role(self.new_task_button, "primary")
        set_button_role(self.edit_task_button, "quiet")
        set_button_role(self.open_allowed_url_button, "quiet")
        set_button_role(self.mark_done_button, "primary")
        set_button_role(self.cancel_claim_button, "quiet")
        set_button_role(self.delete_task_button, "danger")

        self.new_task_button.clicked.connect(self.open_new_task_dialog)
        self.edit_task_button.clicked.connect(self.open_edit_task_dialog)
        self.open_allowed_url_button.clicked.connect(self.open_selected_allowed_url)
        self.mark_done_button.clicked.connect(self.mark_selected_done)
        self.cancel_claim_button.clicked.connect(self.cancel_selected_claim)
        self.delete_task_button.clicked.connect(self.delete_selected_task)

        actions_layout.addLayout(
            _make_button_row(
                self.new_task_button,
                self.edit_task_button,
                self.open_allowed_url_button,
                self.mark_done_button,
                self.cancel_claim_button,
                self.delete_task_button,
            )
        )
        layout.addWidget(actions_card)

        table_card, table_layout, self.table_card_title_label = _build_card(
            "Today's Tasks"
        )
        set_card_role(table_card, "list")
        self.empty_state_label = make_muted_label("")
        table_layout.addWidget(self.empty_state_label)
        self.task_cards_panel = QWidget()
        self.task_cards_panel.setObjectName("taskCardsPanel")
        self.task_cards_layout = QVBoxLayout(self.task_cards_panel)
        reset_layout(self.task_cards_layout)
        self.task_cards_layout.setSpacing(SMALL_GAP)
        table_layout.addWidget(self.task_cards_panel)
        self.tasks_table = QTableWidget(0, 6)
        self.tasks_table.setObjectName("tasksTable")
        self.tasks_table.setHorizontalHeaderLabels(
            ["Title", "Kind", "Plan", "Reward", "URL", "Status"]
        )
        self.tasks_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.tasks_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.tasks_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tasks_table.setAlternatingRowColors(True)
        self.tasks_table.setShowGrid(False)
        self.tasks_table.setWordWrap(False)
        self.tasks_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.tasks_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tasks_table.setMinimumHeight(220)
        self.tasks_table.setMaximumHeight(TABLE_MAX_HEIGHT)
        self.tasks_table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.tasks_table.verticalHeader().setVisible(False)
        self.tasks_table.verticalHeader().setDefaultSectionSize(32)
        header = self.tasks_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.tasks_table.setColumnWidth(1, 92)
        self.tasks_table.setColumnWidth(2, 92)
        self.tasks_table.setColumnWidth(3, 82)
        self.tasks_table.setColumnWidth(4, 140)
        self.tasks_table.setColumnWidth(5, 92)
        self.tasks_table.itemSelectionChanged.connect(self._update_action_state)
        self.tasks_table.setVisible(False)
        table_layout.addWidget(self.tasks_table)
        layout.addWidget(table_card)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(1000)
        self.refresh_timer.timeout.connect(self.refresh_tasks)
        self.refresh_timer.start()

        self.refresh_tasks()

    def refresh_tasks(self) -> None:
        """Reload tasks from the service into the table."""
        selected_task_id = self.selected_task_id()
        snapshot = self.service.dashboard_snapshot()
        self.summary_label.setText(
            f"{snapshot.planned_task_count} planned / "
            f"{snapshot.unplanned_task_count} unplanned today"
        )
        self.summary_label.setWordWrap(True)
        self.day_status_label.setText(f"Day status: {snapshot.day_status_label}")
        if snapshot.soft_start_active:
            self.day_status_label.setText(
                "Day status: Soft Start active - tasks unlock in "
                f"{_format_soft_start_remaining(snapshot.soft_start_remaining_seconds)}"
            )
        self._tasks = self.service.list_tasks()
        self.tasks_table.setRowCount(len(self._tasks))
        self._refresh_empty_state(snapshot.day_started)
        for row, task in enumerate(self._tasks):
            self._set_task_row(row, task)
        self._restore_task_selection(selected_task_id)
        self._refresh_task_cards(snapshot, selected_task_id=self.selected_task_id())
        self._update_action_state()

    def open_new_task_dialog(self, initial_kind: TaskKind = TaskKind.NORMAL) -> None:
        """Open the task dialog and create a task if accepted."""
        if not isinstance(initial_kind, TaskKind):
            initial_kind = TaskKind.NORMAL
        snapshot = self.service.dashboard_snapshot()
        dialog = self.dialog_class(
            day_started=snapshot.day_started,
            initial_kind=initial_kind,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        values = dialog.values()
        try:
            task = self.service.create_task(
                title=values.title,
                kind=values.kind,
                reward_minutes_override=values.reward_minutes_override,
                allowed_url=values.allowed_url,
            )
        except ValueError as error:
            self.status_label.setText(str(error))
            return

        self.status_label.setText(f"Created: {task.title}")
        self._notify_changed()

    def open_edit_task_dialog(self) -> None:
        """Open a read-only task dialog for the selected task."""
        task = self.selected_task()
        if task is None:
            self.status_label.setText("Select a task first")
            return

        dialog = self.dialog_class(task=task, read_only=True, parent=self)
        dialog.exec()
        self.status_label.setText(f"Viewing: {task.title}")

    def open_selected_allowed_url(self) -> None:
        """Open the selected task's exact allowed URL."""
        task = self.selected_task()
        snapshot = self.service.dashboard_snapshot()
        if not _can_open_task_allowed_url(task, day_started=snapshot.day_started):
            self.status_label.setText(
                _open_allowed_url_tooltip(task, day_started=snapshot.day_started)
            )
            return

        opened = QDesktopServices.openUrl(QUrl(task.allowed_url))
        if not opened:
            self.status_label.setText(f"Open in Chrome: {task.allowed_url}")
            return

        warning = _task_allowed_url_browser_warning(self.service)
        if warning:
            self.status_label.setText(warning)
            return
        self.status_label.setText("Opened exact task URL.")

    def mark_selected_done(self) -> None:
        """Claim or confirm completion for the selected task."""
        task = self.selected_task()
        if task is None:
            self.status_label.setText("Select a task first")
            return

        try:
            if _uses_completion_claim(task) and not task.completion_claimed_at:
                claim = self.service.claim_task_done(task.id)
                self.status_label.setText(
                    "Claimed: "
                    f"{claim.task.title}. Confirm Done after 3 minutes."
                )
                self._notify_changed()
                return
            result = self.service.confirm_task_done(task.id)
        except ValueError as error:
            self.status_label.setText(str(error))
            self.refresh_tasks()
            return
        if result.reward_entry is None and task.status is TaskStatus.DONE:
            self.status_label.setText(f"Already done: {result.task.title}")
        else:
            self.status_label.setText(f"Completed: {result.task.title}")
        self._notify_changed()

    def cancel_selected_claim(self) -> None:
        """Cancel the selected task's pending completion claim."""
        task = self.selected_task()
        if task is None:
            self.status_label.setText("Select a task first")
            return

        try:
            updated_task = self.service.cancel_task_completion_claim(task.id)
        except ValueError as error:
            self.status_label.setText(str(error))
            self.refresh_tasks()
            return

        self.status_label.setText(f"Claim canceled: {updated_task.title}")
        self._notify_changed()

    def delete_selected_task(self) -> None:
        """Delete the selected task when lifecycle policy allows it."""
        task = self.selected_task()
        if task is None:
            self.status_label.setText("Select a task first")
            return

        try:
            self.service.delete_task(task.id)
        except ValueError as error:
            self.status_label.setText(str(error))
            self.refresh_tasks()
            return

        self.status_label.setText(f"Deleted: {task.title}")
        self._notify_changed()

    def selected_task(self) -> Task | None:
        """Return the selected task, if a row is selected."""
        task_id = self.selected_task_id()
        if task_id is None:
            return None
        for task in self._tasks:
            if task.id == task_id:
                return task
        return None

    def selected_task_id(self) -> int | None:
        """Return the selected task id, if a row is selected."""
        selected_rows = self.tasks_table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        item = self.tasks_table.item(selected_rows[0].row(), 0)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _set_task_row(self, row: int, task: Task) -> None:
        title = QTableWidgetItem(task.title)
        title.setData(Qt.ItemDataRole.UserRole, task.id)
        title.setToolTip(task.title)
        self.tasks_table.setItem(row, 0, title)
        self.tasks_table.setItem(row, 1, _table_item(_display_kind(task)))
        self.tasks_table.setItem(
            row,
            2,
            _table_item(task.planning_status.value.title()),
        )
        self.tasks_table.setItem(
            row,
            3,
            _table_item(str(self._display_reward_minutes(task))),
        )
        url_item = _table_item("URL exception" if task.allowed_url else "")
        if task.allowed_url:
            url_item.setToolTip(task.allowed_url)
        self.tasks_table.setItem(row, 4, url_item)
        self.tasks_table.setItem(row, 5, _table_item(self._display_task_status(task)))

    def _refresh_task_cards(self, snapshot, *, selected_task_id: int | None) -> None:
        _clear_layout(self.task_cards_layout)
        self._task_card_widgets = {}
        rows = list(enumerate(self._tasks))
        main_rows = [
            (row, task)
            for row, task in rows
            if task.kind is TaskKind.MAIN
            and task.planning_status is TaskPlanningStatus.PLANNED
        ]
        support_rows = [
            (row, task)
            for row, task in rows
            if task.planning_status is TaskPlanningStatus.PLANNED
            and task.kind is not TaskKind.MAIN
        ]
        unplanned_rows = [
            (row, task)
            for row, task in rows
            if task.planning_status is TaskPlanningStatus.UNPLANNED
        ]

        self.task_cards_layout.addWidget(
            self._build_task_section(
                "Anchor task",
                main_rows,
                subtitle="Main task for today.",
                empty_title="No anchor task yet",
                empty_detail=(
                    "Create a MAIN task before Start Day."
                    if not snapshot.day_started
                    else "The day is active without a planned anchor task."
                ),
                primary=True,
                show_new_task=not main_rows and not snapshot.day_started,
                snapshot=snapshot,
                selected_task_id=selected_task_id,
            )
        )
        self.task_cards_layout.addWidget(
            self._build_task_section(
                "Planned support",
                support_rows,
                subtitle="Rewardable planned work.",
                empty_title="No support tasks planned",
                empty_detail="Add support work before Start Day.",
                snapshot=snapshot,
                selected_task_id=selected_task_id,
            )
        )
        self.task_cards_layout.addWidget(
            self._build_task_section(
                "Unplanned capture",
                unplanned_rows,
                subtitle="Captured during an active day.",
                empty_title="No unplanned tasks",
                empty_detail="New active-day tasks appear here without reward.",
                snapshot=snapshot,
                selected_task_id=selected_task_id,
            )
        )

    def _build_task_card(
        self,
        row: int,
        task: Task,
        snapshot,
        *,
        selected_task_id: int | None,
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("TaskCard")
        card.setProperty("selected", task.id == selected_task_id)
        set_card_role(card, "task")
        self._task_card_widgets[task.id] = card
        layout = QVBoxLayout(card)
        reset_layout(layout)
        layout.setSpacing(SMALL_GAP + 2)

        title_row = QHBoxLayout()
        reset_layout(title_row)
        title_row.setSpacing(SMALL_GAP)
        title = QLabel(task.title)
        title.setObjectName("TaskCardTitle")
        title.setWordWrap(True)
        title.setMinimumHeight(24)
        select_button = QPushButton("Select")
        select_button.setObjectName("taskCardSelectButton")
        set_button_role(select_button, "quiet")
        select_button.clicked.connect(
            lambda _checked=False, index=row: self._select_task_card(index)
        )
        title_row.addWidget(title, 1)
        title_row.addWidget(select_button)
        layout.addLayout(title_row)

        chip_row = QHBoxLayout()
        reset_layout(chip_row)
        chip_row.setSpacing(SMALL_GAP)
        for label in (
            _task_chip(
                _display_kind(task),
                "focus" if task.kind.value == "main" else "neutral",
            ),
            _task_chip(
                task.planning_status.value.title(),
                "success"
                if task.planning_status is TaskPlanningStatus.PLANNED
                else "warning",
            ),
            _task_chip(f"{self._display_reward_minutes(task)}m reward", "neutral"),
            _task_chip(self._display_task_status(task), _task_status_role(task, self)),
        ):
            chip_row.addWidget(label)
        if task.allowed_url:
            chip_row.addWidget(_task_chip("URL exception", "utility"))
        chip_row.addStretch(1)
        layout.addLayout(chip_row)
        layout.addLayout(self._build_inline_action_row(row, task, snapshot))
        return card

    def _build_inline_action_row(self, row: int, task: Task, snapshot) -> QHBoxLayout:
        action_row = QHBoxLayout()
        reset_layout(action_row)
        action_row.setSpacing(SMALL_GAP + 2)

        view_button = self._build_inline_button(
            "View",
            "taskCardViewButton",
            "quiet",
            row,
            self.open_edit_task_dialog,
        )
        action_row.addWidget(view_button)

        if task.allowed_url:
            open_button = self._build_inline_button(
                "Open URL",
                "taskCardOpenUrlButton",
                "quiet",
                row,
                self.open_selected_allowed_url,
            )
            can_open = _can_open_task_allowed_url(
                task,
                day_started=snapshot.day_started,
            )
            open_button.setEnabled(can_open)
            open_button.setToolTip(
                _open_allowed_url_tooltip(task, day_started=snapshot.day_started)
            )
            action_row.addWidget(open_button)

        if task.status is not TaskStatus.DONE:
            done_button = self._build_inline_button(
                _mark_done_button_text(task, self.service),
                "taskCardMarkDoneButton",
                "primary",
                row,
                self.mark_selected_done,
            )
            can_complete = _can_complete_task_from_snapshot(task, snapshot, self.service)
            done_button.setEnabled(can_complete)
            done_button.setToolTip(
                _mark_done_tooltip(
                    task,
                    snapshot.day_started,
                    snapshot.soft_start_active,
                    snapshot.surrender_active_today,
                    self.service,
                )
            )
            action_row.addWidget(done_button)

        if (
            task.status is TaskStatus.PENDING
            and _uses_completion_claim(task)
            and task.completion_claimed_at
        ):
            cancel_button = self._build_inline_button(
                "Cancel claim",
                "taskCardCancelClaimButton",
                "quiet",
                row,
                self.cancel_selected_claim,
            )
            action_row.addWidget(cancel_button)

        if _can_delete_in_ui(task, day_started=snapshot.day_started):
            delete_button = self._build_inline_button(
                "Delete",
                "taskCardDeleteButton",
                "danger",
                row,
                self.delete_selected_task,
            )
            delete_button.setToolTip(_delete_task_tooltip(task, day_started=False))
            action_row.addWidget(delete_button)

        action_row.addStretch(1)
        return action_row

    def _build_inline_button(
        self,
        text: str,
        object_name: str,
        role: str,
        row: int,
        handler: Callable[[], None],
    ) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        set_button_role(button, role)
        button.clicked.connect(
            lambda _checked=False, index=row, action=handler: self._run_card_action(
                index,
                action,
            )
        )
        return button

    def _run_card_action(self, row: int, handler: Callable[[], None]) -> None:
        self._select_task_card(row)
        handler()

    def _build_task_section(
        self,
        title: str,
        rows: list[tuple[int, Task]],
        *,
        subtitle: str,
        empty_title: str,
        empty_detail: str,
        primary: bool = False,
        show_new_task: bool = False,
        snapshot=None,
        selected_task_id: int | None = None,
    ) -> QFrame:
        section = QFrame()
        section.setObjectName("TaskSection")
        section.setProperty("role", "primary" if primary else "neutral")
        layout = QVBoxLayout(section)
        reset_layout(layout)
        layout.setSpacing(SMALL_GAP)

        title_label = QLabel(title)
        title_label.setObjectName("TaskSectionTitle")
        layout.addWidget(title_label)
        subtitle_label = make_muted_label(subtitle)
        subtitle_label.setObjectName("TaskSectionSubtitle")
        subtitle_label.setWordWrap(True)
        layout.addWidget(subtitle_label)
        if rows:
            for row, task in rows:
                layout.addWidget(
                    self._build_task_card(
                        row,
                        task,
                        snapshot,
                        selected_task_id=selected_task_id,
                    )
                )
            return section

        layout.addWidget(EmptyState(empty_title, empty_detail))
        if show_new_task:
            new_button = QPushButton("New Task")
            new_button.setObjectName("anchorNewTaskButton")
            set_button_role(new_button, "primary")
            new_button.clicked.connect(
                lambda _checked=False: self.open_new_task_dialog(TaskKind.MAIN)
            )
            action_row = QHBoxLayout()
            reset_layout(action_row)
            action_row.addWidget(new_button)
            action_row.addStretch(1)
            layout.addLayout(action_row)
        return section

    def _select_task_card(self, row: int) -> None:
        self.tasks_table.selectRow(row)
        self._sync_task_card_selection()

    def _restore_task_selection(self, selected_task_id: int | None) -> None:
        if selected_task_id is None:
            return
        for row, task in enumerate(self._tasks):
            if task.id == selected_task_id:
                self.tasks_table.selectRow(row)
                return

    def _sync_task_card_selection(self) -> None:
        selected_task_id = self.selected_task_id()
        for task_id, card in self._task_card_widgets.items():
            is_selected = task_id == selected_task_id
            if card.property("selected") == is_selected:
                continue
            card.setProperty("selected", is_selected)
            card.style().unpolish(card)
            card.style().polish(card)

    def _display_reward_minutes(self, task: Task) -> int:
        if task.planning_status.value == "unplanned":
            return 0
        if task.reward_minutes > 0:
            return task.reward_minutes
        return self.service.reward_service.reward_minutes_for_kind(task.kind)

    def _display_task_status(self, task: Task) -> str:
        if task.status is TaskStatus.DONE:
            return "Done"
        if _uses_completion_claim(task) and task.completion_claimed_at:
            remaining_seconds = self.service.task_completion_claim_remaining_seconds(task)
            return "Claim pending" if remaining_seconds > 0 else "Confirm ready"
        return task.status.value.title()

    def _notify_changed(self) -> None:
        self.refresh_tasks()
        if self.on_tasks_changed is not None:
            self.on_tasks_changed()

    def _update_action_state(self) -> None:
        self._sync_task_card_selection()
        task = self.selected_task()
        has_selection = task is not None
        snapshot = self.service.dashboard_snapshot()
        self.new_task_button.setToolTip(
            "New tasks after Start Day are Unplanned. MAIN must be planned before Start Day."
            if snapshot.day_started
            else "Create a task for today's plan."
        )
        self.edit_task_button.setEnabled(has_selection)
        self.edit_task_button.setToolTip(
            "View the selected task." if has_selection else "Select a task first."
        )
        can_open_allowed_url = _can_open_task_allowed_url(
            task,
            day_started=snapshot.day_started,
        )
        self.open_allowed_url_button.setEnabled(can_open_allowed_url)
        self.open_allowed_url_button.setToolTip(
            _open_allowed_url_tooltip(task, day_started=snapshot.day_started)
        )
        self.mark_done_button.setText(_mark_done_button_text(task, self.service))
        can_complete = (
            has_selection
            and _can_complete_task_from_snapshot(task, snapshot, self.service)
        )
        self.mark_done_button.setEnabled(can_complete)
        self.mark_done_button.setToolTip(
            _mark_done_tooltip(
                task,
                snapshot.day_started,
                snapshot.soft_start_active,
                snapshot.surrender_active_today,
                self.service,
            )
        )
        self.cancel_claim_button.setVisible(True)
        self.cancel_claim_button.setEnabled(
            bool(
                has_selection
                and task.status is TaskStatus.PENDING
                and _uses_completion_claim(task)
                and task.completion_claimed_at
            )
        )
        self.cancel_claim_button.setToolTip(
            "Cancel the pending completion claim."
            if self.cancel_claim_button.isEnabled()
            else "No pending completion claim."
        )
        self.delete_task_button.setVisible(not snapshot.day_started)
        self.delete_task_button.setEnabled(
            has_selection and _can_delete_in_ui(task, day_started=snapshot.day_started)
        )
        self.delete_task_button.setToolTip(
            _delete_task_tooltip(task, day_started=snapshot.day_started)
        )

    def _refresh_empty_state(self, day_started: bool) -> None:
        if self._tasks:
            if day_started:
                self.empty_state_label.setText(
                    "New tasks will be Unplanned and will not earn rewards."
                )
            else:
                self.empty_state_label.setText(
                    "Create a MAIN task before starting the day."
                )
            return

        if day_started:
            self.empty_state_label.setText(
                "No tasks for today. New tasks will be Unplanned and will not earn rewards."
            )
        else:
            self.empty_state_label.setText(
                "No tasks planned yet. Create a MAIN task before starting the day."
            )


def _can_delete_in_ui(task: Task | None, *, day_started: bool) -> bool:
    if task is None or task.status is not TaskStatus.PENDING:
        return False
    if task.planning_status.value == "planned":
        return not day_started
    return False


def _uses_completion_claim(task: Task | None) -> bool:
    return bool(
        task is not None
        and task.planning_status is TaskPlanningStatus.PLANNED
        and task.status is TaskStatus.PENDING
    )


def _can_open_task_allowed_url(task: Task | None, *, day_started: bool) -> bool:
    return bool(
        task is not None
        and day_started
        and task.allowed_url
        and task.planning_status is TaskPlanningStatus.PLANNED
        and task.status is TaskStatus.PENDING
    )


def _open_allowed_url_tooltip(task: Task | None, *, day_started: bool) -> str:
    if task is None:
        return "Select a task first."
    if not task.allowed_url:
        return "This task has no allowed URL."
    if task.planning_status is not TaskPlanningStatus.PLANNED:
        return "Only planned tasks can use exact allowed URLs."
    if task.status is TaskStatus.DONE:
        return "Completed task URLs are no longer allowed."
    if not day_started:
        return "Start the day before opening a task URL."
    return (
        "Open the exact task URL. Browser extension control is required; "
        "hosts-only blocking can still block the domain."
    )


def _task_allowed_url_browser_warning(service: SelfBossAppService) -> str:
    status = service.get_browser_integration_status()
    if getattr(status, "trusted_browser_ready", False):
        return ""
    return (
        "Opened task URL. Exact blocking requires extension-connected Chrome; "
        "hosts-only blocking may still block this domain."
    )


def _claim_still_pending(
    task: Task | None,
    service: SelfBossAppService,
) -> bool:
    return bool(
        _uses_completion_claim(task)
        and task is not None
        and task.completion_claimed_at
        and service.task_completion_claim_remaining_seconds(task) > 0
    )


def _can_complete_task_from_snapshot(
    task: Task | None,
    snapshot,
    service: SelfBossAppService,
) -> bool:
    return bool(
        task is not None
        and snapshot.day_started
        and not snapshot.soft_start_active
        and not snapshot.surrender_active_today
        and task.status is not TaskStatus.DONE
        and not _claim_still_pending(task, service)
    )


def _mark_done_button_text(
    task: Task | None,
    service: SelfBossAppService,
) -> str:
    if task is None:
        return "Claim Done"
    if task.status is TaskStatus.DONE:
        return "Done"
    if task.planning_status is TaskPlanningStatus.UNPLANNED:
        return "Mark Done"
    if not task.completion_claimed_at:
        return "Claim Done"
    remaining_seconds = service.task_completion_claim_remaining_seconds(task)
    if remaining_seconds > 0:
        return f"Confirm in {_format_soft_start_remaining(remaining_seconds)}"
    return "Confirm Done"


def _mark_done_tooltip(
    task: Task | None,
    day_started: bool,
    soft_start_active: bool,
    surrender_active: bool,
    service: SelfBossAppService,
) -> str:
    if task is None:
        return "Select a task first."
    if task.status is TaskStatus.DONE:
        return "This task is already complete."
    if not day_started:
        return "Start the day before completing planned tasks."
    if soft_start_active:
        return "Soft Start active. Tasks unlock when the buffer ends."
    if surrender_active:
        return "Task completion is unavailable after Surrender."
    if _claim_still_pending(task, service):
        return "Confirm Done becomes available after the claim delay."
    if _uses_completion_claim(task) and not task.completion_claimed_at:
        return "Claim completion first. Reward and access unlock after Confirm Done."
    if _uses_completion_claim(task):
        return "Confirm the selected task complete."
    return "Mark the selected task complete."


def _delete_task_tooltip(task: Task | None, *, day_started: bool) -> str:
    if task is None:
        return "Select a task first."
    if day_started:
        return "Tasks cannot be deleted after Start Day."
    if task.status is not TaskStatus.PENDING:
        return "Completed tasks cannot be deleted."
    if task.planning_status.value != "planned":
        return "Only pending planned tasks can be deleted."
    return "Delete this pending planned task."


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _task_chip(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(False)
    return configure_pill(label, role)


def _task_status_role(task: Task, page: TasksPage) -> str:
    if task.status is TaskStatus.DONE:
        return "success"
    if _uses_completion_claim(task) and task.completion_claimed_at:
        remaining_seconds = page.service.task_completion_claim_remaining_seconds(task)
        return "warning" if remaining_seconds > 0 else "success"
    return "focus"


def _format_soft_start_remaining(seconds: int) -> str:
    minutes, remaining_seconds = divmod(max(0, seconds), 60)
    if minutes <= 0:
        return f"{remaining_seconds}s"
    if remaining_seconds == 0:
        return f"{minutes}m"
    return f"{minutes}m {remaining_seconds}s"


def _display_kind(task: Task) -> str:
    if task.kind.value == "main":
        return "MAIN"
    return task.kind.value.title()


def _table_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setToolTip(text)
    return item


def _build_card(title: str) -> tuple[QFrame, QVBoxLayout, QLabel]:
    card = CardFrame(title)
    title_label = card.title_label
    if title_label is None:
        raise RuntimeError("Tasks cards require a title label.")
    return card, card.card_layout, title_label


def _make_button_row(*buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    reset_layout(row)
    row.setSpacing(SMALL_GAP)
    for button in buttons:
        row.addWidget(button)
    row.addStretch(1)
    return row


def _tasks_stylesheet() -> str:
    return (
        """
    QWidget#tasksPage {
        background: #0b0c0b;
    }
    QScrollArea#tasksScrollArea,
    QWidget#tasksContent {
        background: #0b0c0b;
        border: none;
    }
    """
        + common_stylesheet()
        + modern_common_stylesheet()
    )
