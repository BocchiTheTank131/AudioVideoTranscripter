from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette, QFontDatabase
import os
from pathlib import Path
from utils.paths import resource_root


def apply_theme(app, theme='system'):
    dark = theme == 'dark' or (theme == 'system' and app.styleHints().colorScheme() == Qt.ColorScheme.Dark)
    bg, surface, card, text, muted, border, field = (
        ('#0e1320', '#151c2b', '#1b2435', '#eef2fa', '#97a5bc', '#2a354a', '#111827') if dark else
        ('#f3f5fa', '#ffffff', '#edf0f8', '#182237', '#61708a', '#dce2ef', '#ffffff'))
    app.setStyle('Fusion')
    if os.name == 'nt':
        # The offscreen Qt platform does not enumerate Windows fonts. Explicitly
        # register the same system fonts used by the native window for reliable QA.
        for filename in ('segoeui.ttf', 'segoeuib.ttf'):
            path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / filename
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    palette = QPalette()
    for role, color in [(QPalette.ColorRole.Window, bg), (QPalette.ColorRole.Base, surface),
                        (QPalette.ColorRole.AlternateBase, card), (QPalette.ColorRole.WindowText, text),
                        (QPalette.ColorRole.Text, text), (QPalette.ColorRole.Button, card),
                        (QPalette.ColorRole.ButtonText, text), (QPalette.ColorRole.Highlight, '#8274e8'),
                        (QPalette.ColorRole.HighlightedText, '#ffffff')]:
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    check = (resource_root() / 'assets/check.svg').as_posix()
    app.setStyleSheet(f'''
        QWidget {{ color:{text}; font-family:'Segoe UI'; font-size:13px; }}
        QMainWindow, QDialog {{ background:{bg}; }}
        QFrame#card, QFrame#panel {{ background:{surface}; border:1px solid {border}; border-radius:12px; }}
        QFrame#drop {{ background:{field}; border:2px dashed {border}; border-radius:12px; }}
        QLabel#title {{ font-size:27px; font-weight:700; }}
        QLabel#section {{ font-size:16px; font-weight:600; }}
        QLabel#muted {{ color:{muted}; }}
        QLabel#badge {{ background:{card}; color:{muted}; padding:6px 12px; border-radius:10px; }}
        QLabel#metric {{ font-size:15px; font-weight:600; }}
        QPushButton {{ background:{card}; border:1px solid {border}; padding:8px 14px; border-radius:7px; font-weight:600; }}
        QPushButton:hover {{ border-color:#8c85ee; background:{border}; }}
        QPushButton:pressed {{ background:#6d63c5; }}
        QPushButton:disabled {{ color:{muted}; background:{surface}; }}
        QPushButton#primary {{ background:#8274e8; color:#ffffff; border:0; padding:12px 16px; }}
        QPushButton#primary:hover {{ background:#9589f1; }}
        QPushButton#primary:disabled {{ background:{border}; color:{muted}; }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background:{field}; border:1px solid {border}; border-radius:6px; padding:7px; selection-background-color:#8274e8; }}
        QComboBox QAbstractItemView {{ background:{surface}; selection-background-color:#8274e8; padding:6px; }}
        QCheckBox {{ spacing:8px; padding:3px 0; }}
        QCheckBox::indicator {{ width:16px; height:16px; border:1px solid {muted}; border-radius:4px; background:{field}; }}
        QCheckBox::indicator:checked {{ background:#8274e8; border-color:#b4abff; image:url({check}); }}
        QProgressBar {{ border:0; background:{card}; height:8px; border-radius:4px; text-align:center; }}
        QProgressBar::chunk {{ background:#8b7eef; border-radius:4px; }}
        QTextEdit, QPlainTextEdit, QTableWidget {{ background:{surface}; border:1px solid {border}; border-radius:8px; padding:8px; selection-background-color:#6054a7; }}
        QHeaderView::section {{ background:{card}; color:{muted}; border:0; padding:8px; font-weight:600; }}
        QTableWidget::item {{ padding:6px; }}
        QTabWidget::pane {{ border:0; }}
        QTabBar::tab {{ padding:10px 16px; color:{muted}; border-bottom:2px solid transparent; }}
        QTabBar::tab:selected {{ color:{text}; border-bottom-color:#8b7eef; }}
        QScrollBar:vertical {{ background:{surface}; width:10px; border:0; }}
        QScrollBar::handle:vertical {{ background:{border}; min-height:25px; border-radius:5px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        QToolTip {{ background:{card}; color:{text}; border:1px solid {border}; padding:6px; }}
        QStatusBar {{ color:{muted}; }}
    ''')
