from __future__ import annotations
import re
from dataclasses import dataclass, field


# ── Synonym map ────────────────────────────────────────────────────────────────
# Maps question words → column/table name fragments they should match
_SYNONYMS: dict[str, list[str]] = {
    "revenue":     ["amount", "total", "price", "sales", "revenue", "income", "payment"],
    "sales":       ["amount", "order", "sales", "transaction", "purchase"],
    "customer":    ["customer", "client", "user", "buyer", "member", "account"],
    "product":     ["product", "item", "sku", "goods", "inventory", "catalog"],
    "order":       ["order", "purchase", "transaction", "booking", "cart"],
    "employee":    ["employee", "staff", "worker", "user", "person", "hr"],
    "salary":      ["salary", "wage", "pay", "compensation", "earning"],
    "inventory":   ["stock", "inventory", "quantity", "qty", "warehouse"],
    "price":       ["price", "cost", "amount", "rate", "fee", "value"],
    "date":        ["date", "time", "timestamp", "created_at", "updated_at", "period"],
    "department":  ["department", "dept", "team", "division", "group"],
    "category":    ["category", "type", "class", "segment", "genre", "kind"],
    "address":     ["address", "location", "city", "state", "country", "region", "zip"],
    "email":       ["email", "mail", "contact", "communication"],
    "phone":       ["phone", "mobile", "contact", "tel"],
    "status":      ["status", "state", "flag", "active", "enabled"],
    "discount":    ["discount", "promo", "coupon", "offer", "rebate"],
    "shipping":    ["shipping", "delivery", "logistics", "freight", "carrier"],
    "invoice":     ["invoice", "bill", "receipt", "payment"],
    "supplier":    ["supplier", "vendor", "manufacturer", "provider"],
    "rating":      ["rating", "score", "review", "feedback", "star"],
    "log":         ["log", "audit", "history", "event", "activity"],
    "permission":  ["permission", "role", "access", "privilege", "right"],
}

# Stop words to skip when tokenizing questions
_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need",
    "i", "you", "we", "they", "he", "she", "it", "this", "that", "these",
    "in", "on", "at", "of", "for", "to", "from", "by", "with", "and", "or",
    "but", "not", "all", "any", "some", "each", "every", "no",
    "how", "many", "much", "what", "which", "who", "where", "when",
    "show", "get", "find", "list", "give", "tell", "display", "return",
    "me", "us", "my", "our", "their", "its",
    "per", "total", "average", "count", "number", "between", "than",
}


# ── Public API ─────────────────────────────────────────────────────────────────

@dataclass
class TableScore:
    schema: str
    table: str
    schema_string: str
    score: float
    reasons: list[str] = field(default_factory=list)


def select_tables(
    question: str,
    all_table_info: dict,      # key: (schema, table) → TableInfo
    max_tables: int = 4,
    min_score: float = 1.0,
) -> list[tuple[str, str, str]]:
    """
    Given a question and full DB schema, return the most relevant
    (schema, table, schema_string) tuples ordered by relevance.

    all_table_info: dict mapping (schema_name, table_name) → TableInfo
                    (same dict as SchemaBrowser._table_info)
    """
    tokens = _tokenize(question)
    expanded = _expand_synonyms(tokens)

    scores: list[TableScore] = []

    for (schema, table), info in all_table_info.items():
        ts = _score_table(table, info, tokens, expanded)
        ts.schema = schema
        ts.schema_string = info.schema_string() if hasattr(info, "schema_string") else f"{table}(...)"
        scores.append(ts)

    # Sort descending by score
    scores.sort(key=lambda x: x.score, reverse=True)

    # Expand via FK relationships
    selected = [s for s in scores if s.score >= min_score][:max_tables]

    if not selected and scores:
        # Nothing scored → return the best 1 as a fallback
        selected = [scores[0]]

    return [(s.schema, s.table, s.schema_string) for s in selected]


def score_tables_for_display(
    question: str,
    all_table_info: dict,
) -> list[TableScore]:
    """
    Same as select_tables but returns full TableScore objects with reasons,
    useful for debugging / showing the user why a table was selected.
    """
    tokens = _tokenize(question)
    expanded = _expand_synonyms(tokens)
    scores = []
    for (schema, table), info in all_table_info.items():
        ts = _score_table(table, info, tokens, expanded)
        ts.schema = schema
        ts.schema_string = info.schema_string() if hasattr(info, "schema_string") else table
        scores.append(ts)
    scores.sort(key=lambda x: x.score, reverse=True)
    return scores


# ── Internal helpers ───────────────────────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    """Lowercase alphanum tokens, remove stop words and very short tokens."""
    tokens = re.findall(r"[a-z][a-z0-9_]*", text.lower())
    return {t for t in tokens if t not in _STOP and len(t) > 2}


def _expand_synonyms(tokens: set[str]) -> set[str]:
    """Add synonym targets for every question token that appears as a key."""
    expanded = set(tokens)
    for token in tokens:
        for syn_key, targets in _SYNONYMS.items():
            if token == syn_key or token in targets:
                expanded.update(targets)
                expanded.add(syn_key)
    return expanded


def _score_table(
    table: str,
    info,           # TableInfo or any object with .columns list
    tokens: set[str],
    expanded: set[str],
) -> TableScore:
    score = 0.0
    reasons = []
    table_lower = table.lower()

    # ── 1. Table name match ──────────────────────
    # Direct
    if table_lower in tokens:
        score += 5.0
        reasons.append(f"table name '{table}' in question")
    # Partial (table name is a substring of a token or vice versa)
    elif any(table_lower in t or t in table_lower for t in tokens if len(t) > 3):
        score += 3.0
        reasons.append(f"table name '{table}' partially matches question")
    # Synonym match on table name
    elif table_lower in expanded:
        score += 2.0
        reasons.append(f"table name '{table}' matches synonym expansion")

    # ── 2. Column name matches ────────────────────
    columns = getattr(info, "columns", [])
    for col in columns:
        col_lower = col.name.lower() if hasattr(col, "name") else str(col).lower()
        if col_lower in tokens:
            score += 3.0
            reasons.append(f"column '{col_lower}' in question")
        elif col_lower in expanded:
            score += 1.5
            reasons.append(f"column '{col_lower}' via synonym")
        # Partial token match on column name
        elif any(col_lower in t or (len(t) > 3 and t in col_lower) for t in tokens):
            score += 1.0
            reasons.append(f"column '{col_lower}' partial match")

    # ── 3. Singulars / plurals ────────────────────
    # e.g. question says "orders", table is "order"
    for token in tokens:
        if token.endswith("s") and token[:-1] == table_lower:
            score += 2.0
            reasons.append(f"plural match '{token}' → '{table}'")
        elif table_lower.endswith("s") and table_lower[:-1] == token:
            score += 2.0
            reasons.append(f"singular match '{token}' → '{table}'")

    return TableScore(schema="", table=table, schema_string="", score=score, reasons=reasons)
