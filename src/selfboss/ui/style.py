"""Shared UI layout and style tokens for LoopGuard widgets."""

from __future__ import annotations

PAGE_MARGIN = 24
PAGE_SPACING = 16
CARD_PADDING = 16
CARD_SPACING = 10
GRID_SPACING = 16
CONTROL_HEIGHT = 34
SMALL_GAP = 6
MEDIUM_GAP = 10
LARGE_GAP = 16
DASHBOARD_MAX_WIDTH = 1120
TABLE_PAGE_MAX_WIDTH = 1280
SETTINGS_MAX_WIDTH = 960
CONTENT_MAX_WIDTH = TABLE_PAGE_MAX_WIDTH
CONTENT_NARROW_MAX_WIDTH = SETTINGS_MAX_WIDTH
TABLE_MAX_HEIGHT = 360
SIDEBAR_WIDTH = 190


def common_stylesheet() -> str:
    """Return shared QSS for reusable LoopGuard UI primitives."""
    return f"""
    QFrame#CardFrame {{
        background: #ffffff;
        border: 1px solid #d9e1ec;
        border-radius: 8px;
    }}
    QLabel#CardTitle {{
        color: #475569;
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
    }}
    QLabel#MutedText {{
        color: #64748b;
        font-size: 12px;
    }}
    QLabel#ValueText {{
        color: #111827;
        font-size: 15px;
        font-weight: 700;
    }}
    QLabel#Badge {{
        background: #f1f5f9;
        color: #475569;
        border: 1px solid #d9e1ec;
        border-radius: 8px;
        padding: 4px 8px;
        font-size: 11px;
        font-weight: 800;
    }}
    QPushButton {{
        min-height: {CONTROL_HEIGHT - 4}px;
    }}
    QPushButton:disabled {{
        color: #94a3b8;
        background: #f8fafc;
        border-color: #e2e8f0;
    }}
    QLineEdit,
    QSpinBox,
    QComboBox {{
        min-height: {CONTROL_HEIGHT - 4}px;
    }}
    """
