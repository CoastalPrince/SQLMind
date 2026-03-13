from __future__ import annotations
import re

try:
    import sqlglot
    import sqlglot.expressions as exp
    HAS_SQLGLOT = True
except ImportError:
    HAS_SQLGLOT = False


# ── Schema parsing ─────────────────────────────────────────────────────────────

def parse_schema(schema: str) -> dict[str, set[str]]:
    """
    Parse one or more table definitions.
    'actor(actor_id, first_name); city(city_id, city, country_id)'
    Returns {table_name: {col1, col2, ...}}
    """
    tables: dict[str, set[str]] = {}
    for m in re.finditer(r'(\w+)\s*\(([^)]+)\)', schema):
        tname = m.group(1).lower()
        cols  = {c.strip().lower().split()[0] for c in m.group(2).split(',')}
        cols.discard('')
        tables[tname] = cols
    return tables


def _all_columns(tables: dict[str, set[str]]) -> set[str]:
    result: set[str] = set()
    for cols in tables.values():
        result |= cols
    return result


# ── Free-text / Natural language detector ─────────────────────────────────────

_NL_INTERIOR_PATTERNS = [
    re.compile(r'(?i)\bplease\s+(include|note|use|see|add)\b'),
    re.compile(r'(?i)\b(as you can see|this query|the result|returns all|which means)\b'),
    re.compile(r'(?i)\b(in order to|you can use|will give you|note that)\b'),
    re.compile(r'(?i)\buse the following schema\b'),
    re.compile(r'(?i)\bthe above\s+(query|sql|statement)\b'),
]


def is_natural_language(text: str) -> bool:
    """
    Returns True if the output is natural language, not SQL.
    Primary guard against hallucinated free-text responses.
    """
    stripped = text.strip()

    # 1. Must start with a SQL keyword
    if not re.match(
        r'^(SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|DROP|EXPLAIN|SHOW)',
        stripped, re.IGNORECASE
    ):
        return True

    # 2. Multi-sentence prose (sentence-ending punctuation then new capital)
    if re.search(r'[A-Z][^.!?]{20,}[.!?]\s+[A-Z]', stripped):
        return True

    # 3. Numbered list items — hallucinated schema dumps
    nl_lines = sum(
        1 for line in stripped.split('\n')
        if re.match(r'^\d+\.\s+[A-Za-z]', line.strip())
    )
    if nl_lines > 1:
        return True

    # 4. Known NL phrases that cannot appear in valid SQL
    for pat in _NL_INTERIOR_PATTERNS:
        if pat.search(stripped):
            return True

    # 5. SELECT with no FROM and long text is suspicious
    if re.match(r'^SELECT\b', stripped, re.IGNORECASE):
        has_from = bool(re.search(r'\bFROM\s+\w+', stripped, re.IGNORECASE))
        has_agg_only = bool(re.match(
            r'^SELECT\s+(COUNT|SUM|AVG|MAX|MIN|NOW|CURRENT)',
            stripped, re.IGNORECASE
        ))
        if not has_from and not has_agg_only and len(stripped) > 40:
            return True

    return False


# ── Syntax ────────────────────────────────────────────────────────────────────

_ALWAYS_VALID: set[str] = {
    "*", "count", "sum", "avg", "max", "min", "coalesce", "nullif",
    "date", "now", "current_date", "current_timestamp", "interval",
    "year", "month", "day", "substring", "length", "upper", "lower",
    "trim", "cast", "extract", "date_trunc", "date_format", "isnull",
    "letter", "total", "cnt", "n", "c", "id",
    "rank", "row_number", "lag", "lead", "dense_rank", "over", "partition",
}

_GARBAGE_PATTERNS: list[str] = [
    r'^\s*actor_\d',
    r'^\s*\[SQL\]',
    r'^\s*\[EXPLAIN\]',
    r'SELECT\s+actor_\d',
    r'=\s*actor_\d',
    r'City\s+Code\s+Country',
    r'District\s+Code',
    r'(?i)^show\s+\w+[-_]\w+\s+links',
    r'(?i)^show\s+\w+\s+(and|with)\s+\w+\s+(id|links|likes)',
]


def validate_syntax(sql: str) -> tuple[bool, str]:
    """Returns (is_valid, error_message)."""
    sql = sql.strip()
    if not sql:
        return False, "Empty SQL output."

    # FREE-TEXT GUARD
    if is_natural_language(sql):
        return False, (
            "Output is natural language, not SQL. "
            "Output ONLY a valid SQL statement starting with SELECT "
            "(or WITH / INSERT / UPDATE / DELETE). "
            "Do not explain or add any text."
        )

    if not re.match(
        r'^(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|DROP|EXPLAIN|SHOW)',
        sql, re.IGNORECASE
    ):
        return False, f"Output is not valid SQL: '{sql[:80]}'"

    for pat in _GARBAGE_PATTERNS:
        if re.search(pat, sql, re.IGNORECASE):
            return False, (
                f"Output contains garbage tokens: '{sql[:80]}'. "
                "Output ONLY a valid SQL query."
            )

    if not HAS_SQLGLOT:
        return True, ""

    try:
        tree = sqlglot.parse_one(sql, error_level=sqlglot.ErrorLevel.RAISE)
        if tree is None:
            return False, "SQL parsed to None -- likely malformed."
        return True, ""
    except sqlglot.errors.ParseError as e:
        msg = str(e)[:200]
        if any(k in msg.lower() for k in ("unexpected", "invalid identifier")):
            return False, f"Syntax error: {msg}"
        return True, ""
    except Exception:
        return True, ""


# ── Schema ────────────────────────────────────────────────────────────────────

def _build_join_hints(tables: dict[str, set[str]]) -> str:
    """
    Auto-detect FK relationships from _id column naming convention.
    Returns a short hint for correction prompts, e.g.:
      "city JOIN country ON city.country_id = country.country_id"
    """
    hints: list[str] = []
    for tbl, cols in tables.items():
        for col in cols:
            if col.endswith('_id') and col != f"{tbl}_id":
                ref = col[:-3]
                if ref in tables:
                    hints.append(
                        f"{tbl} JOIN {ref} ON {tbl}.{col} = {ref}.{ref}_id"
                    )
    return "; ".join(hints[:3])


def validate_schema(sql: str, schema: str) -> tuple[bool, str]:
    """
    Multi-table aware schema validation with CTE support and JOIN hints.
    """
    if not HAS_SQLGLOT:
        return True, ""

    tables = parse_schema(schema)
    if not tables:
        return True, ""

    all_cols   = _all_columns(tables)
    all_tables = set(tables.keys())
    multi      = len(tables) > 1

    try:
        tree = sqlglot.parse_one(sql)
    except Exception:
        return True, ""

    # CTE aliases are valid virtual table names — don't flag them
    cte_aliases: set[str] = {
        cte.alias.lower() for cte in tree.find_all(exp.CTE)
    }

    violations: list[str] = []

    for tbl_node in tree.find_all(exp.Table):
        used = tbl_node.name.lower()
        if not used:
            continue
        if used in all_tables or used in _ALWAYS_VALID or used in cte_aliases:
            continue
        if len(used) > 2 and used.isidentifier():
            hint = ""
            if multi:
                fk_hint = _build_join_hints(tables)
                hint = f" Hint -- use JOINs: {fk_hint}" if fk_hint else ""
            violations.append(
                f"Table '{used}' not in schema. "
                f"Known tables: {', '.join(sorted(all_tables))}.{hint}"
            )

    if not multi:
        for col_node in tree.find_all(exp.Column):
            used = col_node.name.lower()
            if not used or used == "*" or len(used) <= 2:
                continue
            if used in all_cols or used in _ALWAYS_VALID:
                continue
            violations.append(
                f"Column '{used}' not in schema. "
                f"Valid columns: {', '.join(sorted(all_cols))}."
            )

    table_violations = [v for v in violations if "Table '" in v]
    if table_violations:
        return False, " | ".join(table_violations)
    return True, ""


# ── Semantic ──────────────────────────────────────────────────────────────────

def _question_signals(question: str) -> dict:
    q = question.lower()
    return {
        "wants_order": any(w in q for w in [
            "top", "bottom", "highest", "lowest", "most", "least",
            "sort", "order", "rank", "best", "worst", "first", "last",
            "recent", "latest", "earliest", "oldest", "newest",
            "maximum", "minimum", "max", "min",
        ]),
        "wants_group": any(w in q for w in [
            "per", "each", "by", "group", "grouped", "every",
            "breakdown", "split", "average", "avg",
            "sum", "total", "distribution", "count", "how many",
        ]),
        "wants_limit": (
            any(w in q for w in [
                "top", "bottom", "first", "last", "only", "limit",
                "most", "least", "best", "worst",
            ])
            or bool(re.search(r'\b\d+\b', q))
        ),
        "wants_date": any(w in q for w in [
            "date", "day", "week", "month", "year", "today", "yesterday",
            "recent", "latest", "between", "since", "before", "after",
            "this year", "last year", "period", "range", "updated",
            "created", "modified", "last_update", "hire_date",
            "payment_date", "rental_date",
        ]),
        "wants_where": any(w in q for w in [
            "where", "filter", "only", "with", "above", "below",
            "greater", "less", "more than", "fewer", "equal",
            "specific", "that", "who", "which", "whose",
            "in", "not in", "contain", "like", "between",
            "start", "starts", "begin", "begins", "end", "ends",
            "active", "inactive", "status", "named", "called",
        ]),
        "wants_join": any(w in q for w in [
            "join", "related", "linked", "combined",
            "across", "match", "merge", "from both", "with their",
            "and their", "along with",
        ]),
    }


def validate_semantics(sql: str, question: str) -> tuple[bool, str]:
    """
    Light semantic check — only catches clearly wrong outputs:
      1. DML (UPDATE/DELETE/INSERT) when question asks for SELECT data
      2. LIMIT N > 1 with zero row-count / superlative intent

    Does NOT flag: ORDER BY, GROUP BY, WHERE, JOIN, HAVING, CTEs,
                   window functions, subqueries, date comparisons.
    """
    if not HAS_SQLGLOT:
        return True, ""

    try:
        tree = sqlglot.parse_one(sql)
    except Exception:
        return True, ""

    sig = _question_signals(question)
    q   = question.lower()
    violations: list[str] = []

    is_dml = isinstance(tree, (exp.Update, exp.Delete, exp.Insert))
    is_select_question = not any(w in q for w in [
        "update", "insert", "delete", "remove", "add", "set ",
        "change", "modify", "create", "drop",
    ])
    if is_dml and is_select_question:
        violations.append(
            f"Question asks for SELECT data but SQL is a {type(tree).__name__}. "
            "Generate a SELECT statement instead."
        )

    if not is_dml:
        limit_node = tree.find(exp.Limit)
        if limit_node and not sig["wants_limit"] and not sig["wants_order"]:
            limit_val = limit_node.sql()
            if re.search(r'LIMIT\s+([2-9]|\d{2,})', limit_val, re.IGNORECASE):
                violations.append(
                    "Remove LIMIT -- question does not specify a number of rows."
                )

    if violations:
        return False, " | ".join(violations)
    return True, ""


# ── Public API ────────────────────────────────────────────────────────────────

def validate_sql(sql: str, schema: str, question: str) -> tuple[bool, str, str]:
    """
    Full 3-stage pipeline: syntax (+ free-text guard) -> schema -> semantics.
    Returns (is_valid: bool, stage: str, error_message: str).

    Stage labels match correction hint prefixes in train_v3.jsonl:
      "SYNTAX ERROR: ..."  "SCHEMA ERROR: ..."  "SEMANTIC ERROR: ..."
    """
    ok, err = validate_syntax(sql)
    if not ok:
        return False, "SYNTAX", err

    ok, err = validate_schema(sql, schema)
    if not ok:
        return False, "SCHEMA", err

    ok, err = validate_semantics(sql, question)
    if not ok:
        return False, "SEMANTIC", err

    return True, "PASS", ""
