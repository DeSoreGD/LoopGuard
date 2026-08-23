"""Task dialog for creating or viewing tasks."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from selfboss.core.models import Task, TaskKind, TaskPlanningStatus
from selfboss.core.rewards import RewardService
from selfboss.ui.window_chrome import (
    apply_dark_window_chrome,
    apply_loopguard_window_icon,
)


@dataclass(frozen=True)
class TaskDialogValues:
    """Values collected from the task dialog."""

    title: str
    kind: TaskKind
    reward_minutes_override: int
    allowed_url: str | None


class TaskDialog(QDialog):
    """Modal dialog for task fields."""

    def __init__(
        self,
        *,
        task: Task | None = None,
        read_only: bool = False,
        day_started: bool = False,
        initial_kind: TaskKind = TaskKind.NORMAL,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task = task
        self.read_only = read_only
        self.day_started = day_started
        self.reward_service = RewardService()

        self.setWindowTitle("Task Details" if task else "New Task")
        apply_loopguard_window_icon(self)
        self.setObjectName("TaskDialog")
        self.setModal(True)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(18, 18, 18, 18)

        header = QFrame()
        header.setObjectName("DialogHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setSpacing(4)
        header_layout.setContentsMargins(14, 12, 14, 12)
        self.dialog_title_label = QLabel("Task Details" if task else "New Task")
        self.dialog_title_label.setObjectName("DialogTitle")
        self.dialog_subtitle_label = QLabel(
            "Viewing locked task details."
            if read_only
            else (
                "Capture active-day work without reward."
                if day_started and task is None
                else "Plan an anchor or support task for today."
            )
        )
        self.dialog_subtitle_label.setObjectName("DialogSubtitle")
        self.dialog_subtitle_label.setWordWrap(True)
        header_layout.addWidget(self.dialog_title_label)
        header_layout.addWidget(self.dialog_subtitle_label)
        layout.addWidget(header)

        self.planning_note_label = QLabel(
            "Day already started. New tasks are unplanned and do not grant "
            "reward in this MVP."
            if day_started and task is None
            else "This task will be part of today's plan."
        )
        self.planning_note_label.setObjectName("DialogNote")
        self.planning_note_label.setWordWrap(True)
        layout.addWidget(self.planning_note_label)

        fields_panel = QFrame()
        fields_panel.setObjectName("DialogPanel")
        fields_layout = QVBoxLayout(fields_panel)
        fields_layout.setSpacing(10)
        fields_layout.setContentsMargins(14, 14, 14, 14)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setSpacing(10)

        self.title_input = QLineEdit()
        self.title_input.setObjectName("taskDialogTitleInput")
        self.title_input.setMinimumWidth(260)
        self.title_input.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.kind_input = QComboBox()
        self.kind_input.setObjectName("taskDialogKindInput")
        for kind in TaskKind:
            self.kind_input.addItem(kind.value.title(), kind.value)
        self.kind_input.setCurrentIndex(self.kind_input.findData(initial_kind.value))
        self.reward_preview_label = QLabel()
        self.reward_preview_label.setObjectName("DialogRewardPreview")
        self.reward_preview_label.setWordWrap(True)
        self.kind_input.currentIndexChanged.connect(self._update_reward_preview)

        form.addRow("Title", self.title_input)
        form.addRow("Kind", self.kind_input)
        form.addRow("Reward", self.reward_preview_label)
        fields_layout.addLayout(form)
        layout.addWidget(fields_panel)

        self.advanced_group = QGroupBox("Advanced")
        self.advanced_group.setObjectName("TaskDialogAdvancedGroup")
        advanced_form = QFormLayout(self.advanced_group)
        advanced_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        advanced_form.setSpacing(10)
        self.allowed_url_input = QLineEdit()
        self.allowed_url_input.setObjectName("taskDialogAllowedUrlInput")
        self.allowed_url_input.setMinimumWidth(260)
        self.allowed_url_input.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.allowed_url_input.setPlaceholderText("Optional allowed URL")
        self.allowed_url_lock_label = QLabel("")
        self.allowed_url_lock_label.setObjectName("taskDialogAllowedUrlLockLabel")
        self.allowed_url_lock_label.setWordWrap(True)
        if day_started:
            self.allowed_url_input.setEnabled(False)
            self.allowed_url_input.setPlaceholderText(
                "URL exceptions are locked after Start Day"
            )
            self.allowed_url_lock_label.setText(
                "URL exceptions are locked after Start Day."
            )
        advanced_form.addRow("Allowed URL (optional)", self.allowed_url_input)
        advanced_form.addRow("", self.allowed_url_lock_label)
        layout.addWidget(self.advanced_group)

        buttons = (
            QDialogButtonBox.StandardButton.Close
            if read_only
            else (
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel
            )
        )
        self.button_box = QDialogButtonBox(buttons)
        self.button_box.setObjectName("TaskDialogButtonBox")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        _style_dialog_buttons(self.button_box)
        layout.addWidget(self.button_box)

        if task is not None:
            self._load_task(task)
        if read_only:
            self._set_read_only()
        self._update_reward_preview()
        QTimer.singleShot(0, self._apply_dark_window_chrome)

    def values(self) -> TaskDialogValues:
        """Return normalized dialog values."""
        allowed_url = self.allowed_url_input.text().strip()
        return TaskDialogValues(
            title=self.title_input.text().strip(),
            kind=self._selected_kind(),
            reward_minutes_override=0,
            allowed_url=allowed_url or None,
        )

    def showEvent(self, event: QShowEvent) -> None:
        """Reapply native titlebar styling once the dialog handle exists."""
        super().showEvent(event)
        self._apply_dark_window_chrome()

    def _load_task(self, task: Task) -> None:
        self.title_input.setText(task.title)
        self.kind_input.setCurrentIndex(self.kind_input.findData(task.kind.value))
        self.allowed_url_input.setText(task.allowed_url or "")

    def _set_read_only(self) -> None:
        self.title_input.setReadOnly(True)
        self.kind_input.setEnabled(False)
        self.allowed_url_input.setReadOnly(True)
        self.title_input.setProperty("readOnlyState", True)
        self.allowed_url_input.setProperty("readOnlyState", True)
        for widget in (self.title_input, self.kind_input, self.allowed_url_input):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _selected_kind(self) -> TaskKind:
        kind = self.kind_input.currentData()
        if isinstance(kind, TaskKind):
            return kind
        if isinstance(kind, str):
            return TaskKind(kind)
        return TaskKind.NORMAL

    def _update_reward_preview(self) -> None:
        if self.task is not None:
            minutes = (
                0
                if self.task.planning_status is TaskPlanningStatus.UNPLANNED
                else self.task.reward_minutes
                or self.reward_service.reward_minutes_for_kind(self.task.kind)
            )
            self.reward_preview_label.setText(f"{minutes} min")
            return

        if self.day_started:
            self.reward_preview_label.setText("0 min (unplanned)")
            return

        minutes = self.reward_service.reward_minutes_for_kind(self._selected_kind())
        self.reward_preview_label.setText(f"{minutes} min")

    def _apply_dark_window_chrome(self) -> None:
        apply_dark_window_chrome(self)


def _style_dialog_buttons(button_box: QDialogButtonBox) -> None:
    for standard_button, role in (
        (QDialogButtonBox.StandardButton.Ok, "primary"),
        (QDialogButtonBox.StandardButton.Cancel, "quiet"),
        (QDialogButtonBox.StandardButton.Close, "quiet"),
    ):
        button = button_box.button(standard_button)
        if button is None:
            continue
        button.setProperty("buttonRole", role)
        button.style().unpolish(button)
        button.style().polish(button)
