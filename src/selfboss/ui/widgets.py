"""Small visual helper functions for SelfBoss widget pages."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from selfboss.ui.components import (
    CardFrame,
    make_badge,
    make_muted_label,
    make_value_label,
    reset_layout,
)
from selfboss.ui.style import SMALL_GAP


class AppCard(CardFrame):
    """Product-style card built on the existing shared CardFrame."""

    def __init__(
        self,
        title: str | None = None,
        subtitle: str | None = None,
        *,
        role: str = "control",
        parent: QFrame | None = None,
    ) -> None:
        super().__init__(title, subtitle, parent=parent)
        set_card_role(self, role)


class CompactRow(QFrame):
    """Small row container for card/list item layouts."""

    def __init__(self, *, role: str = "neutral", parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CompactRow")
        self.setProperty("role", role)
        layout = QHBoxLayout(self)
        reset_layout(layout)
        layout.setSpacing(SMALL_GAP)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.row_layout = layout


class SubPanel(QFrame):
    """Neutral inset surface for grouping related card content."""

    def __init__(self, *, role: str = "neutral", parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SubPanel")
        self.setProperty("role", role)
        layout = QVBoxLayout(self)
        reset_layout(layout)
        layout.setSpacing(SMALL_GAP)
        self.panel_layout = layout


class EmptyState(QFrame):
    """Compact empty-state surface."""

    def __init__(self, title: str, detail: str = "", parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EmptyState")
        layout = QVBoxLayout(self)
        reset_layout(layout)
        layout.setSpacing(SMALL_GAP)
        title_label = make_value_label(title)
        title_label.setObjectName("EmptyStateTitle")
        layout.addWidget(title_label)
        if detail:
            layout.addWidget(make_muted_label(detail))


def set_card_role(card: QFrame, role: str) -> QFrame:
    """Apply a visual card role without changing widget behavior."""
    card.setProperty("role", role)
    card.style().unpolish(card)
    card.style().polish(card)
    return card


def set_button_role(button: QPushButton, role: str) -> QPushButton:
    """Apply a visual button role without changing the button object name."""
    button.setProperty("buttonRole", role)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.style().unpolish(button)
    button.style().polish(button)
    return button


def make_status_pill(text: str, role: str = "neutral") -> QLabel:
    """Create a compact status pill label."""
    pill = make_badge(text, variant=role)
    return configure_pill(pill, role)


def configure_pill(label: QLabel, role: str = "neutral") -> QLabel:
    """Make an existing label behave visually like a compact pill."""
    label.setObjectName("ProductStatusPill")
    label.setProperty("role", role)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    label.setMinimumHeight(24)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.style().unpolish(label)
    label.style().polish(label)
    return label


def make_metric_label(text: str) -> QLabel:
    """Create a metric/value label for product cards."""
    label = make_value_label(text)
    label.setObjectName("ProductMetric")
    label.setMinimumHeight(34)
    return label


def make_section_title(text: str) -> QLabel:
    """Create a compact section title label for dense product cards."""
    label = make_muted_label(text)
    label.setObjectName("SectionTitle")
    return label


def make_status_row(label_text: str, value_label: QLabel) -> QFrame:
    """Wrap an existing value label in a compact label/value row."""
    row = CompactRow()
    label = make_muted_label(label_text)
    label.setObjectName("StatusRowLabel")
    if value_label.objectName() not in {"ProductStatusPill", "DashboardStatusPill"}:
        value_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
    row.row_layout.addWidget(label)
    row.row_layout.addWidget(value_label, 1)
    return row


def make_subpanel(*widgets: QWidget, role: str = "neutral") -> QFrame:
    """Group existing widgets on a neutral inset card surface."""
    panel = SubPanel(role=role)
    for widget in widgets:
        panel.panel_layout.addWidget(widget)
    return panel


def make_action_row(*buttons: QPushButton) -> QHBoxLayout:
    """Create a compact action row for page buttons."""
    row = QHBoxLayout()
    reset_layout(row)
    row.setSpacing(SMALL_GAP)
    for button in buttons:
        row.addWidget(button)
    row.addStretch(1)
    return row


def make_card(title: str, subtitle: str = "", *, role: str = "control") -> AppCard:
    """Create an AppCard with a semantic visual role."""
    return AppCard(title, subtitle or None, role=role)
