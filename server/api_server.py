"""
Endpoints:
    GET  /health          server + model status
    GET  /device          GPU / device info
    POST /generate-sql    NL question → validated SQL (with auto-correction)
    POST /explain-tables  AI plain-English table explanation
    GET  /docs            Swagger UI
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import logging
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ai_engine import AIEngine, GenerationResult, ExplanationResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sqlmind.api")

_engine = AIEngine()
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sqlmind-inf")
_startup_time = 0.0

_adapter_path: str = os.environ.get("SQLMIND_ADAPTER", "").strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _startup_time
    _startup_time = time.time()

    device, device_label = AIEngine.detect_device()
    log.info(f"SQLMind API v{app.version} starting…")
    log.info(f"GPU/Device   : {device_label}")
    log.info(f"Adapter path : '{_adapter_path or '(not set)'}'")

    if _adapter_path:
        if not os.path.isdir(_adapter_path):
            log.error(f"Adapter path does not exist: {_adapter_path}")
        else:
            log.info("Loading model — this may take a minute…")
            loop = asyncio.get_running_loop()

            def _load():
                return _engine.load_model(
                    _adapter_path,
                    progress_callback=lambda m: log.info(f"  {m}"),
                )

            ok, err = await loop.run_in_executor(_executor, _load)
            if ok:
                log.info(f"✓ Model ready [{_engine._device_name}]")
            else:
                log.error(f"✗ Model load FAILED: {err}")
    else:
        log.warning(
            "SQLMIND_ADAPTER env var not set — model not loaded.\n"
            "  Windows: set SQLMIND_ADAPTER=C:\\path\\to\\adapter\n"
            "  Linux:   export SQLMIND_ADAPTER=/path/to/adapter"
        )

    yield

    log.info("Shutting down…")
    _executor.shutdown(wait=False)


app = FastAPI(
    title="SQLMind API",
    description=(
        "AI SQL generation and table explanation via fine-tuned TinyLlama.\n\n"
        "- `POST /generate-sql` — natural language → validated SQL (auto-corrects up to 4×)\n"
        "- `POST /explain-tables` — plain-English explanation of DB tables\n"
        "- `GET  /device` — GPU / device info"
    ),
    version="3.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Pydantic models ────────────────────────────────────────────────────────────

class ColumnDef(BaseModel):
    name: str
    type: str = "text"
    is_primary: bool = False
    is_foreign: bool = False


class TableDef(BaseModel):
    table: str
    schema_string: str
    columns: list[ColumnDef] = Field(default_factory=list)


class GenerateSQLRequest(BaseModel):
    question: str
    schemas: list[TableDef] = Field(..., min_length=1)
    full_context: dict = Field(default_factory=dict)
    max_retries: int = Field(4, ge=0, le=6)   # default 4, up from 3


class CorrectionEntry(BaseModel):
    attempt: int
    sql: str
    valid: bool
    stage: str
    error: str


class GenerateSQLResponse(BaseModel):
    sql: str
    valid: bool
    attempts: int
    stage: str
    error: str
    schema_used: str
    correction_log: list[CorrectionEntry]
    latency_ms: float


class ExplainTablesRequest(BaseModel):
    tables: list[TableDef] = Field(..., min_length=1)
    db_name: str = ""


class ExplainTablesResponse(BaseModel):
    explanation: str
    tables_explained: list[str]
    db_name: str
    latency_ms: float


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_device: str
    model_device_label: str
    adapter_path: str
    uptime_seconds: float
    version: str


class DeviceResponse(BaseModel):
    device: str
    device_label: str
    cuda_available: bool
    cuda_device_count: int
    cuda_device_name: str
    torch_version: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _require_model():
    if not _engine.is_loaded:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Model not loaded",
                "hint": (
                    "Set SQLMIND_ADAPTER to your adapter folder path before "
                    "starting the server, then restart."
                ),
            },
        )


async def _run_in_thread(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn, *args)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    return HealthResponse(
        status="ok" if _engine.is_loaded else "degraded",
        model_loaded=_engine.is_loaded,
        model_device=_engine._device,
        model_device_label=_engine._device_name,
        adapter_path=_adapter_path,
        uptime_seconds=round(time.time() - _startup_time, 1),
        version=app.version,
    )


@app.get("/device", response_model=DeviceResponse, tags=["System"])
async def device_info():
    """Returns detailed GPU / device information."""
    try:
        import torch
        cuda_ok = torch.cuda.is_available()
        return DeviceResponse(
            device=_engine._device,
            device_label=_engine._device_name,
            cuda_available=cuda_ok,
            cuda_device_count=torch.cuda.device_count() if cuda_ok else 0,
            cuda_device_name=torch.cuda.get_device_name(0) if cuda_ok else "N/A",
            torch_version=torch.__version__,
        )
    except ImportError:
        return DeviceResponse(
            device="cpu", device_label="CPU (torch not installed)",
            cuda_available=False, cuda_device_count=0,
            cuda_device_name="N/A", torch_version="N/A",
        )


@app.post("/generate-sql", response_model=GenerateSQLResponse, tags=["AI"])
async def generate_sql(req: GenerateSQLRequest):
    _require_model()

    selected = [
        {"table": s.table, "schema_string": s.schema_string,
         "columns": [c.model_dump() for c in s.columns]}
        for s in req.schemas
    ]
    schema_str = AIEngine.build_schema_string(selected, req.full_context or None)

    log.info(f"generate-sql  q={req.question[:60]!r}  tables={[s.table for s in req.schemas]}")
    log.info(f"  schema_str length: {len(schema_str)} chars")

    t0 = time.time()
    result: GenerationResult = await _run_in_thread(
        lambda: _engine.generate_sql(schema_str, req.question, req.max_retries)
    )
    ms = (time.time() - t0) * 1000

    log.info(f"  → valid={result.valid}  attempts={result.attempts}  {ms:.0f}ms")
    return GenerateSQLResponse(
        sql=result.sql,
        valid=result.valid,
        attempts=result.attempts,
        stage=result.stage,
        error=result.error,
        schema_used=schema_str,
        correction_log=[CorrectionEntry(**e) for e in result.correction_log],
        latency_ms=round(ms, 1),
    )


@app.post("/explain-tables", response_model=ExplainTablesResponse, tags=["AI"])
async def explain_tables(req: ExplainTablesRequest):
    _require_model()

    payload = [
        {"table": t.table, "schema_string": t.schema_string,
         "columns": [c.model_dump() for c in t.columns]}
        for t in req.tables
    ]

    t0 = time.time()
    result: ExplanationResult = await _run_in_thread(
        lambda: _engine.explain_tables(payload, req.db_name)
    )
    ms = (time.time() - t0) * 1000

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error)

    return ExplainTablesResponse(
        explanation=result.explanation,
        tables_explained=[t.table for t in req.tables],
        db_name=req.db_name,
        latency_ms=round(ms, 1),
    )


@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    log.error(f"{request.url.path}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"error": str(exc)})


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="SQLMind API Server")
    parser.add_argument("--adapter", default=os.environ.get("SQLMIND_ADAPTER", ""),
                        help="Path to LoRA adapter directory")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    if args.adapter:
        os.environ["SQLMIND_ADAPTER"] = args.adapter
        sys.modules[__name__]._adapter_path = args.adapter

    print(f"\n  ┌─ SQLMind API Server v{app.version} ─────────────────────────────")
    print(f"  │  Adapter  : {args.adapter or '(not set — set SQLMIND_ADAPTER)'}")
    print(f"  │  URL      : http://{args.host}:{args.port}")
    print(f"  │  Docs     : http://{args.host}:{args.port}/docs")
    print(f"  │  Device   : {AIEngine.detect_device()[1]}")
    print(f"  └──────────────────────────────────────────────────────────────\n")

    uvicorn.run(
        "api_server:app",
        host=args.host,
        port=args.port,
        log_level="info",
        reload=False,
    )
