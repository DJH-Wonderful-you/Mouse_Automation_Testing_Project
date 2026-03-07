from __future__ import annotations


def app_stylesheet() -> str:
    return """
* {
    font-family: "Segoe UI", "Microsoft YaHei UI";
    font-size: 13px;
}
QMainWindow {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:1,
        stop:0 #edf5ff,
        stop:0.55 #e7f1ff,
        stop:1 #dcecff
    );
}
QWidget#MainRoot {
    background: transparent;
}
QWidget#SidebarPanel {
    background: rgba(242, 248, 255, 0.96);
    border: 1px solid #c9dcef;
    border-radius: 14px;
}
QWidget#SidebarDivider {
    background: #c2d8ef;
}
QLabel#SidebarTitle {
    color: #1e5483;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.3px;
}
QPushButton#SidebarNavButton {
    min-height: 36px;
    border-radius: 12px;
    border: 1px solid #b8d3ee;
    background: #f2f8ff;
    color: #1f4f78;
    font-size: 14px;
    font-weight: 600;
    padding: 6px 12px;
}
QPushButton#SidebarNavButton:hover {
    background: #e4f0ff;
    border-color: #7eaedf;
}
QPushButton#SidebarNavButton:checked {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #1d7cd7,
        stop:1 #2b93f0
    );
    border: 1px solid #1f7ccf;
    color: white;
}
QPushButton#SidebarNavButton:pressed {
    background: #1b6fbe;
}
QWidget#ContentPanel {
    background: transparent;
}
QTabWidget::pane {
    border: 1px solid #ccddf0;
    border-radius: 14px;
    margin: 5px;
    padding: 5px;
    background: rgba(255, 255, 255, 0.8);
}
QTabWidget::tab-bar:left {
    left: 8px;
    top: 8px;
}
QTabBar {
    background: transparent;
}
QTabBar::tab {
    min-width: 138px;
    min-height: 44px;
    margin: 8px 10px 8px 4px;
    padding: 7px 14px;
    border-radius: 10px;
    background: #f8fbff;
    color: #1f466d;
    border: 1px solid #c7daf0;
    font-weight: 600;
}
QTabBar::tab:hover {
    background: #eef5ff;
    border-color: #7fb2e3;
}
QTabBar::tab:selected {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #1d7cd7,
        stop:1 #2b93f0
    );
    color: white;
    border: 1px solid #1f7ccf;
}
QGroupBox {
    background: rgba(255, 255, 255, 0.96);
    border: 1px solid #d2e1f0;
    border-radius: 12px;
    margin-top: 18px;
    padding-top: 10px;
    font-weight: 600;
    color: #19456e;
}
QGroupBox:hover {
    border-color: #bad2ea;
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
    padding: 0 8px 1px 8px;
    color: #1e5b90;
    background: #eef6ff;
    border-radius: 4px;
}
QLabel {
    color: #224a70;
}
QLabel#PageTitle {
    color: #1a4f7d;
    font-size: 22px;
    font-weight: 700;
}
QLabel#PageSubtitle {
    color: #4d7192;
    font-size: 13px;
    padding-bottom: 2px;
}
QWidget#PageIntroCard {
    background: #f2f8ff;
    border: 1px solid #c8dcef;
    border-radius: 10px;
}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    border: 1px solid #c4d8ec;
    border-radius: 6px;
    padding: 6px 8px;
    background: #ffffff;
    color: #153b60;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #3f8dd9;
    background: #fbfdff;
}
QSpinBox, QDoubleSpinBox {
    padding-right: 26px;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid #b9cfe5;
    border-bottom: 1px solid #b9cfe5;
    border-top-right-radius: 5px;
    background: #eef5ff;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    border-left: 1px solid #b9cfe5;
    border-top: 1px solid #b9cfe5;
    border-bottom-right-radius: 5px;
    background: #eef5ff;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background: #dcecff;
}
QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {
    background: #cde1f8;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 6px solid #3a6f9d;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid #3a6f9d;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #31618d;
    margin-right: 6px;
}
QPushButton {
    border-radius: 8px;
    padding: 7px 14px;
    border: 1px solid #b8d0e7;
    background: #f1f7ff;
    color: #1d4d77;
    font-weight: 500;
}
QPushButton:hover {
    background: #e5f1ff;
    border-color: #82afe0;
}
QPushButton:pressed {
    background: #d5e8ff;
}
QPushButton#PrimaryButton {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #1c7ad4,
        stop:1 #3599f3
    );
    border: 1px solid #1f79cc;
    color: white;
    font-weight: 600;
}
QPushButton#PrimaryButton:hover {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #176fbe,
        stop:1 #2e8fe6
    );
}
QPushButton#PrimaryButton:pressed {
    background: #1667b0;
}
QPushButton#PrimaryButton:disabled {
    background: #aac8e7;
    border: 1px solid #aac8e7;
    color: #eaf3fc;
}
QPushButton#DangerButton {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #ef4444,
        stop:1 #dc2626
    );
    border: 1px solid #cc2525;
    color: white;
    font-weight: 600;
}
QPushButton#DangerButton:hover {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #df3e3e,
        stop:1 #c91f1f
    );
}
QPushButton#DangerButton:pressed {
    background: #ba1f1f;
}
QPushButton#DangerButton:disabled {
    background: #efb1b1;
    border: 1px solid #efb1b1;
    color: #fff4f4;
}
QPushButton:disabled {
    color: #8da7c0;
    border-color: #d5e2ef;
    background: #eef4fa;
}
QProgressBar {
    border: 1px solid #c6d9ec;
    border-radius: 6px;
    text-align: center;
    background: #f3f8fe;
    color: #1c4f7a;
}
QProgressBar::chunk {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #2c87df,
        stop:1 #5bb0ff
    );
    border-radius: 5px;
}
QTextEdit {
    background: #f6fbff;
    color: #153b60;
    border: 1px solid #c8ddef;
    border-radius: 8px;
    selection-background-color: #9cc6ef;
}
QSplitter::handle {
    background: #d4e3f2;
}
QCheckBox {
    spacing: 6px;
    color: #244f78;
}
QCheckBox::indicator {
    width: 14px;
    height: 14px;
    border-radius: 3px;
    border: 1px solid #8db3da;
    background: #ffffff;
}
QCheckBox::indicator:checked {
    background: #2f8fe9;
    border: 1px solid #2b82d3;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #aac7e3;
    min-height: 26px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #7eabd7;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #aac7e3;
    min-width: 26px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover {
    background: #7eabd7;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
QToolTip {
    color: #173f64;
    background: #f3f8ff;
    border: 1px solid #b9d1e8;
    padding: 4px 6px;
}
"""
