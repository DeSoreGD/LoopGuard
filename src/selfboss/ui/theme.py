"""Modern visual theme tokens for LoopGuard widgets."""

from __future__ import annotations

APP_BACKGROUND = "#0B0D10"
SURFACE = "#151922"
SURFACE_ELEVATED = "#1A1F2B"
SURFACE_MUTED = "#202635"
SIDEBAR_BACKGROUND = "#10131A"
SIDEBAR_HOVER = "#171C26"
SIDEBAR_ACTIVE = "#18223A"
BORDER = "#2A303A"
BORDER_STRONG = "#394150"
TEXT = "#E6EAF0"
TEXT_MUTED = "#9BA3AF"
TEXT_SUBTLE = "#6F7785"
ACCENT = "#6B8AFB"
ACCENT_HOVER = "#7C9BFF"
ACCENT_SOFT = "#18223A"
SUCCESS = "#8BCB6B"
SUCCESS_SOFT = "#172313"
WARNING = "#D6A85A"
WARNING_SOFT = "#2A2112"
VIOLET = "#A78BFA"
VIOLET_SOFT = "#211B36"
DANGER = "#D36B7A"
DANGER_SOFT = "#2A151A"
DISABLED_TEXT = "#697180"
DISABLED_SURFACE = "#161A22"

RADIUS_CARD = 12
RADIUS_CONTROL = 9
RADIUS_BADGE = 999
CONTROL_MIN_HEIGHT = 32


def modern_common_stylesheet() -> str:
    """Return the modern theme layer for existing LoopGuard object names."""
    return f"""
    /* SelfBoss Modern Theme v1 */
    QMainWindow,
    QWidget#appShell,
    QWidget#contentShell,
    QWidget#dashboardPage,
    QWidget#dashboardContentShell,
    QWidget#dashboardContent,
    QWidget#tasksPage,
    QWidget#tasksContentShell,
    QWidget#tasksContent,
    QWidget#rulesPage,
    QWidget#rulesContentShell,
    QWidget#rulesContent,
    QWidget#settingsPage,
    QWidget#settingsContentShell,
    QWidget#settingsContent {{
        background: {APP_BACKGROUND};
        color: {TEXT};
    }}
    QScrollArea,
    QScrollArea#dashboardScrollArea,
    QScrollArea#tasksScrollArea,
    QScrollArea#rulesScrollArea,
    QScrollArea#settingsScrollArea {{
        background: {APP_BACKGROUND};
        border: none;
    }}
    QAbstractScrollArea::viewport,
    QWidget#dashboardScrollViewport,
    QWidget#tasksScrollViewport,
    QWidget#rulesScrollViewport,
    QWidget#settingsScrollViewport {{
        background: {APP_BACKGROUND};
        border: none;
    }}
    QStackedWidget#contentStack {{
        background: {APP_BACKGROUND};
        border: none;
    }}
    QFrame#appHeader {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {APP_BACKGROUND}, stop:1 #111827);
        border: none;
        border-bottom: 1px solid {BORDER};
    }}
    QLabel#appHeaderTitle {{
        color: {TEXT};
        font-size: 22px;
        font-weight: 700;
    }}
    QLabel#appHeaderSubtitle {{
        color: {TEXT_MUTED};
        font-size: 12px;
        font-weight: 600;
    }}
    QFrame#sidebar {{
        background: {SIDEBAR_BACKGROUND};
        border: none;
        border-right: 1px solid {BORDER};
    }}
    QLabel#sidebarTitle {{
        color: {TEXT};
        font-size: 22px;
        font-weight: 700;
    }}
    QLabel#sidebarSubtitle {{
        color: {TEXT_SUBTLE};
        font-size: 12px;
        font-weight: 500;
    }}
    QLabel#sidebarFooter {{
        background: {SURFACE};
        color: {SUCCESS};
        border: 1px solid #2A3B2C;
        border-radius: {RADIUS_BADGE}px;
        padding: 4px 8px;
        font-size: 11px;
        font-weight: 700;
    }}
    QPushButton#sidebarButton {{
        color: {TEXT_MUTED};
        background: transparent;
        border: 1px solid transparent;
        border-radius: {RADIUS_CONTROL}px;
        padding: 10px 12px;
        text-align: left;
        font-weight: 600;
    }}
    QPushButton#sidebarButton:hover {{
        color: {TEXT};
        background: {SIDEBAR_HOVER};
        border-color: {BORDER};
    }}
    QPushButton#sidebarButton:checked {{
        color: {TEXT};
        background: {SIDEBAR_ACTIVE};
        border-color: #344264;
    }}
    QFrame#CardFrame[role="hero"],
    QFrame#DashboardHeroCard,
    QFrame#CardFrame[variant="hero"] {{
        background: {SURFACE_ELEVATED};
        border-color: {BORDER_STRONG};
    }}
    QFrame#CardFrame[role="control"] {{
        background: {SURFACE_ELEVATED};
    }}
    QFrame#CardFrame[role="list"] {{
        background: {SURFACE_ELEVATED};
    }}
    QFrame#CardFrame[role="compact"] {{
        background: {SURFACE_ELEVATED};
        border-color: {BORDER};
    }}
    QFrame#CardFrame[role="secondary"] {{
        background: {SURFACE};
        border-color: {BORDER};
    }}
    QFrame#CardFrame[role="settings"] {{
        background: {SURFACE};
    }}
    QFrame#CardFrame[role="danger"] {{
        background: {SURFACE_ELEVATED};
        border-color: #4B2C35;
    }}
    QFrame#CardFrame[role="warning"] {{
        background: {SURFACE_ELEVATED};
        border-color: #4B3A20;
    }}
    QFrame#CardFrame[role="recreation"] {{
        background: {SURFACE_ELEVATED};
        border-color: #3D3557;
    }}
    QFrame#CardFrame[role="task"],
    QFrame#TaskSection,
    QFrame#CompactRow {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CONTROL}px;
        padding: 8px;
    }}
    QFrame#TaskSection[role="primary"] {{
        background: {SURFACE_ELEVATED};
        border-color: {BORDER};
    }}
    QWidget#taskCardsPanel {{
        background: transparent;
    }}
    QFrame#TaskSection {{
        background: {SURFACE};
        border-color: {BORDER};
        border-radius: {RADIUS_CARD}px;
        padding: 10px;
    }}
    QFrame#TaskCard {{
        background: {SURFACE_ELEVATED};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CONTROL}px;
        padding: 10px;
    }}
    QFrame#TaskCard[selected="true"] {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #26345D, stop:0.025 #26345D,
            stop:0.026 #1D2433, stop:1 #1D2433);
        border: 1px solid {ACCENT};
    }}
    QLabel#TaskSectionTitle {{
        color: {TEXT_MUTED};
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
    }}
    QLabel#TaskSectionSubtitle {{
        color: {TEXT_SUBTLE};
        font-size: 12px;
        font-weight: 500;
    }}
    QLabel#SectionTitle,
    QLabel#StatusRowLabel {{
        color: {TEXT_SUBTLE};
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
    }}
    QLabel#TaskCardTitle {{
        color: {TEXT};
        font-size: 15px;
        font-weight: 700;
    }}
    QFrame#TaskCard QPushButton {{
        min-height: 28px;
        padding: 4px 10px;
        font-size: 12px;
    }}
    QFrame#TaskCard[selected="true"] QLabel#TaskCardTitle {{
        color: #F2F5FF;
    }}
    QFrame#CompactRow[role="danger"] {{
        background: {DANGER_SOFT};
        border-color: {DANGER};
    }}
    QFrame#SubPanel {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CONTROL}px;
        padding: 10px;
    }}
    QFrame#SubPanel[role="compact"] {{
        background: {SURFACE};
        border-color: {BORDER};
        padding: 7px;
    }}
    QFrame#SubPanel[role="metric"] {{
        background: {SURFACE_ELEVATED};
        border-color: {BORDER};
    }}
    QFrame#SubPanel[role="warning"] {{
        background: {SURFACE_ELEVATED};
        border-color: #4B3A20;
    }}
    QFrame#SubPanel[role="danger"] {{
        background: {SURFACE_ELEVATED};
        border-color: #4B2C35;
    }}
    QFrame#EmptyState {{
        background: {SURFACE};
        border: 1px dashed {BORDER};
        border-radius: {RADIUS_CARD}px;
        padding: 14px;
    }}
    QLabel#EmptyStateTitle {{
        color: {TEXT};
        font-size: 16px;
        font-weight: 700;
    }}
    QFrame#DashboardHeroCard {{
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CARD}px;
    }}
    QFrame#CardFrame,
    QGroupBox {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CARD}px;
    }}
    QLabel {{
        color: {TEXT};
        font-size: 13px;
    }}
    QLabel#CardTitle {{
        color: {TEXT_MUTED};
        font-size: 12px;
        font-weight: 600;
        letter-spacing: 0;
        text-transform: uppercase;
    }}
    QLabel#MutedText {{
        color: {TEXT_MUTED};
        font-size: 12px;
    }}
    QLabel#ValueText {{
        color: {TEXT};
        font-size: 16px;
        font-weight: 700;
    }}
    QLabel#ProductMetric {{
        color: {TEXT};
        font-size: 18px;
        font-weight: 700;
        min-height: 34px;
    }}
    QLabel#ProductStatusPill {{
        background: {SURFACE_MUTED};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_BADGE}px;
        padding: 3px 9px;
        min-height: 22px;
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel#ProductStatusPill[role="focus"] {{
        background: {ACCENT_SOFT};
        color: {ACCENT};
        border-color: #344264;
    }}
    QLabel#ProductStatusPill[role="utility"] {{
        background: #172335;
        color: #AFC2FF;
        border-color: #33435F;
    }}
    QLabel#ProductStatusPill[role="recreation"] {{
        background: {VIOLET_SOFT};
        color: {VIOLET};
        border-color: #3D3557;
    }}
    QLabel#ProductStatusPill[role="success"] {{
        background: {SUCCESS_SOFT};
        color: {SUCCESS};
        border-color: #2A3B2C;
    }}
    QLabel#ProductStatusPill[role="danger"] {{
        background: {DANGER_SOFT};
        color: {DANGER};
        border-color: #4B2C35;
    }}
    QLabel#ProductStatusPill[role="warning"] {{
        background: {WARNING_SOFT};
        color: {WARNING};
        border-color: #4B3A20;
    }}
    QLabel#Badge,
    QLabel#mainTaskBadge {{
        background: {SURFACE_MUTED};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_BADGE}px;
        padding: 4px 9px;
        font-size: 11px;
        font-weight: 700;
    }}
    QLabel#accessBadge {{
        background: {ACCENT_SOFT};
        color: {ACCENT};
        border: 1px solid #344264;
        border-radius: {RADIUS_BADGE}px;
        padding: 5px 12px;
        font-size: 16px;
        font-weight: 700;
    }}
    QLabel#DashboardStatusPill {{
        background: {ACCENT_SOFT};
        color: {ACCENT};
        border: 1px solid #344264;
        border-radius: {RADIUS_BADGE}px;
        padding: 4px 10px;
        min-height: 22px;
        font-size: 12px;
        font-weight: 700;
    }}
    QLabel#DashboardHeroValue {{
        color: {TEXT};
        font-size: 24px;
        font-weight: 700;
        min-height: 38px;
    }}
    QLabel#DashboardMetric,
    QLabel#accessModeMetricLabel {{
        color: {TEXT};
        font-size: 18px;
        font-weight: 700;
        min-height: 34px;
    }}
    QLabel#testModeBadge,
    QLabel#rulesTestModeBadge,
    QLabel#settingsTestModeBadge {{
        background: {SUCCESS_SOFT};
        color: {SUCCESS};
        border: 1px solid #2A3B2C;
        border-radius: {RADIUS_BADGE}px;
        padding: 4px 10px;
        min-height: 22px;
        font-weight: 700;
    }}
    QLabel#mainTaskTitle {{
        color: {TEXT};
        font-size: 15px;
        font-weight: 700;
    }}
    QLabel#taskStatusMessage,
    QLabel#rulesStatusMessage,
    QLabel#rulesFieldLabel {{
        color: {TEXT_MUTED};
    }}
    QPushButton {{
        background: {SURFACE_ELEVATED};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CONTROL}px;
        min-height: {CONTROL_MIN_HEIGHT}px;
        padding: 5px 11px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background: #252C3B;
        border-color: #465064;
        color: #F4F7FB;
    }}
    QPushButton:pressed {{
        background: {ACCENT_SOFT};
        border-color: {BORDER_STRONG};
    }}
    QPushButton:focus {{
        border-color: {ACCENT};
    }}
    QPushButton:disabled {{
        color: {DISABLED_TEXT};
        background: {DISABLED_SURFACE};
        border-color: {BORDER};
    }}
    QPushButton[buttonRole="primary"] {{
        background: {ACCENT_SOFT};
        color: {ACCENT};
        border-color: #344264;
    }}
    QPushButton[buttonRole="primary"]:hover {{
        background: #24325A;
        color: #DDE6FF;
        border-color: {ACCENT_HOVER};
    }}
    QPushButton[buttonRole="primary"]:pressed {{
        background: #1E2947;
        border-color: {ACCENT};
    }}
    QPushButton[buttonRole="danger"] {{
        background: {DANGER_SOFT};
        color: {DANGER};
        border-color: #63333E;
    }}
    QPushButton[buttonRole="danger"]:hover {{
        background: #351A21;
        color: #F0A0AC;
        border-color: #8A4452;
    }}
    QPushButton[buttonRole="danger"]:pressed {{
        background: #2A151A;
        border-color: {DANGER};
    }}
    QPushButton[buttonRole="quiet"] {{
        background: transparent;
        color: {TEXT_MUTED};
        border-color: {BORDER};
    }}
    QPushButton[buttonRole="quiet"]:hover {{
        background: {SURFACE_MUTED};
        color: {TEXT};
        border-color: {BORDER_STRONG};
    }}
    QPushButton[buttonRole="quiet"]:pressed {{
        background: #1C2433;
        color: {TEXT};
        border-color: #465064;
    }}
    QPushButton[buttonRole="primary"]:disabled,
    QPushButton[buttonRole="danger"]:disabled,
    QPushButton[buttonRole="quiet"]:disabled {{
        color: {DISABLED_TEXT};
        background: {DISABLED_SURFACE};
        border-color: {BORDER};
    }}
    QPushButton[buttonRole="primary"]:disabled:hover,
    QPushButton[buttonRole="danger"]:disabled:hover,
    QPushButton[buttonRole="quiet"]:disabled:hover,
    QPushButton:disabled:hover {{
        color: {DISABLED_TEXT};
        background: {DISABLED_SURFACE};
        border-color: {BORDER};
    }}
    QPushButton#startDayButton,
    QPushButton#startPlannedUsePassButton {{
        background: {ACCENT_SOFT};
        color: {ACCENT};
        border-color: #344264;
    }}
    QPushButton#startDayButton:hover,
    QPushButton#startPlannedUsePassButton:hover {{
        background: #202C4A;
        border-color: {ACCENT};
    }}
    QPushButton#startHighButton {{
        background: {VIOLET_SOFT};
        color: {VIOLET};
        border-color: #493A6F;
    }}
    QPushButton#startHighButton:hover {{
        background: #2B2444;
        border-color: {VIOLET};
    }}
    QPushButton#startDayButton:disabled,
    QPushButton#startHighButton:disabled,
    QPushButton#startPlannedUsePassButton:disabled {{
        color: {DISABLED_TEXT};
        background: {DISABLED_SURFACE};
        border-color: {BORDER};
    }}
    QLineEdit,
    QSpinBox,
    QComboBox,
    QTableWidget,
    QListWidget {{
        background: {APP_BACKGROUND};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CONTROL}px;
        min-height: {CONTROL_MIN_HEIGHT}px;
        selection-background-color: {ACCENT_SOFT};
        selection-color: {TEXT};
    }}
    QLineEdit:focus,
    QSpinBox:focus,
    QComboBox:focus,
    QTableWidget:focus,
    QListWidget:focus {{
        border-color: {ACCENT};
    }}
    QLineEdit:disabled,
    QSpinBox:disabled,
    QComboBox:disabled {{
        color: {DISABLED_TEXT};
        background: {DISABLED_SURFACE};
        border-color: {BORDER};
    }}
    QLineEdit:read-only {{
        background: {DISABLED_SURFACE};
        color: {TEXT_MUTED};
        border-color: {BORDER};
    }}
    QTableWidget {{
        background: {SURFACE};
        alternate-background-color: {SURFACE_ELEVATED};
        gridline-color: {BORDER};
        border-radius: {RADIUS_CARD}px;
    }}
    QTableWidget::item {{
        padding: 8px;
        border-bottom: 1px solid {BORDER};
    }}
    QTableWidget::item:selected {{
        background: {ACCENT_SOFT};
        color: {TEXT};
    }}
    QHeaderView::section {{
        background: {SURFACE_ELEVATED};
        color: {TEXT_MUTED};
        border: none;
        border-bottom: 1px solid {BORDER};
        padding: 7px;
        font-size: 12px;
        font-weight: 600;
    }}
    QProgressBar#recreationBudgetProgress {{
        background: {APP_BACKGROUND};
        border: 1px solid {BORDER};
        border-radius: 5px;
        height: 10px;
        max-height: 10px;
        text-align: center;
    }}
    QProgressBar#recreationBudgetProgress::chunk {{
        background: {VIOLET};
        border-radius: 4px;
    }}
    QTabWidget#rulesEditorTabs::pane {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CARD}px;
    }}
    QTabWidget#rulesEditorTabs QTabBar::tab {{
        background: {APP_BACKGROUND};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER};
        padding: 8px 14px;
        font-weight: 600;
    }}
    QTabWidget#rulesEditorTabs QTabBar::tab:selected {{
        background: {SURFACE};
        color: {TEXT};
        border-bottom-color: {SURFACE};
    }}
    QDialog,
    QMessageBox {{
        background: {APP_BACKGROUND};
        color: {TEXT};
    }}
    QDialog#TaskDialog {{
        background: {APP_BACKGROUND};
    }}
    QDialog#BrowserSetupDialog {{
        background: {APP_BACKGROUND};
    }}
    QLabel#BrowserSetupIntro {{
        background: {ACCENT_SOFT};
        color: #DDE6FF;
        border: 1px solid #344264;
        border-radius: {RADIUS_CONTROL}px;
        padding: 12px;
        font-size: 15px;
        font-weight: 700;
    }}
    QLabel#BrowserSetupSteps {{
        background: {SURFACE};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CONTROL}px;
        padding: 12px;
        font-size: 13px;
        font-weight: 600;
    }}
    QDialog QFrame#DialogHeader {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CARD}px;
    }}
    QDialog QFrame#DialogPanel {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CARD}px;
    }}
    QDialog QGroupBox {{
        background: {SURFACE};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CARD}px;
        margin-top: 14px;
        padding: 14px;
        font-weight: 600;
    }}
    QDialog QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: {TEXT_MUTED};
        background: {APP_BACKGROUND};
    }}
    QDialog QLabel,
    QMessageBox QLabel {{
        color: {TEXT_MUTED};
        font-size: 13px;
    }}
    QDialog QLabel#DialogTitle {{
        color: {TEXT};
        font-size: 18px;
        font-weight: 700;
    }}
    QDialog QLabel#DialogSubtitle,
    QDialog QLabel#DialogNote {{
        color: {TEXT_MUTED};
        font-size: 12px;
    }}
    QDialog QLabel#DialogNote {{
        background: {SURFACE_ELEVATED};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CONTROL}px;
        padding: 9px 11px;
    }}
    QDialog QLabel#DialogRewardPreview {{
        background: {SURFACE_ELEVATED};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_BADGE}px;
        color: {TEXT};
        padding: 5px 9px;
        font-weight: 600;
    }}
    QDialog QFormLayout QLabel {{
        color: {TEXT_MUTED};
        font-weight: 600;
    }}
    QDialog QLineEdit[readOnlyState="true"] {{
        color: {TEXT_MUTED};
        background: {DISABLED_SURFACE};
        border-color: {BORDER};
    }}
    QDialog QDialogButtonBox {{
        background: transparent;
    }}
    QDialog QDialogButtonBox QPushButton,
    QMessageBox QPushButton {{
        min-width: 90px;
    }}
    QScrollBar:vertical {{
        background: {APP_BACKGROUND};
        border: none;
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER_STRONG};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {TEXT_SUBTLE};
    }}
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        background: transparent;
        border: none;
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: {APP_BACKGROUND};
        border: none;
        height: 10px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {BORDER_STRONG};
        border-radius: 5px;
        min-width: 24px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {TEXT_SUBTLE};
    }}
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal,
    QScrollBar::sub-page:horizontal {{
        background: transparent;
        border: none;
        width: 0;
    }}
    """
