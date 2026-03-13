"""
ui/schema_browser.py  — SQLMind v3
Left sidebar: connection, schema tree, table search.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTreeWidget, QTreeWidgetItem,
    QFrame, QLineEdit, QProgressBar
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, QObject
from PyQt6.QtGui import QFont, QColor

from core.db_connector import DBConnector, TableInfo


class SchemaLoaderWorker(QObject):
    schema_loaded = pyqtSignal(str, list)
    table_loaded  = pyqtSignal(str, str, object)
    finished      = pyqtSignal()

    def __init__(self, connector: DBConnector, schemas: list):
        super().__init__()
        self.connector = connector
        self.schemas   = schemas

    def run(self):
        for schema in self.schemas:
            tables = self.connector.list_tables(schema)
            self.schema_loaded.emit(schema, tables)
            for table in tables:
                info = self.connector.get_table_info(schema, table)
                self.table_loaded.emit(schema, table, info)
        self.finished.emit()


class SchemaBrowser(QWidget):
    schema_selected       = pyqtSignal(str, str, str)
    multi_schema_selected = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self._connector: DBConnector | None = None
        self._table_info: dict = {}
        self._selected_tables: set = set()
        self._thread  = None
        self._worker  = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Logo header ────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("sidebar_header")
        header.setFixedHeight(64)
        hl = QVBoxLayout(header)
        hl.setContentsMargins(18, 12, 18, 12)
        hl.setSpacing(3)

        title = QLabel("SQLMind")
        title.setObjectName("app_title")

        subtitle = QLabel("AI  ·  QUERY  ·  ENGINE")
        subtitle.setObjectName("app_subtitle")

        hl.addWidget(title)
        hl.addWidget(subtitle)
        root.addWidget(header)

        # ── Connection strip ──────────────────────────────────────
        conn = QFrame()
        conn.setFixedHeight(40)
        conn.setStyleSheet(
            "background:#07090e; border-bottom:1px solid #1a2535;"
        )
        cl = QHBoxLayout(conn)
        cl.setContentsMargins(14, 0, 10, 0)
        cl.setSpacing(8)

        self.conn_dot = QLabel("●")
        self.conn_dot.setStyleSheet(
            "color:#2d3f54; font-size:8px; font-family:Consolas;"
        )
        self.conn_dot.setFixedWidth(10)

        self.conn_label = QLabel("not connected")
        self.conn_label.setStyleSheet(
            "font-size:10px; color:#2d3f54; font-family:Consolas;"
        )

        self.btn_connect = QPushButton("Connect")
        self.btn_connect.setFixedSize(68, 24)
        self.btn_connect.setStyleSheet(
            "background:#00e5b010; border:1px solid #00e5b030; color:#00e5b0;"
            "font-size:9px; border-radius:3px; font-family:Consolas;"
            "font-weight:700; letter-spacing:0.5px;"
        )
        cl.addWidget(self.conn_dot)
        cl.addWidget(self.conn_label, 1)
        cl.addWidget(self.btn_connect)
        root.addWidget(conn)

        # ── Search box ────────────────────────────────────────────
        sf = QFrame()
        sf.setFixedHeight(38)
        sf.setStyleSheet(
            "background:#090d12; border-bottom:1px solid #1a2535;"
        )
        sl = QHBoxLayout(sf)
        sl.setContentsMargins(10, 6, 10, 6)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("filter tables…")
        self.search_box.setStyleSheet(
            "background:#0f1520; border:1px solid #1a2535; border-radius:3px;"
            "padding:4px 8px; font-size:10px; color:#9ab0c8; font-family:Consolas;"
        )
        self.search_box.textChanged.connect(self._filter_tree)
        sl.addWidget(self.search_box)
        root.addWidget(sf)

        # ── Tree ──────────────────────────────────────────────────
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setIndentation(14)
        self.tree.itemClicked.connect(self._on_item_clicked)
        root.addWidget(self.tree, 1)

        # ── Loading progress ──────────────────────────────────────
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setFixedHeight(2)
        self.progress.setStyleSheet(
            "QProgressBar{border:none;background:#090d12;}"
            "QProgressBar::chunk{background:qlineargradient("
            "x1:0,y1:0,x2:1,y2:0,stop:0 #00e5b0,stop:1 #00d4ff);}"
        )
        root.addWidget(self.progress)

        # ── Bottom hint ───────────────────────────────────────────
        hint = QLabel("ctrl+click  ·  multi-select")
        hint.setFixedHeight(22)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(
            "font-size:9px; color:#1a2535; background:#07090e;"
            "border-top:1px solid #1a2535; font-family:Consolas; letter-spacing:0.5px;"
        )
        root.addWidget(hint)

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_connector(self, connector: DBConnector, display_name: str):
        self._connector = connector
        self.conn_dot.setStyleSheet("color:#00e5b0; font-size:8px;")
        short = display_name if len(display_name) <= 24 else display_name[:22] + "…"
        self.conn_label.setText(short)
        self.conn_label.setStyleSheet("font-size:10px; color:#00e5b0; font-family:Consolas;")
        self.btn_connect.setText("Refresh")
        self._load_schemas()

    def set_disconnected(self):
        self._connector = None
        self.conn_dot.setStyleSheet("color:#2d3f54; font-size:8px;")
        self.conn_label.setText("not connected")
        self.conn_label.setStyleSheet("font-size:10px; color:#2d3f54; font-family:Consolas;")
        self.btn_connect.setText("Connect")
        self.tree.clear()
        self._table_info.clear()
        self._selected_tables.clear()

    def get_selected_schema_strings(self) -> list:
        results = []
        for (schema, table) in self._selected_tables:
            info = self._table_info.get((schema, table))
            if info:
                results.append((schema, table, info.schema_string()))
        return results

    # ── Internal ───────────────────────────────────────────────────────────────

    def _load_schemas(self):
        if not self._connector:
            return
        self.tree.clear()
        self._table_info.clear()
        self._selected_tables.clear()
        self.progress.setVisible(True)

        schemas = self._connector.list_schemas()
        if not schemas:
            schemas = [self._connector._config.database]

        self._thread = QThread(self)
        self._worker = SchemaLoaderWorker(self._connector, schemas)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.schema_loaded.connect(self._on_schema_loaded)
        self._worker.table_loaded.connect(self._on_table_loaded)
        self._worker.finished.connect(self._on_load_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    def _on_schema_loaded(self, schema_name: str, tables: list):
        item = QTreeWidgetItem(self.tree)
        item.setText(0, f"  {schema_name}")
        item.setData(0, Qt.ItemDataRole.UserRole, ("schema", schema_name, ""))
        item.setForeground(0, QColor("#00d4ff"))
        item.setFont(0, QFont("Consolas", 10, QFont.Weight.Bold))
        item.setExpanded(True)
        for table in tables:
            ti = QTreeWidgetItem(item)
            ti.setText(0, f"  {table}")
            ti.setData(0, Qt.ItemDataRole.UserRole, ("table", schema_name, table))
            ti.setForeground(0, QColor("#6b8099"))
            ti.setFont(0, QFont("Consolas", 10))

    def _on_table_loaded(self, schema: str, table: str, info: TableInfo):
        self._table_info[(schema, table)] = info
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            si = root.child(i)
            d  = si.data(0, Qt.ItemDataRole.UserRole)
            if not d or d[1] != schema:
                continue
            for j in range(si.childCount()):
                ti = si.child(j)
                td = ti.data(0, Qt.ItemDataRole.UserRole)
                if not td or td[2] != table:
                    continue
                cols = ", ".join(c.name for c in info.columns)
                rc   = f"  ({info.row_count:,} rows)" if info.row_count else ""
                ti.setToolTip(0, f"{table}({cols}){rc}")
                for col in info.columns:
                    flag = " [PK]" if col.is_primary else (" [FK]" if col.is_foreign else "")
                    ci = QTreeWidgetItem(ti)
                    ci.setText(0, f"    {col.name}  {col.data_type}{flag}")
                    ci.setForeground(0, QColor("#2d3f54"))
                    ci.setFont(0, QFont("Consolas", 9))
                    ci.setData(0, Qt.ItemDataRole.UserRole, ("column", schema, table))
                break

    def _on_load_finished(self):
        self.progress.setVisible(False)

    def _on_item_clicked(self, item: QTreeWidgetItem, _col: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] != "table":
            return
        _, schema, table = data
        info = self._table_info.get((schema, table))
        if not info:
            return

        from PyQt6.QtWidgets import QApplication
        ctrl = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)

        key = (schema, table)
        if ctrl:
            if key in self._selected_tables:
                self._selected_tables.discard(key)
                item.setForeground(0, QColor("#6b8099"))
                item.setFont(0, QFont("Consolas", 10))
            else:
                self._selected_tables.add(key)
                item.setForeground(0, QColor("#00e5b0"))
                item.setFont(0, QFont("Consolas", 10, QFont.Weight.Bold))
            self.multi_schema_selected.emit(self.get_selected_schema_strings())
        else:
            for (s, t) in list(self._selected_tables):
                self._deselect_item(s, t)
            self._selected_tables.clear()
            self._selected_tables.add(key)
            item.setForeground(0, QColor("#00e5b0"))
            item.setFont(0, QFont("Consolas", 10, QFont.Weight.Bold))
            self.schema_selected.emit(schema, table, info.schema_string())

    def _deselect_item(self, schema: str, table: str):
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            si = root.child(i)
            d  = si.data(0, Qt.ItemDataRole.UserRole)
            if not d or d[1] != schema:
                continue
            for j in range(si.childCount()):
                ti = si.child(j)
                td = ti.data(0, Qt.ItemDataRole.UserRole)
                if td and td[2] == table:
                    ti.setForeground(0, QColor("#6b8099"))
                    ti.setFont(0, QFont("Consolas", 10))
                    return

    def _filter_tree(self, text: str):
        text = text.lower().strip()
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            si = root.child(i)
            any_visible = False
            for j in range(si.childCount()):
                ti = si.child(j)
                visible = (not text) or (text in ti.text(0).strip().lower())
                ti.setHidden(not visible)
                if visible:
                    any_visible = True
            si.setHidden(bool(text) and not any_visible)
