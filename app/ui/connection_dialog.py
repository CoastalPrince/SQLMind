"""
ui/connection_dialog.py
Modal dialog for entering DB connection details.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QComboBox, QSpinBox,
    QPushButton, QFrame, QProgressBar
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont

from core.db_connector import DBConnector, ConnectionConfig


class ConnectWorker(QObject):
    finished = pyqtSignal(bool, str)

    def __init__(self, connector, config):
        super().__init__()
        self.connector = connector
        self.config = config

    def run(self):
        ok, err = self.connector.connect(self.config)
        self.finished.emit(ok, err)


class ConnectionDialog(QDialog):
    connected = pyqtSignal(object, object)  # connector, config

    def __init__(self, parent=None, existing_config: ConnectionConfig = None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Database")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._connector = DBConnector()
        self._thread = None
        self._worker = None
        self._build_ui(existing_config)

    def _build_ui(self, cfg: ConnectionConfig = None):
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Header ──
        header = QFrame()
        header.setStyleSheet("background:#13161b; padding:20px 24px; border-bottom:1px solid #2a3040;")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("NEW CONNECTION")
        title.setStyleSheet("font-size:14px; font-weight:bold; color:#00d4aa; letter-spacing:3px;")
        subtitle = QLabel("PostgreSQL · MySQL")
        subtitle.setStyleSheet("font-size:11px; color:#4a5568; letter-spacing:1px;")
        h_layout.addWidget(title)
        h_layout.addWidget(subtitle)
        layout.addWidget(header)

        # ── Form ──
        form_frame = QFrame()
        form_frame.setStyleSheet("padding:20px 24px;")
        form = QFormLayout(form_frame)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        def label(text):
            l = QLabel(text)
            l.setStyleSheet("font-size:11px; color:#4a5568; letter-spacing:1px;")
            return l

        # DB Type
        self.db_type = QComboBox()
        self.db_type.addItems(["postgresql", "mysql"])
        self.db_type.currentTextChanged.connect(self._on_type_change)
        form.addRow(label("TYPE"), self.db_type)

        # Host
        self.host = QLineEdit(cfg.host if cfg else "localhost")
        self.host.setPlaceholderText("e.g. 127.0.0.1")
        form.addRow(label("HOST"), self.host)

        # Port
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(cfg.port if cfg else 5432)
        form.addRow(label("PORT"), self.port)

        # Database
        self.database = QLineEdit(cfg.database if cfg else "")
        self.database.setPlaceholderText("database name")
        form.addRow(label("DATABASE"), self.database)

        # Username
        self.username = QLineEdit(cfg.username if cfg else "")
        self.username.setPlaceholderText("username")
        form.addRow(label("USER"), self.username)

        # Password
        self.password = QLineEdit(cfg.password if cfg else "")
        self.password.setPlaceholderText("password")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(label("PASSWORD"), self.password)

        if cfg:
            self.db_type.setCurrentText(cfg.db_type)

        layout.addWidget(form_frame)

        # ── Status ──
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("padding:0 24px; font-size:11px; color:#e05252; min-height:20px;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setStyleSheet("margin:0 24px 8px 24px;")
        layout.addWidget(self.progress)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(24, 8, 24, 20)
        btn_row.setSpacing(10)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setObjectName("btn_primary")
        self.btn_connect.clicked.connect(self._do_connect)
        self.btn_connect.setDefault(True)

        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_connect)
        layout.addLayout(btn_row)

    def _on_type_change(self, db_type: str):
        self.port.setValue(5432 if db_type == "postgresql" else 3306)

    def _do_connect(self):
        host     = self.host.text().strip()
        database = self.database.text().strip()
        username = self.username.text().strip()

        if not host or not database or not username:
            self.status_label.setText("Host, database and username are required.")
            return

        config = ConnectionConfig(
            db_type  = self.db_type.currentText(),
            host     = host,
            port     = self.port.value(),
            database = database,
            username = username,
            password = self.password.text(),
        )

        self.btn_connect.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.progress.setVisible(True)
        self.status_label.setText("Connecting...")
        self.status_label.setStyleSheet("padding:0 24px; font-size:11px; color:#0099ff;")

        # Run connection in background thread
        self._thread  = QThread()
        self._worker  = ConnectWorker(self._connector, config)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_connect_done)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

        self._config = config

    def _on_connect_done(self, ok: bool, error: str):
        self.progress.setVisible(False)
        self.btn_cancel.setEnabled(True)

        if ok:
            self.status_label.setText("Connected!")
            self.status_label.setStyleSheet("padding:0 24px; font-size:11px; color:#4ecb71;")
            self.connected.emit(self._connector, self._config)
            self.accept()
        else:
            self.status_label.setText(f"Failed: {error}")
            self.status_label.setStyleSheet("padding:0 24px; font-size:11px; color:#e05252;")
            self.btn_connect.setEnabled(True)
