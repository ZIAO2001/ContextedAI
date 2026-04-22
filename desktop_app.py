import atexit
import os
import subprocess
import sys
import time

import httpx
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView


SERVER_PORT = 8020
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"


class TitleBar(QWidget):
    def __init__(self, window: QMainWindow):
        super().__init__(window)
        self.window = window
        self.drag_offset = QPoint()

        self.setFixedHeight(38)
        self.setStyleSheet(
            """
            QWidget {
                background: #17181d;
                color: #e6e6ec;
                border-bottom: 1px solid #2b2d36;
            }
            QLabel {
                font-size: 12px;
                color: #b9bcc8;
                padding-left: 8px;
            }
            QPushButton {
                border: none;
                min-width: 12px;
                max-width: 12px;
                min-height: 12px;
                max-height: 12px;
                border-radius: 6px;
                padding: 0;
            }
            QPushButton#close {
                background: #ff5f57;
            }
            QPushButton#min {
                background: #febc2e;
            }
            QPushButton#max {
                background: #28c840;
            }
            QPushButton#close:hover { background: #ff7b74; }
            QPushButton#min:hover { background: #ffd15a; }
            QPushButton#max:hover { background: #48dd63; }
            QPushButton#settings {
                min-width: 52px;
                max-width: 52px;
                min-height: 24px;
                max-height: 24px;
                border-radius: 6px;
                background: #2b2f3b;
                color: #d8dbe5;
                font-size: 11px;
                padding: 0 8px;
            }
            QPushButton#settings:hover {
                background: #3a4050;
            }
            """
        )

        layout = QHBoxLayout()
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        self.close_btn = QPushButton("")
        self.min_btn = QPushButton("")
        self.max_btn = QPushButton("")
        self.settings_btn = QPushButton("设置")
        self.title = QLabel("ContextedAI Desktop")

        self.close_btn.setObjectName("close")
        self.min_btn.setObjectName("min")
        self.max_btn.setObjectName("max")
        self.settings_btn.setObjectName("settings")
        self.close_btn.setToolTip("关闭")
        self.min_btn.setToolTip("最小化")
        self.max_btn.setToolTip("最大化/还原")
        self.settings_btn.setToolTip("打开设置")

        layout.addWidget(self.close_btn)
        layout.addWidget(self.min_btn)
        layout.addWidget(self.max_btn)
        layout.addSpacing(10)
        layout.addWidget(self.title)
        layout.addStretch(1)
        layout.addWidget(self.settings_btn)
        layout.addSpacing(8)
        self.setLayout(layout)

        self.min_btn.clicked.connect(self.window.showMinimized)
        self.max_btn.clicked.connect(self.toggle_maximize)
        self.close_btn.clicked.connect(self.window.close)
        self.settings_btn.clicked.connect(self.window.open_web_settings)

    def toggle_maximize(self) -> None:
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton and not self.window.isMaximized():
            self.window.move(event.globalPosition().toPoint() - self.drag_offset)
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.toggle_maximize()
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ContextedAI Desktop")
        self.resize(1400, 900)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)

        self.title_bar = TitleBar(self)
        self.web = QWebEngineView()
        profile = self.web.page().profile()
        profile.setHttpCacheType(QWebEngineProfile.NoCache)
        profile.clearHttpCache()
        self.web.load(QUrl(SERVER_URL))
        self.web.setStyleSheet("background:#111218;")

        root = QWidget()
        layout = QVBoxLayout()
        layout.addWidget(self.title_bar)
        layout.addWidget(self.web)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        root.setLayout(layout)
        root.setStyleSheet("background:#111218;")
        self.setCentralWidget(root)

    def open_web_settings(self) -> None:
        self.web.page().runJavaScript(
            "window.__openDesktopSettings && window.__openDesktopSettings();"
        )


def _wait_server_ready(timeout_sec: float = 20.0) -> None:
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            response = httpx.get(f"{SERVER_URL}/health", timeout=1.0)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError("Backend server did not start in time.")


def _start_backend() -> subprocess.Popen:
    cmd = [sys.executable, "-m", "uvicorn", "app:app", "--port", str(SERVER_PORT)]
    env = os.environ.copy()
    process = subprocess.Popen(cmd, env=env)
    _wait_server_ready()
    return process


def main() -> int:
    backend = _start_backend()
    atexit.register(lambda: backend.poll() is None and backend.terminate())

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    code = app.exec()

    if backend.poll() is None:
        backend.terminate()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
