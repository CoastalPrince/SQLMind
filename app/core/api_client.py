from __future__ import annotations
import json
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class APIGenerationResult:
    sql: str = ""
    valid: bool = False
    attempts: int = 0
    stage: str = ""
    error: str = ""
    schema_used: str = ""
    correction_log: list[dict] = field(default_factory=list)
    latency_ms: float = 0.0
    api_error: str = ""

    @property
    def ok(self) -> bool:
        return not self.api_error


@dataclass
class APIExplanationResult:
    explanation: str = ""
    tables_explained: list[str] = field(default_factory=list)
    db_name: str = ""
    latency_ms: float = 0.0
    api_error: str = ""

    @property
    def ok(self) -> bool:
        return not self.api_error


@dataclass
class HealthStatus:
    reachable: bool = False
    model_loaded: bool = False
    model_device: str = ""
    model_device_label: str = ""
    uptime_seconds: float = 0.0
    error: str = ""


class SQLMindClient:
    """Thin HTTP client for the SQLMind local API."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")

    def _post(self, path: str, payload: dict, timeout: int = 120) -> dict:
        url  = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path: str, timeout: int = 10) -> dict:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ── Health ─────────────────────────────────────────────────────────────────
    def health(self) -> HealthStatus:
        try:
            data = self._get("/health")
            return HealthStatus(
                reachable=True,
                model_loaded=data.get("model_loaded", False),
                model_device=data.get("model_device", ""),
                model_device_label=data.get("model_device_label", ""),
                uptime_seconds=data.get("uptime_seconds", 0),
            )
        except Exception as e:
            return HealthStatus(reachable=False, error=str(e))

    # ── Generate SQL ───────────────────────────────────────────────────────────
    def generate_sql(
        self,
        question: str,
        schemas: list[dict],
        max_retries: int = 3,
        full_context: dict | None = None,
    ) -> APIGenerationResult:
        """
        schemas: [{"table": str, "schema_string": str, "columns": [...]}]

        full_context: all other tables in the database as compact schema strings.
          {"public.film": "film(film_id, title, rental_rate)",
           "public.payment": "payment(payment_id, amount)", ...}
          The server appends these so the model sees the full DB for JOIN context.
        """
        payload = {
            "question":    question,
            "schemas":     schemas,
            "max_retries": max_retries,
        }
        if full_context:
            payload["full_context"] = full_context

        try:
            data = self._post("/generate-sql", payload)
            return APIGenerationResult(
                sql=data.get("sql", ""),
                valid=data.get("valid", False),
                attempts=data.get("attempts", 0),
                stage=data.get("stage", ""),
                error=data.get("error", ""),
                schema_used=data.get("schema_used", ""),
                correction_log=data.get("correction_log", []),
                latency_ms=data.get("latency_ms", 0),
            )
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            try:
                detail = json.loads(body).get("detail", body)
            except Exception:
                detail = body
            return APIGenerationResult(api_error=f"HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            return APIGenerationResult(
                api_error=f"Cannot reach API at {self.base_url} -- {e.reason}"
            )
        except Exception as e:
            return APIGenerationResult(api_error=str(e))

    # ── Explain Tables ─────────────────────────────────────────────────────────
    def explain_tables(
        self,
        tables: list[dict],
        db_name: str = "",
    ) -> APIExplanationResult:
        payload = {"tables": tables, "db_name": db_name}
        try:
            data = self._post("/explain-tables", payload, timeout=180)
            return APIExplanationResult(
                explanation=data.get("explanation", ""),
                tables_explained=data.get("tables_explained", []),
                db_name=data.get("db_name", ""),
                latency_ms=data.get("latency_ms", 0),
            )
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            try:
                detail = json.loads(body).get("detail", body)
            except Exception:
                detail = body
            return APIExplanationResult(api_error=f"HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            return APIExplanationResult(
                api_error=f"Cannot reach API at {self.base_url} -- {e.reason}"
            )
        except Exception as e:
            return APIExplanationResult(api_error=str(e))
