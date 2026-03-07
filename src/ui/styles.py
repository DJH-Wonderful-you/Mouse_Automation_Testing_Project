from __future__ import annotations


def app_stylesheet() -> str:
    return """
QMainWindow {
    background: #edf2f7;
}
QTabWidget::pane {
    border: 1px solid #d4dce6;
    border-radius: 10px;
    background: transparent;
}
QTabBar::tab {
    min-width: 108px;
    min-height: 24px;
    margin: 3px 6px;
    padding: 4px 10px;
    border-radius: 10px;
    background: #f8fbfe;
    color: #2e4256;
    border: 1px solid #d7e2ec;
    font-weight: 500;
}
QTabBar::tab:selected {
    background: #12948a;
    color: white;
    border: 1px solid #12948a;
}
QGroupBox {
    background: white;
    border: 1px solid #d9e3ee;
    border-radius: 10px;
    margin-top: 18px;
    padding-top: 8px;
    font-weight: 600;
    color: #1f3a4d;
}
QScrollArea {
    border: none;
    background: transparent;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 1px;
    padding: 0 8px;
    color: #244256;
    background: #edf2f7;
    border-radius: 4px;
}
QLabel {
    color: #22384a;
}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    border: 1px solid #c8d5e3;
    border-radius: 6px;
    padding: 6px 8px;
    background: #ffffff;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #0d9488;
}
QPushButton {
    border-radius: 7px;
    padding: 7px 12px;
    border: 1px solid #c7d3e0;
    background: #f7fafc;
    color: #1f3345;
}
QPushButton:hover {
    background: #ecf4fb;
}
QPushButton#PrimaryButton {
    background: #12948a;
    border: none;
    color: white;
    font-weight: 600;
}
QPushButton#DangerButton {
    background: #ef4444;
    border: none;
    color: white;
    font-weight: 600;
}
QProgressBar {
    border: 1px solid #c9d6e4;
    border-radius: 6px;
    text-align: center;
    background: #f8fafc;
    color: #203040;
}
QProgressBar::chunk {
    background: #14b8a6;
    border-radius: 5px;
}
QSplitter::handle {
    background: #dbe4ee;
}
"""
