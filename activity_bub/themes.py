"""Complete palettes shared by Qt widgets, cards, and the post reader."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    bg: str
    panel: str
    card: str
    line: str
    text: str
    muted: str
    accent: str
    accent_text: str
    selected: str
    canvas: str
    warning: str


THEMES = (
    Theme("bub", "Bub · lime", "#0b1016", "#111923", "#141f2a", "#34475a", "#e9f1f6", "#a1b4c6", "#b7ef6a", "#14200d", "#243a30", "#0d1720", "#f2c880"),
    Theme("arcade", "Arcade · violet", "#120e1d", "#1d172c", "#241d36", "#544466", "#f3ebff", "#bbadce", "#e2a5ff", "#271139", "#443052", "#171124", "#f7d58b"),
    Theme("ocean", "Ocean · cyan", "#08161b", "#10232b", "#15303a", "#375f6b", "#e5f6f9", "#9ebfc8", "#6cdef1", "#08262d", "#214b57", "#0a1c24", "#f5d08a"),
    Theme("ember", "Ember · orange", "#1b1110", "#291b18", "#34241f", "#695046", "#fff1e8", "#ceb1a2", "#ffb277", "#311808", "#563a2c", "#211613", "#f8de88"),
    Theme("terminal", "Terminal · green", "#06110b", "#0b1e12", "#11291b", "#355d40", "#d7ffe3", "#9dc4a6", "#71f6a1", "#082413", "#1d4530", "#08170d", "#ebdc83"),
    Theme("daylight", "Daylight · paper", "#f5f2ea", "#eae6dc", "#fffdf8", "#b3b8ad", "#242e2a", "#58665c", "#256a49", "#ffffff", "#dfeddf", "#e9e7df", "#875707"),
)


def get_theme(key):
    return next((t for t in THEMES if t.key == key), THEMES[0])


def stylesheet(t):
    return f"""QMainWindow, QDialog, QWidget {{ background: {t.bg}; color: {t.text}; font-size: 13px; }}
QLabel {{ background: transparent; }}
QLabel#muted {{ color: {t.muted}; }}
QLabel#pageTitle {{ font-size: 24px; font-weight: 700; }}
QPushButton {{ background: {t.panel}; border: 1px solid {t.line}; border-radius: 6px; padding: 6px 9px; }}
QPushButton:hover {{ background: {t.selected}; border-color: {t.accent}; }}
QPushButton:pressed {{ background: {t.selected}; }}
QPushButton:checked, QPushButton#primary {{ background: {t.accent}; color: {t.accent_text}; border-color: {t.accent}; font-weight: 700; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.panel}; }}
QLineEdit, QComboBox, QSpinBox {{ background: {t.panel}; border: 1px solid {t.line}; border-radius: 6px; padding: 6px; selection-background-color: {t.selected}; selection-color: {t.text}; }}
QLineEdit:focus {{ border-color: {t.accent}; }}
QComboBox::drop-down {{ border: 0; width: 20px; }}
QCheckBox {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {t.line}; border-radius: 4px; background: {t.panel}; }}
QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
QListView, QTableWidget, QListWidget, QTextBrowser {{ background: {t.bg}; border: 0; outline: 0; selection-background-color: {t.selected}; }}
QTableWidget {{ gridline-color: {t.line}; alternate-background-color: {t.panel}; }}
QHeaderView::section {{ background: {t.panel}; border: 0; border-bottom: 1px solid {t.line}; color: {t.muted}; padding: 8px; }}
QScrollBar:vertical {{ background: {t.bg}; width: 9px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t.line}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QToolTip {{ color: {t.text}; background: {t.panel}; border: 1px solid {t.line}; padding: 6px; }}
QMenu {{ background: {t.panel}; border: 1px solid {t.line}; padding: 5px; }}
QMenu::item {{ padding: 7px 20px; }}
QMenu::item:selected {{ background: {t.selected}; border-radius: 4px; }}
QProgressBar {{ border: 1px solid {t.line}; border-radius: 5px; background: {t.panel}; text-align: center; min-height: 14px; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 4px; }}
"""
