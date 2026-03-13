from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, Callable

log = logging.getLogger("sqlmind.engine")


# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class GenerationResult:
    sql: str = ""
    valid: bool = False
    attempts: int = 0
    stage: str = ""
    error: str = ""
    correction_log: list[dict] = field(default_factory=list)


@dataclass
class ExplanationResult:
    explanation: str = ""
    success: bool = False
    error: str = ""


# ── Engine ─────────────────────────────────────────────────────────────────────

class AIEngine:
    BASE_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"

    _SYSTEM_PROMPT = (
        "You are a strict SQL generator.\n"
        "You must output ONLY valid SQL.\n"
        "Do not explain.\n"
        "Do not add text."
    )

    def __init__(self):
        self.model = None
        self.tokenizer = None
        self._loaded = False
        self._device = "cpu"
        self._device_name = "CPU"

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @staticmethod
    def detect_device() -> tuple[str, str]:
        """Returns (device_str, human_label): CUDA → MPS → CPU."""
        try:
            import torch
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                vram = torch.cuda.get_device_properties(0).total_memory // (1024 ** 3)
                return "cuda", f"CUDA · {gpu_name} · {vram}GB VRAM"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps", "MPS · Apple Silicon"
        except ImportError:
            pass
        return "cpu", "CPU · No GPU detected"

    def load_model(
        self,
        adapter_path: str,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[bool, str]:
        def _p(msg: str):
            log.info(msg)
            if progress_callback:
                progress_callback(msg)

        try:
            import torch
            from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
            from peft import PeftModel

            device, device_label = self.detect_device()
            self._device = device
            self._device_name = device_label
            _p(f"Device detected: {device_label}")

            _p("Loading tokenizer…")
            self.tokenizer = AutoTokenizer.from_pretrained(
                adapter_path,
                trust_remote_code=True,
            )
            if self.tokenizer.pad_token is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token
            # Left-padding for generation (Qwen2.5 convention)
            self.tokenizer.padding_side = "left"

            if device == "cuda":
                _p("Loading base model in 4-bit NF4 (GPU)…")
                bnb_cfg = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.float16,
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_quant_type="nf4",
                )
                base = AutoModelForCausalLM.from_pretrained(
                    self.BASE_MODEL,
                    quantization_config=bnb_cfg,
                    device_map="auto",
                    trust_remote_code=True,
                )
            elif device == "mps":
                _p("Loading base model on Apple MPS (float16)…")
                base = AutoModelForCausalLM.from_pretrained(
                    self.BASE_MODEL,
                    torch_dtype=torch.float16,
                    device_map="mps",
                    trust_remote_code=True,
                )
            else:
                _p("Loading base model on CPU (float32) — may take ~2 min…")
                base = AutoModelForCausalLM.from_pretrained(
                    self.BASE_MODEL,
                    torch_dtype=torch.float32,
                    device_map="cpu",
                    trust_remote_code=True,
                    low_cpu_mem_usage=True,
                )

            _p("Applying LoRA adapter…")
            self.model = PeftModel.from_pretrained(base, adapter_path)
            self.model.eval()

            if device in ("mps", "cpu"):
                self.model = self.model.to(device)

            self._loaded = True
            _p(f"✓ Model ready [{device_label}]")
            return True, ""

        except Exception as exc:
            self._loaded = False
            log.exception("Model load failed")
            return False, str(exc)

    @staticmethod
    def build_schema_string(
        selected: list[dict],
        full_context: dict | None = None,
    ) -> str:
        parts: list[str] = []
        for s in selected:
            ss = s.get("schema_string", "").strip()
            if not ss:
                cols = ", ".join(c["name"] for c in s.get("columns", []))
                ss = f"{s['table']}({cols})"
            parts.append(ss)
        if full_context:
            for key, ss in full_context.items():
                if ss and ss not in parts:
                    parts.append(ss.strip())
        return "; ".join(parts)

    _GENERATE_BLACKLIST = {"token_type_ids"}

    def _infer(self, prompt: str, max_new_tokens: int = 200) -> str:
        import torch

        enc = self.tokenizer(
            prompt,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=1024,
        )
        for key in self._GENERATE_BLACKLIST:
            enc.pop(key, None)
        enc = {k: v.to(self._device) for k, v in enc.items()}

        with torch.no_grad():
            out = self.model.generate(
                **enc,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                temperature=1.0,
                repetition_penalty=1.15,
                eos_token_id=self.tokenizer.eos_token_id,
                pad_token_id=self.tokenizer.pad_token_id,
            )

        input_len = enc["input_ids"].shape[1]
        raw = self.tokenizer.decode(out[0][input_len:], skip_special_tokens=True).strip()

        # Strip Qwen2.5 chat tokens that may leak
        for stop in ["<|im_start|>", "<|im_end|>", "<|endoftext|>"]:
            if stop in raw:
                raw = raw[:raw.index(stop)]

        return raw.strip()

    def _sql_prompt(self, schema: str, question: str, hint: str = "") -> str:
        """Qwen2.5 chat-template prompt matching train_qwen.jsonl format."""
        if hint:
            user_content = (
                "Use the schema below to write SQL.\n"
                f"Schema: {schema} Question: {question}\n\n"
                f"Your previous SQL was INCORRECT. Fix this specific issue:\n"
                f"{hint}\n"
                "Output ONLY the corrected SQL."
            )
        else:
            user_content = (
                "Use the schema below to write SQL.\n"
                f"Schema: {schema} Question: {question}"
            )
        return (
            f"<|im_start|>system\n{self._SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n{user_content}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )

    def _explain_prompt(self, schemas: list[dict], db_name: str = "") -> str:
        table_block = ""
        for s in schemas:
            cols = s.get("columns", [])
            if cols:
                col_detail = ", ".join(
                    f"{c['name']} ({c['type']})"
                    + (" PK" if c.get("is_primary") else "")
                    + (" FK" if c.get("is_foreign") else "")
                    for c in cols
                )
            else:
                col_detail = s.get("schema_string", s["table"])
            table_block += f"\nTable: {s['table']}\nColumns: {col_detail}\n"

        db_ctx = f" in the '{db_name}' database" if db_name else ""
        instruction = (
            f"Examine the following tables{db_ctx}.\n"
            "For each table, briefly explain:\n"
            "  1. What real-world entity it represents\n"
            "  2. What the key columns mean\n"
            "  3. How it relates to the other tables listed\n"
            "  4. Its significance in this database\n"
            f"Keep each table to 4-5 sentences. Be specific.\n"
            f"{table_block}"
        )
        return (
            f"<|im_start|>system\nYou are a database documentation expert.<|im_end|>\n"
            f"<|im_start|>user\n{instruction}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )

    def generate_sql(
        self,
        schema: str,
        question: str,
        max_retries: int = 4,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> GenerationResult:
        if not self._loaded:
            return GenerationResult(error="Model not loaded.")

        def _p(m):
            if progress_callback:
                progress_callback(m)

        from validator import validate_sql

        result = GenerationResult()
        hint = ""
        sql = ""
        stage = ""
        error = ""

        for attempt in range(1, max_retries + 2):
            _p(f"Attempt {attempt}/{max_retries + 1}…")
            sql = self._infer(self._sql_prompt(schema, question, hint), 200)

            if ";" in sql:
                sql = sql[:sql.index(";") + 1].strip()

            for stop in ["<|im_start|>", "<|im_end|>", "<|endoftext|>"]:
                if stop in sql:
                    sql = sql[:sql.index(stop)].strip()

            valid, stage, error = validate_sql(sql, schema, question)

            result.correction_log.append({
                "attempt": attempt, "sql": sql,
                "valid": valid, "stage": stage, "error": error,
            })

            if valid:
                result.sql = sql
                result.valid = True
                result.attempts = attempt
                result.stage = "PASS"
                result.error = ""
                return result

            if attempt > max_retries:
                break

            _p(f"Correcting [{stage}]: {error[:70]}…")
            hint = f"{stage} ERROR: {error}"

        result.sql = sql
        result.valid = False
        result.attempts = attempt
        result.stage = stage
        result.error = error
        return result

    def explain_tables(
        self,
        schemas: list[dict],
        db_name: str = "",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> ExplanationResult:
        if not self._loaded:
            return ExplanationResult(error="Model not loaded.")
        if progress_callback:
            progress_callback(f"Explaining {len(schemas)} table(s)…")
        try:
            prompt = self._explain_prompt(schemas, db_name)
            text = self._infer(prompt, max_new_tokens=500)
            return ExplanationResult(explanation=text.strip(), success=True)
        except Exception as exc:
            log.exception("explain_tables failed")
            return ExplanationResult(error=str(exc))
