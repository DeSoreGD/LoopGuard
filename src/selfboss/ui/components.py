"""Reusable PySide6 widget helpers for LoopGuard UI pages."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from selfboss.ui.style import (
    CARD_PADDING,
    CARD_SPACING,
    CONTENT_MAX_WIDTH,
    PAGE_MARGIN,
    PAGE_SPACING,
)


class CardFrame(QFrame):
    """Shared top-aligned card container with consistent internal spacing."""

    def __init__(
        self,
        title: str | None = None,
        subtitle: str | None = None,
        *,
        parent: QFrame | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CardFrame")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        configure_card_layout(layout)
        self.card_layout = layout

        self.title_label: QLabel | None = None
        self.subtitle_label: QLabel | None = None
        if title is not None:
            self.title_label = make_title_label(title)
            layout.addWidget(self.title_label)
        if subtitle is not None:
            self.subtitle_label = make_muted_label(subtitle)
            layout.addWidget(self.subtitle_label)


def make_title_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("CardTitle")
    set_word_wrap(label)
    return label


def make_muted_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("MutedText")
    set_word_wrap(label)
    return label


def make_value_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("ValueText")
    set_word_wrap(label)
    return label


def make_badge(text: str, variant: str = "neutral") -> QLabel:
    label = QLabel(text)
    label.setObjectName("Badge")
    label.setProperty("variant", variant)
    set_word_wrap(label)
    return label


def set_word_wrap(label: QLabel) -> QLabel:
    label.setWordWrap(True)
    return label


def reset_layout(layout: QLayout) -> QLayout:
    layout.setContentsMargins(0, 0, 0, 0)
    return layout


def top_aligned(layout: QLayout) -> QLayout:
    layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    return layout


def configure_page_layout(layout: QLayout) -> QLayout:
    layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
    layout.setSpacing(PAGE_SPACING)
    top_aligned(layout)
    return layout


def configure_card_layout(layout: QLayout) -> QLayout:
    layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
    layout.setSpacing(CARD_SPACING)
    top_aligned(layout)
    return layout


def make_page_content(
    object_name: str,
    *,
    max_width: int = CONTENT_MAX_WIDTH,
) -> tuple[QWidget, QWidget, QVBoxLayout]:
    """Build a left-aligned, max-width content area for scroll pages."""
    shell = QWidget()
    shell.setObjectName(f"{object_name}Shell")
    shell_layout = QHBoxLayout(shell)
    reset_layout(shell_layout)
    shell_layout.setSpacing(0)
    top_aligned(shell_layout)

    content = QWidget()
    content.setObjectName(object_name)
    content.setMaximumWidth(max_width)
    content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    content_layout = QVBoxLayout(content)
    configure_page_layout(content_layout)

    shell_layout.addStretch(1)
    shell_layout.addWidget(content, 24, Qt.AlignmentFlag.AlignTop)
    shell_layout.addStretch(1)
    return shell, content, content_layout
