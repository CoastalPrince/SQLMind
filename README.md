# 🧠 SQLMind — Local Text-to-SQL AI Assistant

<p align="center">
  <img src="https://img.shields.io/badge/Model-Qwen2.5--1.5B--Instruct-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Fine--tuning-QLoRA%20%2B%20PEFT-orange?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Backend-FastAPI-green?style=for-the-badge" />
  <img src="https://img.shields.io/badge/UI-PyQt6-purple?style=for-the-badge" />
  <img src="https://img.shields.io/badge/License-MIT-lightgrey?style=for-the-badge" />
</p>

> **Convert natural language to production-ready SQL — fully local, no cloud API required.**

SQLMind is an end-to-end Text-to-SQL system powered by a fine-tuned `Qwen2.5-1.5B-Instruct` model. It runs entirely on your machine, keeping your database schemas and queries completely private.

---

## ✨ Features

- **Natural Language → SQL** — Ask questions in plain English; get accurate, executable SQL
- **3-Stage Validation Pipeline** — Syntax → Schema → Semantic validation before query execution
- **Auto-Correction Loop** — Detects and fixes `SYNTAX`, `SCHEMA`, and `SEMANTIC` errors automatically with contextual hints
- **RAG-Enhanced Schema Matching** — Multi-table retrieval-augmented generation for complex joins
- **Desktop UI** — Clean PyQt6 application with live database connectivity
- **REST API** — FastAPI backend for programmatic access and integration
- **100% Local** — No OpenAI, no Anthropic, no data leaves your device

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    PyQt6 Desktop UI                     │
│              (Query input, Results viewer)              │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP
┌────────────────────────▼───────────────────────────────┐
│                   FastAPI Backend                      │
│  ┌──────────────┐  ┌───────────────┐  ┌─────────────┐  │
│  │  AI Engine   │  │   Validator   │  │Schema Match │  │
│  │ Qwen2.5-1.5B │  │ Syntax+Schema │  │  RAG-based  │  │
│  │  (QLoRA)     │  │  +Semantic    │  │             │  │
│  └──────────────┘  └───────────────┘  └─────────────┘  │
└────────────────────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│              Database Layer (SQLite / MySQL / PG)       │
└─────────────────────────────────────────────────────────┘
```

---

## 🧰 Tech Stack

| Component | Technology |
|-----------|-----------|
| Base Model | `Qwen/Qwen2.5-1.5B-Instruct` |
| Fine-tuning | QLoRA + PEFT (LoRA rank 16) |
| Quantization | 4-bit via `bitsandbytes` |
| Training Framework | Hugging Face `transformers` + `trl` |
| Backend | FastAPI |
| Frontend | PyQt6 |
| DB Connectors | SQLite, MySQL, PostgreSQL |

---

## 🚀 Quickstart

### Prerequisites
- Python 3.10+
- 6 GB+ VRAM (GPU) **or** 8 GB+ RAM (CPU, slower)
- Git

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/sqlmind.git
cd sqlmind
```

### 2. Install dependencies

```bash
pip install -r server/requirements_server.txt
```

### 3. Add your fine-tuned adapter

Place your trained LoRA adapter in:
```
server/qwen25_sql_adapter/
  ├── adapter_config.json
  ├── adapter_model.safetensors
  └── tokenizer files...
```

> **Don't have an adapter yet?** Fine-tune your own using the included notebook — see [Fine-tuning](#-fine-tuning) below.

### 4. Start the server

```bash
# Linux / macOS
bash server/start_server.sh

# Windows
server\start_server.bat
```

### 5. Launch the UI

```bash
python app/main.py
```

---

## 🎓 Fine-tuning

The repository includes a complete fine-tuning notebook:

📓 **`Qwen2.5_SQL_Finetune_RAG.ipynb`** — Google Colab ready

**What the notebook does:**
1. Validates your `train_qwen.jsonl` is in Qwen2.5 chat template format
2. Loads `Qwen2.5-1.5B-Instruct` in 4-bit (QLoRA)
3. Fine-tunes with PEFT LoRA on your SQL dataset
4. Saves the adapter to Google Drive
5. Includes inference + validation cells to test before deploying

**Training configuration:**
```python
MAX_SEQ_LEN   = 1024
NUM_EPOCHS    = 14
BATCH_SIZE    = 2
GRAD_ACCUM    = 4
LEARNING_RATE = 2e-4
LORA_RANK     = 16
LORA_ALPHA    = 32
```

**Target modules (LoRA):** `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`

---

## 📁 Project Structure

```
sqlmind/
├── app/                          # PyQt6 frontend
│   ├── main.py                   # App entry point
│   ├── ui/                       # UI components
│   ├── db_connector.py           # Database connection manager
│   ├── schema_matcher.py         # RAG-based schema retrieval
│   └── api_client.py             # FastAPI client wrapper
│
├── server/                       # Backend
│   ├── ai_engine.py              # Qwen2.5 inference engine
│   ├── validator.py              # 3-stage SQL validator
│   ├── api_server.py             # FastAPI routes
│   ├── qwen25_sql_adapter/       # LoRA adapter weights (add yours here)
│   ├── requirements_server.txt
│   ├── start_server.sh
│   └── start_server.bat
│
├── Qwen2.5_SQL_Finetune_RAG.ipynb   # Fine-tuning notebook
├── train.jsonl                       # Training data (299 examples)
└── README.md
```

---

## 🔄 Validation Pipeline

SQLMind runs every generated query through a 3-stage pipeline before returning it:

```
Generated SQL
     │
     ▼
┌─────────────┐     FAIL → Correction Hint → Re-generate
│   Stage 1   │
│   Syntax    │ — Checks SQL grammar and structure
└──────┬──────┘
       │ PASS
       ▼
┌─────────────┐     FAIL → Correction Hint → Re-generate
│   Stage 2   │
│   Schema    │ — Validates table/column names against live schema
└──────┬──────┘
       │ PASS
       ▼
┌─────────────┐     FAIL → Correction Hint → Re-generate
│   Stage 3   │
│  Semantic   │ — Executes dry-run; checks for logical errors
└──────┬──────┘
       │ PASS
       ▼
  Return SQL ✅
```

---

## 🔑 API Reference

### `POST /generate`

Generate SQL from a natural language question.

```json
{
  "question": "Show me all customers who placed orders in the last 30 days",
  "schema": "customers(id, name, email), orders(id, customer_id, created_at, total)"
}
```

**Response:**
```json
{
  "sql": "SELECT c.id, c.name, c.email FROM customers c JOIN orders o ON c.id = o.customer_id WHERE o.created_at >= DATE('now', '-30 days');",
  "validated": true,
  "corrections": 0
}
```

---|---|---|
| Parameters | 1.1B | 1.5B |
| Instruction tuning | Weak | Strong (RLHF-aligned) |
| SQL accuracy | Baseline | Improved |
| VRAM (4-bit) | ~4 GB | ~4 GB |
| Context length | 2048 | 32768 |

---

## 📋 Requirements

```
transformers>=4.45.0
peft>=0.13.0
accelerate>=0.34.0
bitsandbytes>=0.43.0
torch>=2.1.0
fastapi>=0.110.0
uvicorn>=0.29.0
PyQt6>=6.6.0
sqlalchemy>=2.0.0
```

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Commit your changes (`git commit -m 'Add your feature'`)
4. Push to the branch (`git push origin feature/your-feature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgements

- [Qwen2.5](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct) by Alibaba Cloud
- [PEFT](https://github.com/huggingface/peft) by Hugging Face
- [bitsandbytes](https://github.com/TimDettmers/bitsandbytes) by Tim Dettmers

---

<p align="center">Built with ❤️ | Star ⭐ if you find this useful!</p>
