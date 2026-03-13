"""
ui/main_window.py  --  SQLMind v3
Two-tab layout:
  [SQL GENERATOR]   question -> generated SQL -> run -> results table
  [TABLE INSIGHTS]  explain selected / all + column detail pane

Schema Viewer dialog (non-modal): shows full column breakdown per table.
"""
from __future__ import annotations
import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QTextEdit, QPushButton, QFrame, QApplication,
    QProgressBar, QSizePolicy, QTabWidget, QDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QScrollArea,
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, QObject
from PyQt6.QtGui  import QKeySequence, QShortcut, QFont, QColor

from core.db_connector  import DBConnector, ConnectionConfig
from core.api_client    import SQLMindClient
from core.schema_matcher import select_tables
from ui.connection_dialog import ConnectionDialog
from ui.schema_browser    import SchemaBrowser
from ui.results_panel     import ResultsPanel


# ── Workers ────────────────────────────────────────────────────────────────────
class BgWorker(QObject):
    finished = pyqtSignal(object)
    def __init__(self, fn):
        super().__init__(); self._fn = fn
    def run(self):
        try:    self.finished.emit(self._fn())
        except Exception as e: self.finished.emit(e)

class ExecWorker(QObject):
    finished = pyqtSignal(object)
    def __init__(self, connector, sql):
        super().__init__(); self.connector = connector; self.sql = sql
    def run(self):
        self.finished.emit(self.connector.execute_query(self.sql))

def _run_bg(parent, worker) -> QThread:
    t = QThread(parent)
    worker.moveToThread(t)
    t.started.connect(worker.run)
    t.start()
    return t


# ── Schema Viewer dialog ───────────────────────────────────────────────────────
class SchemaViewerDialog(QDialog):
    """Full column breakdown for every selected table. Non-modal."""

    def __init__(self, parent, selected_schemas: list, table_info: dict):
        super().__init__(parent)
        self.setWindowTitle("Schema Viewer")
        self.setMinimumSize(780, 540)
        self.resize(900, 620)
        self.setModal(False)
        self.setStyleSheet(parent.styleSheet())
        self._build(selected_schemas, table_info)

    def _build(self, selected, tinfo):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ────────────────────────────────────────────────────────
        hdr = QFrame()
        hdr.setFixedHeight(54)
        hdr.setStyleSheet("background:#0a0d12; border-bottom:1px solid #1a2130;")
        hl  = QHBoxLayout(hdr)
        hl.setContentsMargins(22, 0, 22, 0)
        hl.setSpacing(14)

        title = QLabel("SCHEMA  VIEWER")
        title.setStyleSheet(
            "font-family:Consolas,monospace; font-size:12px; font-weight:700;"
            "color:#00e5b0; letter-spacing:3px;"
        )
        sub = QLabel(f"{len(selected)} table(s)")
        sub.setStyleSheet("font-size:11px; color:#4a5f74; padding-left:6px;")
        hl.addWidget(title)
        hl.addWidget(sub)
        hl.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setFixedSize(80, 30)
        close_btn.clicked.connect(self.close)
        hl.addWidget(close_btn)
        root.addWidget(hdr)

        # ── Scrollable cards ──────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none; background:#0d1117;}")

        body = QWidget()
        body.setStyleSheet("background:#0d1117;")
        bv   = QVBoxLayout(body)
        bv.setContentsMargins(22, 18, 22, 22)
        bv.setSpacing(18)

        for schema, table, schema_str in selected:
            info = tinfo.get((schema, table))
            bv.addWidget(self._make_card(schema, table, schema_str, info))

        bv.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll)

    def _make_card(self, schema, table, schema_str, info):
        card = QFrame()
        card.setStyleSheet(
            "QFrame{background:#141920; border:1px solid #1e2a3a;"
            "border-radius:8px;}"
        )
        cv = QVBoxLayout(card)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        # Card header
        ch = QFrame()
        ch.setStyleSheet(
            "background:#0f1520; border-radius:8px 8px 0 0;"
            "border-bottom:1px solid #1e2a3a;"
        )
        ch.setFixedHeight(52)
        chl = QHBoxLayout(ch)
        chl.setContentsMargins(18, 0, 18, 0)
        chl.setSpacing(10)

        tname = QLabel(table)
        tname.setStyleSheet(
            "font-family:Consolas,monospace; font-size:15px; font-weight:700;"
            "color:#dce6f0; background:transparent; border:none;"
        )
        sname = QLabel(f"  {schema}")
        sname.setStyleSheet(
            "font-size:11px; color:#4a5f74; background:transparent; border:none;"
        )
        chl.addWidget(tname)
        chl.addWidget(sname)
        chl.addStretch()

        if info and info.row_count:
            rc = QLabel(f"{info.row_count:,} rows")
            rc.setStyleSheet(
                "font-size:10px; color:#4a5f74; background:#0a0d12;"
                "border:1px solid #1a2130; border-radius:3px; padding:3px 10px;"
                "font-family:Consolas,monospace; border:none;"
            )
            chl.addWidget(rc)

        n_cols = len(info.columns) if info and info.columns else 0
        cc = QLabel(f"{n_cols} columns")
        cc.setStyleSheet(
            "font-size:10px; color:#00e5b070; background:#00e5b010;"
            "border:1px solid #00e5b030; border-radius:3px; padding:3px 10px;"
            "font-family:Consolas,monospace;"
        )
        chl.addWidget(cc)
        cv.addWidget(ch)

        # Columns table
        if info and info.columns:
            cols = info.columns
            tbl  = QTableWidget(len(cols), 5)
            tbl.setHorizontalHeaderLabels(["Column", "Type", "PK", "FK", "Nullable"])
            tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            tbl.verticalHeader().setVisible(False)
            tbl.setShowGrid(True)
            tbl.setAlternatingRowColors(True)
            tbl.setSortingEnabled(True)
            tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            for i in (2, 3, 4):
                tbl.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            tbl.setColumnWidth(2, 48)
            tbl.setColumnWidth(3, 48)
            tbl.setColumnWidth(4, 80)
            tbl.setStyleSheet(
                "QTableWidget{"
                "  background:#141920; border:none;"
                "  alternate-background-color:#111820;"
                "  gridline-color:#1a2130;"
                "  font-size:12px;"
                "}"
                "QTableWidget::item{padding:7px 12px; color:#aabbd0; border:none;}"
                "QTableWidget::item:selected{background:#00e5b012; color:#00e5b0;}"
                "QHeaderView::section{"
                "  background:#0f1520; border:none;"
                "  border-bottom:1px solid #1a2130;"
                "  border-right:1px solid #1a2130;"
                "  padding:8px 12px;"
                "  font-family:Consolas,monospace; font-size:9px;"
                "  font-weight:700; color:#4a5f74; letter-spacing:1px;"
                "}"
            )

            for r, col in enumerate(cols):
                def mk(text, fg="#aabbd0", bold=False, align_center=False):
                    it = QTableWidgetItem(text)
                    it.setForeground(QColor(fg))
                    it.setFont(QFont("Consolas", 10,
                                     QFont.Weight.Bold if bold else QFont.Weight.Normal))
                    if align_center:
                        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    return it

                tbl.setItem(r, 0, mk(col.name,
                                     "#dce6f0" if col.is_primary else "#aabbd0",
                                     bold=col.is_primary))
                tbl.setItem(r, 1, mk(col.data_type, "#6b8099"))
                tbl.setItem(r, 2, mk("YES" if col.is_primary else "—",
                                     "#00e5b0" if col.is_primary else "#2a3a4a",
                                     align_center=True))
                tbl.setItem(r, 3, mk("YES" if col.is_foreign else "—",
                                     "#3b9eff" if col.is_foreign else "#2a3a4a",
                                     align_center=True))
                tbl.setItem(r, 4, mk("YES" if col.nullable else "NO",
                                     "#8899aa" if col.nullable else "#f5a623",
                                     align_center=True))

            tbl.setFixedHeight(min(len(cols) * 32 + 38, 340))
            cv.addWidget(tbl)
        else:
            fallback = QLabel(f"  {schema_str}")
            fallback.setStyleSheet(
                "font-family:Consolas,monospace; font-size:11px; color:#4a5f74;"
                "padding:14px 18px;"
            )
            cv.addWidget(fallback)

        return card


# ── Main Window ────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SQLMind  -  AI Query Engine")
        self.resize(1600, 960)
        self.setMinimumSize(1100, 700)

        self._connector: DBConnector | None     = None
        self._config:    ConnectionConfig | None = None
        self._api     = SQLMindClient()
        self._selected_schemas: list = []
        self._api_ready  = False
        self._api_device = ""

        # keep workers alive (prevent GC mid-thread)
        self._hw = self._ht = None
        self._gw = self._gt = None
        self._ew = self._et = None
        self._xw = self._xt = None

        self._load_stylesheet()
        self._build_ui()
        self._wire_signals()

        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._poll_health)
        self._health_timer.start(4000)
        QTimer.singleShot(600, self._poll_health)

    # ── Stylesheet ─────────────────────────────────────────────────────────────
    def _load_stylesheet(self):
        p = os.path.join(os.path.dirname(__file__), "..", "assets", "style.qss")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    self.setStyleSheet(f.read())
            except UnicodeDecodeError:
                with open(p, encoding="latin-1") as f:
                    self.setStyleSheet(f.read())

    # ══════════════════════════════════════════════════════════════════
    # ROOT layout
    # ══════════════════════════════════════════════════════════════════
    def _build_ui(self):
        root = QWidget()
        root.setStyleSheet("background:#0a0d12;")
        self.setCentralWidget(root)
        rl = QHBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        outer = QSplitter(Qt.Orientation.Horizontal)
        outer.setHandleWidth(1)
        outer.setChildrenCollapsible(False)

        self.schema_browser = SchemaBrowser()
        self.schema_browser.setMinimumWidth(200)
        self.schema_browser.setMaximumWidth(300)
        outer.addWidget(self.schema_browser)
        outer.addWidget(self._build_centre())
        outer.setSizes([240, 1360])

        rl.addWidget(outer)
        self._build_statusbar()

    # ── Centre: topbar + tabs ──────────────────────────────────────────────────
    def _build_centre(self) -> QWidget:
        w  = QWidget()
        w.setObjectName("content_area")
        lo = QVBoxLayout(w)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)
        lo.addWidget(self._build_topbar())
        lo.addWidget(self._build_tabs(), 1)
        return w

    # ── Top bar ────────────────────────────────────────────────────────────────
    def _build_topbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topbar")
        bar.setFixedHeight(52)
        lo  = QHBoxLayout(bar)
        lo.setContentsMargins(18, 0, 18, 0)
        lo.setSpacing(10)

        # Active-table chip
        self.schema_tag = QLabel("  No table selected  ")
        self.schema_tag.setFixedHeight(32)
        self.schema_tag.setStyleSheet(
            "background:#141920; border:1px solid #1a2130; border-radius:6px;"
            "padding:0 16px; font-size:12px; color:#4a5f74;"
            "font-family:Consolas,monospace;"
        )
        lo.addWidget(self.schema_tag)

        self.auto_badge = QLabel("  AUTO  ")
        self.auto_badge.setFixedHeight(22)
        self.auto_badge.setStyleSheet(
            "font-family:Consolas,monospace; font-size:9px; font-weight:700;"
            "color:#f5a623; background:#f5a62314; border:1px solid #f5a62340;"
            "border-radius:3px; padding:0 8px; letter-spacing:1px;"
        )
        self.auto_badge.setVisible(False)
        lo.addWidget(self.auto_badge)

        self.btn_view_schema = QPushButton("View Schema")
        self.btn_view_schema.setFixedHeight(32)
        self.btn_view_schema.setEnabled(False)
        self.btn_view_schema.setStyleSheet(
            "font-size:11px; font-weight:600; padding:0 14px;"
            "color:#8899aa; background:#141920;"
            "border:1px solid #1a2130; border-radius:6px;"
        )
        lo.addWidget(self.btn_view_schema)

        lo.addStretch(1)

        # Server indicator
        self.server_dot = QLabel("●")
        self.server_dot.setFixedWidth(16)
        self.server_dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.server_dot.setStyleSheet("color:#ff5f57; font-size:9px;")
        lo.addWidget(self.server_dot)

        self.server_label = QLabel("Checking server...")
        self.server_label.setStyleSheet("font-size:11px; color:#4a5f74;")
        lo.addWidget(self.server_label)

        lo.addSpacing(12)

        self.btn_connect_db = QPushButton("+ Connect DB")
        self.btn_connect_db.setFixedHeight(32)
        self.btn_connect_db.setStyleSheet(
            "font-size:11px; font-weight:600; padding:0 16px;"
            "color:#3b9eff; background:#3b9eff14;"
            "border:1px solid #3b9eff40; border-radius:6px;"
        )
        lo.addWidget(self.btn_connect_db)

        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_disconnect.setFixedHeight(32)
        self.btn_disconnect.setObjectName("btn_danger")
        self.btn_disconnect.setVisible(False)
        lo.addWidget(self.btn_disconnect)

        return bar

    # ── Tabs ───────────────────────────────────────────────────────────────────
    def _build_tabs(self) -> QTabWidget:
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane{border:none; background:#0a0d12;}"
            "QTabWidget::tab-bar{left:0;}"
            "QTabBar{background:#0c1016; border-bottom:2px solid #1a2130;}"
            "QTabBar::tab{"
            "  background:#0c1016; color:#4a5f74;"
            "  font-family:Consolas,monospace; font-size:10px; font-weight:700;"
            "  letter-spacing:2px; padding:14px 32px;"
            "  border:none; border-bottom:2px solid transparent; margin:0;"
            "  min-width:180px;"
            "}"
            "QTabBar::tab:selected{color:#00e5b0; border-bottom:2px solid #00e5b0;}"
            "QTabBar::tab:hover:!selected{color:#8899aa;}"
        )
        self.tabs.addTab(self._build_sql_tab(),     "  SQL GENERATOR  ")
        self.tabs.addTab(self._build_explain_tab(), "  TABLE INSIGHTS  ")
        return self.tabs

    # ══════════════════════════════════════════════════════════════════
    # TAB 1: SQL GENERATOR
    # ══════════════════════════════════════════════════════════════════
    def _build_sql_tab(self) -> QWidget:
        w  = QWidget()
        w.setStyleSheet("background:#0a0d12;")
        lo = QVBoxLayout(w)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        vs = QSplitter(Qt.Orientation.Vertical)
        vs.setHandleWidth(1)
        vs.setChildrenCollapsible(False)
        vs.addWidget(self._build_question_panel())
        self.results_panel = ResultsPanel()
        vs.addWidget(self.results_panel)
        vs.setSizes([320, 600])
        lo.addWidget(vs, 1)
        return w

    def _build_question_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("query_panel")
        lo    = QVBoxLayout(panel)
        lo.setContentsMargins(22, 18, 22, 16)
        lo.setSpacing(12)

        # ── QUESTION ──────────────────────────────────────────────────────────
        qh = QHBoxLayout()
        ql = QLabel("QUESTION")
        ql.setObjectName("section_label")
        qh.addWidget(ql)
        qh.addStretch()
        self.schema_hint = QLabel("Select a table on the left, or type to auto-match")
        self.schema_hint.setStyleSheet("font-size:10px; color:#4a5f74;")
        qh.addWidget(self.schema_hint)
        lo.addLayout(qh)

        self.query_input = QTextEdit()
        self.query_input.setObjectName("query_input")
        self.query_input.setPlaceholderText(
            "Ask in plain English...\n"
            "e.g.  Show total revenue per month for the last year\n"
            "      Find the top 10 customers by order value\n"
            "      List all actors whose records were updated in the last 6 months"
        )
        self.query_input.setMinimumHeight(88)
        self.query_input.setMaximumHeight(108)
        lo.addWidget(self.query_input)

        # ── divider ───────────────────────────────────────────────────────────
        div = QFrame(); div.setFixedHeight(1)
        div.setStyleSheet("background:#1a2130; margin:0 -22px;")
        lo.addWidget(div)

        # ── GENERATED SQL ─────────────────────────────────────────────────────
        sh = QHBoxLayout()
        sh.setSpacing(8)
        sl = QLabel("GENERATED SQL")
        sl.setObjectName("section_label")
        sh.addWidget(sl)

        self.sql_valid_badge = QLabel("")
        self.sql_valid_badge.setFixedHeight(22)
        self.sql_valid_badge.setVisible(False)
        sh.addWidget(self.sql_valid_badge)

        sh.addStretch()
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setFixedSize(60, 24)
        self.copy_btn.setStyleSheet(
            "font-size:10px; color:#4a5f74; background:transparent;"
            "border:1px solid #1a2130; border-radius:4px;"
        )
        self.copy_btn.clicked.connect(self._copy_sql)
        self.copy_btn.setVisible(False)
        sh.addWidget(self.copy_btn)
        lo.addLayout(sh)

        self.sql_display = QTextEdit()
        self.sql_display.setObjectName("sql_display")
        self.sql_display.setPlaceholderText(
            "Generated SQL appears here  (editable before running)..."
        )
        self.sql_display.setMinimumHeight(70)
        self.sql_display.setMaximumHeight(92)
        self.sql_display.textChanged.connect(self._refresh_buttons)
        lo.addWidget(self.sql_display)

        # ── Action row ────────────────────────────────────────────────────────
        ar = QHBoxLayout()
        ar.setSpacing(10)

        self.ai_status = QLabel("")
        self.ai_status.setStyleSheet("font-size:11px; color:#4a5f74;")
        ar.addWidget(self.ai_status, 1)

        self.gen_progress = QProgressBar()
        self.gen_progress.setRange(0, 0)
        self.gen_progress.setVisible(False)
        self.gen_progress.setFixedSize(84, 3)
        ar.addWidget(self.gen_progress)

        self.btn_generate = QPushButton("Generate SQL")
        self.btn_generate.setObjectName("btn_primary")
        self.btn_generate.setEnabled(False)
        self.btn_generate.setFixedHeight(36)
        self.btn_generate.setToolTip("Ctrl+Enter")
        ar.addWidget(self.btn_generate)

        self.btn_run = QPushButton("Run Query")
        self.btn_run.setObjectName("btn_run")
        self.btn_run.setEnabled(False)
        self.btn_run.setFixedHeight(36)
        self.btn_run.setToolTip("F5")
        ar.addWidget(self.btn_run)

        lo.addLayout(ar)
        return panel

    # ══════════════════════════════════════════════════════════════════
    # TAB 2: TABLE INSIGHTS
    # ══════════════════════════════════════════════════════════════════
    def _build_explain_tab(self) -> QWidget:
        w  = QWidget()
        w.setStyleSheet("background:#0a0d12;")
        lo = QVBoxLayout(w)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        # ── Action bar ────────────────────────────────────────────────────────
        abar = QFrame()
        abar.setFixedHeight(62)
        abar.setStyleSheet("background:#0f1318; border-bottom:1px solid #1a2130;")
        al   = QHBoxLayout(abar)
        al.setContentsMargins(22, 0, 22, 0)
        al.setSpacing(14)

        # Left: title block
        tblock = QVBoxLayout()
        tblock.setSpacing(3)
        ititle = QLabel("TABLE  INSIGHTS")
        ititle.setStyleSheet(
            "font-family:Consolas,monospace; font-size:9px; font-weight:700;"
            "color:#3b9eff; letter-spacing:2px;"
        )
        self.ins_sub = QLabel("Select tables from the left panel, then click Explain")
        self.ins_sub.setStyleSheet("font-size:10px; color:#4a5f74;")
        tblock.addWidget(ititle)
        tblock.addWidget(self.ins_sub)
        al.addLayout(tblock, 1)

        # Selected-tables chip
        self.selected_chip = QLabel("  No tables selected  ")
        self.selected_chip.setFixedHeight(30)
        self.selected_chip.setStyleSheet(
            "background:#141920; border:1px solid #1a2130; border-radius:6px;"
            "padding:0 16px; font-size:11px; color:#4a5f74;"
            "font-family:Consolas,monospace;"
        )
        al.addWidget(self.selected_chip)

        self.btn_explain = QPushButton("Explain Selected")
        self.btn_explain.setObjectName("btn_primary")
        self.btn_explain.setFixedHeight(34)
        self.btn_explain.setEnabled(False)
        self.btn_explain.setToolTip("Ctrl+E")
        al.addWidget(self.btn_explain)

        self.btn_explain_all = QPushButton("Explain All Tables")
        self.btn_explain_all.setFixedHeight(34)
        self.btn_explain_all.setEnabled(False)
        self.btn_explain_all.setStyleSheet(
            "font-size:11px; font-weight:600; border-radius:6px; padding:0 18px;"
            "background:#3b9eff14; border:1px solid #3b9eff40; color:#3b9eff;"
        )
        al.addWidget(self.btn_explain_all)

        # Expand schema button
        self.btn_view_schema2 = QPushButton("View Full Schema")
        self.btn_view_schema2.setFixedHeight(34)
        self.btn_view_schema2.setEnabled(False)
        self.btn_view_schema2.setStyleSheet(
            "font-size:11px; font-weight:600; border-radius:6px; padding:0 16px;"
            "background:#141920; border:1px solid #1a2130; color:#8899aa;"
        )
        self.btn_view_schema2.clicked.connect(self._view_schema)
        al.addWidget(self.btn_view_schema2)

        lo.addWidget(abar)

        # ── Progress bar ──────────────────────────────────────────────────────
        self.exp_progress = QProgressBar()
        self.exp_progress.setRange(0, 0)
        self.exp_progress.setVisible(False)
        self.exp_progress.setFixedHeight(3)
        self.exp_progress.setStyleSheet(
            "QProgressBar{border:none;background:#0f1318;}"
            "QProgressBar::chunk{background:qlineargradient("
            "x1:0,y1:0,x2:1,y2:0,stop:0 #00e5b0,stop:1 #3b9eff);}"
        )
        lo.addWidget(self.exp_progress)

        # ── Horizontal split: explanation (left) | column detail (right) ─────
        hs = QSplitter(Qt.Orientation.Horizontal)
        hs.setHandleWidth(1)
        hs.setChildrenCollapsible(False)
        hs.addWidget(self._build_explanation_pane())
        hs.addWidget(self._build_column_detail_pane())
        hs.setSizes([860, 380])
        lo.addWidget(hs, 1)
        return w

    def _build_explanation_pane(self) -> QFrame:
        pane = QFrame()
        pane.setStyleSheet("background:#0a0d12;")
        lo   = QVBoxLayout(pane)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        lbar = QFrame()
        lbar.setFixedHeight(38)
        lbar.setStyleSheet("background:#0f1318; border-bottom:1px solid #1a2130;")
        ll   = QHBoxLayout(lbar)
        ll.setContentsMargins(22, 0, 22, 0)

        lbl = QLabel("AI  EXPLANATION")
        lbl.setStyleSheet(
            "font-family:Consolas,monospace; font-size:9px; font-weight:700;"
            "color:#4a5f74; letter-spacing:2px;"
        )
        ll.addWidget(lbl)
        ll.addStretch()

        self.exp_meta_lbl = QLabel("")
        self.exp_meta_lbl.setStyleSheet("font-size:10px; color:#4a5f74;")
        ll.addWidget(self.exp_meta_lbl)
        lo.addWidget(lbar)

        self.explanation_text = QTextEdit()
        self.explanation_text.setReadOnly(True)
        self.explanation_text.setStyleSheet(
            "background:#0a0d12; border:none; padding:22px 26px;"
            "font-family:'Segoe UI',Arial,sans-serif; font-size:13px;"
            "color:#aabbd0; line-height:1.8;"
        )
        self.explanation_text.setPlaceholderText(
            "Select tables from the left panel, then click Explain Selected\n"
            "or Explain All Tables above.\n\n"
            "The AI will describe each table: what it represents,\n"
            "what its columns mean, and how tables relate to each other."
        )
        lo.addWidget(self.explanation_text, 1)
        return pane

    def _build_column_detail_pane(self) -> QFrame:
        pane = QFrame()
        pane.setMinimumWidth(280)
        pane.setStyleSheet("background:#0f1318; border-left:1px solid #1a2130;")
        lo   = QVBoxLayout(pane)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        hdr = QFrame()
        hdr.setFixedHeight(38)
        hdr.setStyleSheet("background:#0f1318; border-bottom:1px solid #1a2130;")
        hl  = QHBoxLayout(hdr)
        hl.setContentsMargins(16, 0, 16, 0)
        lbl = QLabel("COLUMN  DETAIL")
        lbl.setStyleSheet(
            "font-family:Consolas,monospace; font-size:9px; font-weight:700;"
            "color:#4a5f74; letter-spacing:2px;"
        )
        hl.addWidget(lbl)
        lo.addWidget(hdr)

        self.schema_detail_text = QTextEdit()
        self.schema_detail_text.setReadOnly(True)
        self.schema_detail_text.setStyleSheet(
            "background:#0f1318; border:none; padding:14px 18px;"
            "font-family:Consolas,monospace; font-size:11px;"
            "color:#6b8099; line-height:1.8;"
        )
        self.schema_detail_text.setPlaceholderText(
            "Column names, types\nand constraints for\nselected tables."
        )
        lo.addWidget(self.schema_detail_text, 1)
        return pane

    # ── Status bar ─────────────────────────────────────────────────────────────
    def _build_statusbar(self):
        sb = self.statusBar()
        sb.setStyleSheet(
            "QStatusBar{background:#0a0d12; border-top:1px solid #1a2130;"
            "padding:0 18px; font-size:11px; min-height:28px;}"
            "QStatusBar::item{border:none;}"
        )
        self.status_lbl  = QLabel("Ready")
        self.status_lbl.setStyleSheet("color:#4a5f74;")
        self.status_conn = QLabel("")
        sb.addWidget(self.status_lbl, 1)
        sb.addPermanentWidget(self.status_conn)

    # ══════════════════════════════════════════════════════════════════
    # Signal wiring
    # ══════════════════════════════════════════════════════════════════
    def _wire_signals(self):
        self.btn_connect_db.clicked.connect(self._show_conn_dialog)
        self.btn_disconnect.clicked.connect(self._disconnect)
        self.schema_browser.btn_connect.clicked.connect(self._show_conn_dialog)
        self.schema_browser.schema_selected.connect(self._on_schema_selected)
        self.schema_browser.multi_schema_selected.connect(self._on_multi_selected)
        self.btn_generate.clicked.connect(self._generate_sql)
        self.btn_run.clicked.connect(self._run_query)
        self.btn_explain.clicked.connect(self._explain_selected)
        self.btn_explain_all.clicked.connect(self._explain_all)
        self.btn_view_schema.clicked.connect(self._view_schema)

        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._auto_select)
        self.query_input.textChanged.connect(lambda: self._auto_timer.start(350))

        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._generate_sql)
        QShortcut(QKeySequence("F5"),          self).activated.connect(self._run_query)
        QShortcut(QKeySequence("Ctrl+E"),      self).activated.connect(self._explain_selected)

    # ══════════════════════════════════════════════════════════════════
    # Health polling
    # ══════════════════════════════════════════════════════════════════
    def _poll_health(self):
        if self._ht and self._ht.isRunning():
            return
        self._hw = BgWorker(self._api.health)
        self._hw.finished.connect(self._on_health)
        self._ht = _run_bg(self, self._hw)
        self._hw.finished.connect(self._ht.quit)

    def _on_health(self, status):
        if isinstance(status, Exception) or not status.reachable:
            self._api_ready  = False
            self._api_device = ""
            self._set_server_dot("offline")
        elif status.reachable and status.model_loaded:
            self._api_ready  = True
            label = getattr(status, "model_device_label", "") or "Ready"
            self._set_server_dot("ready", label)
        else:
            self._api_ready = False
            self._set_server_dot("loading")
        self._refresh_buttons()

    def _set_server_dot(self, state: str, label: str = ""):
        cfg = {
            "ready":   ("#28cd7e", "#28cd7e", label),
            "loading": ("#f5a623", "#f5a623", "Model loading..."),
            "offline": ("#ff5f57", "#4a5f74", "Server offline"),
        }
        dot, txt, text = cfg.get(state, cfg["offline"])
        self.server_dot.setStyleSheet(f"color:{dot}; font-size:9px;")
        self.server_label.setText(text)
        self.server_label.setStyleSheet(f"font-size:11px; color:{txt};")

    # ══════════════════════════════════════════════════════════════════
    # DB connect / disconnect
    # ══════════════════════════════════════════════════════════════════
    def _show_conn_dialog(self):
        dlg = ConnectionDialog(self, self._config)
        dlg.connected.connect(self._on_connected)
        dlg.exec()

    def _on_connected(self, connector, config):
        self._connector = connector
        self._config    = config
        self.schema_browser.set_connector(connector, config.display_name)
        self.btn_disconnect.setVisible(True)
        self.btn_connect_db.setVisible(False)
        self.status_conn.setText(f"  {config.display_name}")
        self.status_conn.setStyleSheet("font-size:11px; color:#28cd7e;")
        self.btn_explain_all.setEnabled(True)
        self._set_status("Connected - select a table to begin", "ok")
        self._refresh_buttons()

    def _disconnect(self):
        if self._connector:
            self._connector.disconnect()
        self._connector = None
        self._config    = None
        self.schema_browser.set_disconnected()
        self.btn_disconnect.setVisible(False)
        self.btn_connect_db.setVisible(True)
        self.status_conn.setText("")
        self._selected_schemas = []
        self._update_schema_display()
        self.btn_explain_all.setEnabled(False)
        self._set_status("Disconnected", "")
        self._refresh_buttons()

    # ══════════════════════════════════════════════════════════════════
    # Schema selection
    # ══════════════════════════════════════════════════════════════════
    def _on_schema_selected(self, schema, table, schema_str):
        self._selected_schemas = [(schema, table, schema_str)]
        self._update_schema_display(manual=True)
        self._refresh_buttons()

    def _on_multi_selected(self, selections):
        self._selected_schemas = selections
        self._update_schema_display(manual=True)
        self._refresh_buttons()

    def _update_schema_display(self, manual: bool = False):
        sel = self._selected_schemas

        # ── Nothing selected ──────────────────────────────────────────────────
        if not sel:
            _tag_style_off = (
                "background:#141920; border:1px solid #1a2130; border-radius:6px;"
                "padding:0 16px; font-size:12px; color:#4a5f74; font-family:Consolas,monospace;"
            )
            self.schema_tag.setText("  No table selected  ")
            self.schema_tag.setStyleSheet(_tag_style_off)
            self.schema_hint.setText("Select a table on the left, or type to auto-match")
            self.schema_hint.setStyleSheet("font-size:10px; color:#4a5f74;")
            self.selected_chip.setText("  No tables selected  ")
            self.selected_chip.setStyleSheet(
                "background:#141920; border:1px solid #1a2130; border-radius:6px;"
                "padding:0 16px; font-size:11px; color:#4a5f74; font-family:Consolas,monospace;"
            )
            self.auto_badge.setVisible(False)
            self.btn_view_schema.setEnabled(False)
            self.btn_view_schema2.setEnabled(False)
            self.schema_detail_text.setPlainText("")
            return

        # ── Tables selected ───────────────────────────────────────────────────
        names = ", ".join(f"{s}.{t}" for s, t, _ in sel)
        _tag_style_on = (
            "background:#00e5b010; border:1px solid #00e5b040; border-radius:6px;"
            "padding:0 16px; font-size:12px; color:#00e5b0; font-family:Consolas,monospace;"
        )
        self.schema_tag.setText(f"  {names}  ")
        self.schema_tag.setStyleSheet(_tag_style_on)

        hint = "  |  ".join(ss for _, _, ss in sel)
        self.schema_hint.setText(hint)
        self.schema_hint.setStyleSheet("font-size:10px; color:#00e5b050;")

        self.selected_chip.setText(f"  {names}  ")
        self.selected_chip.setStyleSheet(
            "background:#00e5b010; border:1px solid #00e5b040; border-radius:6px;"
            "padding:0 16px; font-size:11px; color:#00e5b0; font-family:Consolas,monospace;"
        )
        self.auto_badge.setVisible(not manual)
        self.btn_view_schema.setEnabled(True)
        self.btn_view_schema2.setEnabled(True)
        self._refresh_column_detail()

    def _refresh_column_detail(self):
        all_info = self.schema_browser._table_info
        lines = []
        for schema, table, schema_str in self._selected_schemas:
            info = all_info.get((schema, table))
            lines.append(f"-- {schema}.{table}")
            if info and info.row_count:
                lines.append(f"-- {info.row_count:,} rows")
            lines.append("")
            if info and info.columns:
                w = max(len(c.name) for c in info.columns) + 2
                for col in info.columns:
                    flags = (
                        (" [PK]"       if col.is_primary else "") +
                        (" [FK]"       if col.is_foreign else "") +
                        (" NOT NULL"   if not col.nullable else "")
                    )
                    lines.append(f"  {col.name.ljust(w)} {col.data_type}{flags}")
            else:
                lines.append(f"  {schema_str}")
            lines.append("")
        self.schema_detail_text.setPlainText("\n".join(lines).strip())

    def _auto_select(self):
        question = self.query_input.toPlainText().strip()
        if len(question) < 6:
            return
        all_info = self.schema_browser._table_info
        if not all_info:
            return
        if self._selected_schemas and not self.auto_badge.isVisible():
            return
        selected = select_tables(question, all_info, max_tables=3)
        if selected:
            self._selected_schemas = selected
            self._update_schema_display(manual=False)
            self._refresh_buttons()

    def _view_schema(self):
        if not self._selected_schemas:
            return
        dlg = SchemaViewerDialog(
            self, self._selected_schemas, self.schema_browser._table_info
        )
        dlg.show()

    # ══════════════════════════════════════════════════════════════════
    # Generate SQL
    # ══════════════════════════════════════════════════════════════════
    def _generate_sql(self):
        question = self.query_input.toPlainText().strip()
        if not question:
            return self._set_status("Enter a question first", "warn")
        if not self._api_ready:
            return self._set_status(
                "Server not ready  -  run start_server.bat and wait for the model to load",
                "warn",
            )
        if not self._selected_schemas:
            self._auto_select()
            if not self._selected_schemas:
                return self._set_status(
                    "No table selected  -  pick one from the left panel", "warn"
                )

        payload, full_ctx = self._build_payload()
        self._set_busy(True, "Generating SQL...")
        self.btn_generate.setEnabled(False)
        self.btn_run.setEnabled(False)
        self.sql_valid_badge.setVisible(False)
        self.results_panel.clear()

        self._gw = BgWorker(
            lambda: self._api.generate_sql(question, payload, max_retries=4, full_context=full_ctx)
        )
        self._gw.finished.connect(self._on_generated)
        self._gt = _run_bg(self, self._gw)
        self._gw.finished.connect(self._gt.quit)

    def _on_generated(self, result):
        self._set_busy(False)
        if isinstance(result, Exception):
            return self._set_status(f"Error: {result}", "error")
        if not result.ok:
            return self._set_status(f"API error: {result.api_error}", "error")

        self.sql_display.setPlainText(result.sql)
        self.copy_btn.setVisible(True)

        # Validity badge
        self.sql_valid_badge.setVisible(True)
        if result.valid:
            self.sql_valid_badge.setText("  VALID  ")
            self.sql_valid_badge.setStyleSheet(
                "font-family:Consolas,monospace; font-size:9px; font-weight:700;"
                "color:#28cd7e; background:#28cd7e14; border:1px solid #28cd7e40;"
                "border-radius:3px; padding:0 10px; letter-spacing:1px;"
            )
        else:
            self.sql_valid_badge.setText("  NEEDS REVIEW  ")
            self.sql_valid_badge.setStyleSheet(
                "font-family:Consolas,monospace; font-size:9px; font-weight:700;"
                "color:#f5a623; background:#f5a62314; border:1px solid #f5a62340;"
                "border-radius:3px; padding:0 10px; letter-spacing:1px;"
            )

        class _R: pass
        r = _R()
        r.sql  = result.sql;  r.valid = result.valid
        r.attempts = result.attempts;  r.stage = result.stage
        r.error = result.error;  r.correction_log = result.correction_log
        self.results_panel.show_validation_result(r)

        self._set_status(
            f"SQL generated  |  {result.attempts} attempt(s)  |  {result.latency_ms:.0f} ms",
            "ok" if result.valid else "warn",
        )
        self._refresh_buttons()

    # ══════════════════════════════════════════════════════════════════
    # Run query
    # ══════════════════════════════════════════════════════════════════
    def _run_query(self):
        sql = self.sql_display.toPlainText().strip()
        if not sql:
            return self._set_status("No SQL to run", "warn")
        if not self._connector or not self._connector.is_connected:
            return self._set_status("Not connected to a database", "warn")

        self._set_busy(True, "Executing query...")
        self.btn_run.setEnabled(False)
        self._ew = ExecWorker(self._connector, sql)
        self._ew.finished.connect(self._on_executed)
        self._et = _run_bg(self, self._ew)
        self._ew.finished.connect(self._et.quit)

    def _on_executed(self, result):
        self._set_busy(False)
        self.results_panel.show_query_result(result)
        self._set_status(
            f"Query returned {result.row_count:,} row(s)  |  {result.execution_ms:.1f} ms"
            if result.success else f"Query error: {result.error[:120]}",
            "ok" if result.success else "error",
        )
        self._refresh_buttons()

    # ══════════════════════════════════════════════════════════════════
    # Explain tables
    # ══════════════════════════════════════════════════════════════════
    def _explain_selected(self):
        self._run_explain(self._selected_schemas)

    def _explain_all(self):
        all_info = self.schema_browser._table_info
        if not all_info:
            return self._set_status("Connect to a database first", "warn")
        tables = [(s, t, info.schema_string())
                  for (s, t), info in list(all_info.items())[:12]]
        self._run_explain(tables)

    def _run_explain(self, table_list):
        if not table_list:
            return self._set_status("No tables selected", "warn")
        if not self._api_ready:
            return self._set_status("Server not ready", "warn")

        self.tabs.setCurrentIndex(1)   # switch to Insights tab

        all_info = self.schema_browser._table_info
        db_name  = self._config.database if self._config else ""
        payload  = []
        for s, t, ss in table_list:
            info = all_info.get((s, t))
            cols = ([{"name": c.name, "type": c.data_type,
                      "is_primary": c.is_primary, "is_foreign": c.is_foreign}
                     for c in info.columns] if info else [])
            payload.append({"table": t, "schema_string": ss, "columns": cols})

        self.exp_progress.setVisible(True)
        self.btn_explain.setEnabled(False)
        self.explanation_text.setPlainText("")
        names = ", ".join(t for _, t, _ in table_list)
        self.ins_sub.setText(f"Explaining: {names}...")
        self.exp_meta_lbl.setText(f"Explaining {len(table_list)} table(s)...")

        self._xw = BgWorker(lambda: self._api.explain_tables(payload, db_name))
        self._xw.finished.connect(self._on_explained)
        self._xt = _run_bg(self, self._xw)
        self._xw.finished.connect(self._xt.quit)

    def _on_explained(self, result):
        self.exp_progress.setVisible(False)
        self.btn_explain.setEnabled(bool(self._selected_schemas) and self._api_ready)
        if isinstance(result, Exception) or not result.ok:
            msg = str(result) if isinstance(result, Exception) else result.api_error
            self.explanation_text.setPlainText(f"Error: {msg}")
            self.ins_sub.setText("Explanation failed")
            self.exp_meta_lbl.setText("")
            return
        self.explanation_text.setPlainText(result.explanation)
        names = ", ".join(result.tables_explained)
        self.ins_sub.setText(f"Explained: {names}")
        self.exp_meta_lbl.setText(
            f"{len(result.tables_explained)} table(s)  |  {result.latency_ms:.0f} ms"
        )
        self._set_status(f"Table explanation ready  |  {result.latency_ms:.0f} ms", "ok")

    # ══════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════
    def _build_payload(self) -> tuple[list, dict]:
        """Returns (selected_schemas_payload, full_context_dict)."""
        all_info = self.schema_browser._table_info
        selected_names = {(s, t) for s, t, _ in self._selected_schemas}

        # Primary payload: selected tables with full column detail
        payload = [
            {
                "table": table,
                "schema_string": schema_str,
                "columns": (
                    [{"name": c.name, "type": c.data_type,
                      "is_primary": c.is_primary, "is_foreign": c.is_foreign}
                     for c in all_info[(schema, table)].columns]
                    if (schema, table) in all_info else []
                ),
            }
            for schema, table, schema_str in self._selected_schemas
        ]

        # Full context: all OTHER tables as compact schema strings
        # Gives the model JOIN awareness across the whole database
        full_context: dict[str, str] = {}
        for (schema, table), info in all_info.items():
            if (schema, table) not in selected_names:
                ss = info.schema_string() if hasattr(info, "schema_string") else ""
                if ss:
                    full_context[f"{schema}.{table}"] = ss

        return payload, full_context

    def _copy_sql(self):
        sql = self.sql_display.toPlainText().strip()
        if sql:
            QApplication.clipboard().setText(sql)
            self.copy_btn.setText("Copied!")
            QTimer.singleShot(1600, lambda: self.copy_btn.setText("Copy"))

    def _set_busy(self, busy: bool, msg: str = ""):
        self.gen_progress.setVisible(busy)
        self.ai_status.setText(msg if busy else "")
        self.ai_status.setStyleSheet(
            f"font-size:11px; color:{'#3b9eff' if busy else '#4a5f74'};"
        )

    def _set_status(self, msg: str, level: str = ""):
        c = {"ok":"#28cd7e","warn":"#f5a623","error":"#ff5f57","info":"#3b9eff"}.get(level,"#4a5f74")
        self.status_lbl.setText(msg)
        self.status_lbl.setStyleSheet(f"font-size:11px; color:{c};")

    def _refresh_buttons(self):
        srv = self._api_ready
        sel = bool(self._selected_schemas)
        sql = bool(self.sql_display.toPlainText().strip())
        db  = bool(self._connector and self._connector.is_connected)
        tbl = bool(self.schema_browser._table_info)
        self.btn_generate.setEnabled(srv and sel)
        self.btn_run.setEnabled(db and sql)
        self.btn_explain.setEnabled(srv and sel)
        self.btn_explain_all.setEnabled(tbl)
        self.btn_view_schema.setEnabled(sel)
        self.btn_view_schema2.setEnabled(sel)

    def closeEvent(self, event):
        self._health_timer.stop()
        if self._connector:
            self._connector.disconnect()
        event.accept()
