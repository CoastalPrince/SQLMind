"""
ui/results_panel.py
Displays query results, validation outcome, and execution stats.
NOTE: GenerationResult is defined locally here -- the app has no AI deps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QFrame,
    QPushButton, QAbstractItemView, QHeaderView,
    QFileDialog, QSizePolicy,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QBrush

import pandas as pd

from core.db_connector import QueryResult


# ── Local stub (mirrors server's GenerationResult) ────────────────────────────
# The app never runs AI inference -- it receives results via HTTP from the server.
# We only need the shape, not the server-side import.

@dataclass
class GenerationResult:
    sql: str = ""
    valid: bool = False
    attempts: int = 0
    stage: str = ""
    error: str = ""
    correction_log: list[dict] = field(default_factory=list)


# ── Results Panel ─────────────────────────────────────────────────────────────

class ResultsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._current_df: pd.DataFrame | None = None

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 12)
        layout.setSpacing(6)

        # ── Toolbar ──────────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        panel_label = QLabel("RESULTS")
        panel_label.setObjectName("section_label")
        toolbar.addWidget(panel_label)
        toolbar.addStretch()

        self.row_count_label = QLabel("")
        self.row_count_label.setStyleSheet(
            "font-size:10px; color:#2d3f54; font-family:Consolas;"
        )
        toolbar.addWidget(self.row_count_label)

        self.exec_time_label = QLabel("")
        self.exec_time_label.setStyleSheet(
            "font-size:10px; color:#2d3f54; font-family:Consolas;"
        )
        toolbar.addWidget(self.exec_time_label)

        self.btn_export = QPushButton("EXPORT CSV")
        self.btn_export.setStyleSheet(
            "font-size:9px; padding:4px 10px; color:#2d3f54; font-weight:700;"
            "background:#0f1520; border:1px solid #1a2535; border-radius:3px;"
            "font-family:Consolas; letter-spacing:1px;"
        )
        self.btn_export.clicked.connect(self._export_csv)
        self.btn_export.setVisible(False)
        toolbar.addWidget(self.btn_export)

        layout.addLayout(toolbar)

        # ── Validation panel ──────────────────────────────────────────────────
        self.val_panel = QFrame()
        self.val_panel.setVisible(False)
        val_layout = QVBoxLayout(self.val_panel)
        val_layout.setContentsMargins(12, 10, 12, 10)
        val_layout.setSpacing(4)

        val_header = QHBoxLayout()
        self.val_icon  = QLabel("")
        self.val_icon.setStyleSheet("font-size:13px;")
        self.val_title = QLabel("")
        self.val_title.setStyleSheet(
            "font-size:11px; font-weight:700; color:#e2eaf5; font-family:Consolas;"
        )
        val_header.addWidget(self.val_icon)
        val_header.addWidget(self.val_title)
        val_header.addStretch()
        val_layout.addLayout(val_header)

        self.val_detail = QLabel("")
        self.val_detail.setWordWrap(True)
        self.val_detail.setStyleSheet(
            "font-size:10px; color:#6b8099; padding-left:20px; font-family:Consolas;"
        )
        val_layout.addWidget(self.val_detail)

        self.val_log = QLabel("")
        self.val_log.setWordWrap(True)
        self.val_log.setStyleSheet(
            "font-size:10px; color:#2d3f54; padding-left:20px; font-family:Consolas;"
        )
        self.val_log.setVisible(False)
        val_layout.addWidget(self.val_log)

        layout.addWidget(self.val_panel)

        # ── DB Error panel ────────────────────────────────────────────────────
        self.err_panel = QFrame()
        self.err_panel.setObjectName("val_panel_error")
        self.err_panel.setVisible(False)
        err_layout = QVBoxLayout(self.err_panel)
        err_layout.setContentsMargins(12, 10, 12, 10)

        err_header = QHBoxLayout()
        err_icon = QLabel("!")
        err_icon.setStyleSheet(
            "font-size:12px; font-weight:900; color:#ff4f4f; font-family:Consolas;"
        )
        self.err_title = QLabel("Query Error")
        self.err_title.setStyleSheet(
            "font-size:11px; font-weight:700; color:#ff4f4f; font-family:Consolas;"
        )
        err_header.addWidget(err_icon)
        err_header.addWidget(self.err_title)
        err_header.addStretch()
        err_layout.addLayout(err_header)

        self.err_detail = QLabel("")
        self.err_detail.setWordWrap(True)
        self.err_detail.setStyleSheet(
            "font-size:10px; color:#6b8099; padding-left:16px; font-family:Consolas;"
        )
        err_layout.addWidget(self.err_detail)
        layout.addWidget(self.err_panel)

        # ── Results table ─────────────────────────────────────────────────────
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setSortingEnabled(True)
        self.table.setVisible(False)
        layout.addWidget(self.table, 1)

        # ── Empty state ───────────────────────────────────────────────────────
        self.empty_label = QLabel("run a query to see results here")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(
            "font-size:11px; color:#1a2535; padding:40px; font-family:Consolas;"
        )
        layout.addWidget(self.empty_label)

    # ── Public API ────────────────────────────────────────────────────────────

    def show_validation_result(self, gen_result):
        """Display AI generation + validation outcome.
        Accepts any object with .valid, .attempts, .stage, .error, .correction_log
        """
        self.val_panel.setVisible(True)

        if gen_result.valid:
            self.val_panel.setObjectName("val_panel_pass")
            self.val_icon.setText("OK")
            self.val_icon.setStyleSheet(
                "font-size:9px; font-weight:900; color:#2ecc8a; font-family:Consolas;"
            )
            self.val_title.setText(
                f"SQL valid  --  {gen_result.attempts} attempt(s)"
            )
            self.val_title.setStyleSheet(
                "font-size:11px; font-weight:700; color:#2ecc8a; font-family:Consolas;"
            )
            self.val_detail.setText(
                "passed: syntax  |  schema  |  semantic"
            )
        else:
            self.val_panel.setObjectName("val_panel_warn")
            self.val_icon.setText("!!")
            self.val_icon.setStyleSheet(
                "font-size:9px; font-weight:900; color:#ffb547; font-family:Consolas;"
            )
            self.val_title.setText(
                f"could not fully correct after {gen_result.attempts} attempt(s)"
            )
            self.val_title.setStyleSheet(
                "font-size:11px; font-weight:700; color:#ffb547; font-family:Consolas;"
            )
            self.val_detail.setText(f"[{gen_result.stage}]  {gen_result.error}")

        # Correction log
        if gen_result.correction_log:
            log_lines = []
            for entry in gen_result.correction_log:
                status = "PASS" if entry["valid"] else f"FAIL [{entry['stage']}]"
                log_lines.append(f"  attempt {entry['attempt']} : {status}")
                if not entry["valid"] and entry["error"]:
                    log_lines.append(f"    -> {entry['error'][:100]}")
            self.val_log.setText("\n".join(log_lines))
            self.val_log.setVisible(len(gen_result.correction_log) > 1)

        # Force QSS dynamic property-based repaint (more reliable than objectName swap)
        self.val_panel.setProperty(
            "validation_state", "pass" if gen_result.valid else "warn"
        )
        self.val_panel.style().unpolish(self.val_panel)
        self.val_panel.style().polish(self.val_panel)

    def show_query_result(self, result: QueryResult):
        """Display executed query results in the table."""
        self.err_panel.setVisible(False)
        self.empty_label.setVisible(False)

        if not result.success:
            self.table.setVisible(False)
            self.err_panel.setVisible(True)
            self.err_detail.setText(result.error)
            self.row_count_label.setText("")
            self.exec_time_label.setText(f"{result.execution_ms:.0f}ms")
            self.btn_export.setVisible(False)
            return

        if result.data is None or (result.data.empty and result.row_count == 0):
            self.table.setVisible(False)
            self.empty_label.setText(
                f"query OK  --  {result.row_count} row(s) affected"
            )
            self.empty_label.setStyleSheet(
                "font-size:11px; color:#2ecc8a; padding:40px; font-family:Consolas;"
            )
            self.empty_label.setVisible(True)
            self.exec_time_label.setText(f"{result.execution_ms:.0f}ms")
            return

        self._current_df = result.data
        self._populate_table(result.data)
        self.row_count_label.setText(f"{len(result.data):,} rows")
        self.exec_time_label.setText(f"{result.execution_ms:.1f}ms")
        self.btn_export.setVisible(True)

    def show_db_error(self, error: str):
        self.err_panel.setVisible(True)
        self.err_detail.setText(error)
        self.table.setVisible(False)

    def clear(self):
        self.val_panel.setVisible(False)
        self.err_panel.setVisible(False)
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.table.setVisible(False)
        self.empty_label.setText("run a query to see results here")
        self.empty_label.setStyleSheet(
            "font-size:11px; color:#1a2535; padding:40px; font-family:Consolas;"
        )
        self.empty_label.setVisible(True)
        self.row_count_label.setText("")
        self.exec_time_label.setText("")
        self.btn_export.setVisible(False)
        self._current_df = None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _populate_table(self, df: pd.DataFrame):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(df))
        self.table.setColumnCount(len(df.columns))
        self.table.setHorizontalHeaderLabels(list(df.columns))

        MAX_ROWS = 1000
        for r in range(min(len(df), MAX_ROWS)):
            for c in range(len(df.columns)):
                val  = df.iloc[r, c]
                text = "" if val is None else str(val)
                item = QTableWidgetItem(text)
                item.setFont(QFont("Consolas", 10))
                item.setForeground(QBrush(QColor("#9ab0c8")))
                self.table.setItem(r, c, item)

        if len(df) > MAX_ROWS:
            self.row_count_label.setText(
                f"{MAX_ROWS:,} / {len(df):,} rows (capped)"
            )

        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)
        self.table.setVisible(True)
        self.empty_label.setVisible(False)

    def _export_csv(self):
        if self._current_df is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export to CSV", "results.csv", "CSV Files (*.csv)"
        )
        if path:
            try:
                self._current_df.to_csv(path, index=False)
            except Exception as e:
                self.show_db_error(f"Export failed: {e}")
