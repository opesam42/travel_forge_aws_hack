# TravelForge — AI-Powered Visa Case Preparation Portal

> **AWS SBG UNILAG Hackathon · October 31, 2026**
> Theme: *Building with Open Source AI on Google Gemma*

---

## 👥 Team & Contributors

| Name | Role |
|---|---|
| Kamal | Software Engineer — backend services, LangGraph orchestration, frontend |
| Gbenga | Software Engineer — backend services, LangGraph orchestration, frontend |

> All contributors are listed in the repository commit history. Anyone who contributed code is credited directly in the source.

---

## 🧩 Problem Statement

Travel agencies and visa applicants face a fragmented, high-stakes process when preparing UK visa applications. Critical documents get lost, policy requirements are misunderstood, and errors only surface at submission — resulting in refusals that could have been avoided.

There is no single tool that lets an agency **organize a case, extract and validate applicant information, check it against structured rules, surface problems early, and track it through to manual submission** — all in one place, with clear AI disclosure and human oversight.

---

## 💡 Solution

**TravelForge** is a focused UK visa case preparation portal for travel agencies. It combines:

- **Gemma 4** (open-source LLM via Google AI / Groq) for natural-language intake extraction and grounded remediation advice
- **LangGraph** for agentic workflow orchestration across specialist nodes
- **Deterministic Python rule evaluators** for structured, auditable pass/fail/needs-review decisions
- **pgvector RAG** for policy context retrieval from trusted sources
- **A vanilla JavaScript frontend** served from the same FastAPI origin — no separate build step or runtime framework

The result is an end-to-end agency workflow: applicant intake → document OCR → specialist checks → caseworker review → manual handoff readiness — with clear boundaries between AI assistance and human decisions.

### What TravelForge Does

- Converts natural-language applicant messages into validated structured facts (Gemma 4)
- Extracts passport and financial document data via OCR (unverified until a human reviews it)
- Evaluates case facts against active, source-backed visa rules deterministically
- Presents findings, source citations, missing items, and a grounded remediation checklist to agency staff
- Tracks the case through preparation, human submission handoff, appointment, and decision stages
- After a recorded positive decision, surfaces a travel-preparation checklist (demo data clearly labelled)

### What TravelForge Does NOT Do

- Submit applications to GOV.UK or any official service
- Collect, store, or proxy official-site passwords
- Make or guarantee visa decisions
- Purchase flights, hotels, or transfers
- Automatically activate scraped policy changes

---

## 🏗️ Architecture Overview

```
Applicant text / document upload
            │
            ▼
   Intake Extraction Node
  Gemma 4 → validated Pydantic facts
            │
            ▼
   Document / Route Router
      /             \
     ▼               ▼
Passport          Financial
Specialist        Specialist
(deterministic)   (deterministic)
      \             /
       ▼           ▼
    Rule Result Aggregator
    (active rules, visa_type scoped)
            │
            ▼
   Remediation Node (Gemma 4)
   grounded in structured results
            │
            ▼
  Chat response + case workspace update
  evidence · status · next steps
```

**Key separation of concerns:**

| Layer | Responsibility |
|---|---|
| Gemma 4 | Natural-language understanding, fact extraction, remediation text |
| LangGraph | Workflow orchestration, typed node state passing |
| Deterministic evaluators | Pass / Fail / Not Provided / Needs Review decisions |
| pgvector RAG | Policy citations and context (not decisions) |
| Human reviewer | OCR verification, case approval, submission handoff |

---

## 🛠️ Technologies

| Category | Technology |
|---|---|
| **AI / LLM** | Gemma 4 (`gemma-4-26b-a4b-it`) via Google AI and Groq |
| **Agent orchestration** | LangGraph ≥ 0.2, LangChain ≥ 0.3 |
| **Backend** | FastAPI ≥ 0.110, Python 3.11, Uvicorn |
| **Data validation** | Pydantic v2, SQLModel |
| **Database** | PostgreSQL with pgvector (vector search) |
| **Migrations** | Alembic |
| **Embeddings** | MiniLM-L6-v2 (local ONNX, no external embedding API required) |
| **Document parsing** | PyPDF2, python-docx, BeautifulSoup4 |
| **Frontend** | Plain HTML5, CSS, vanilla JavaScript (no build step) |
| **HTTP client** | httpx |

---

## 📁 Project Structure

```
travel_forge_aws_hack/
├── PLAN.md                          # Hackathon plan and scope
├── README.md                        # This file
└── backend/
    ├── alembic.ini                  # Database migration config
    ├── requirements.txt
    ├── .env.example                 # Environment variable template
    └── app/
        ├── main.py                  # FastAPI app, router registration, startup
        ├── api/                     # REST endpoints
        │   ├── auth.py              # Signup, login, logout
        │   ├── applications.py      # Case CRUD
        │   ├── chat.py              # Case-bound chat
        │   ├── documents.py         # Upload, OCR, review
        │   ├── audit.py             # Audit results
        │   ├── audit_tickets.py     # Staff review tickets
        │   ├── checklist.py         # Case checklist
        │   ├── rules.py             # Active rules (staff only)
        │   ├── trusted_sources.py   # Trusted source management (staff only)
        │   └── staff.py             # Staff case queue, assignment
        ├── core/
        │   ├── config.py            # Settings (pydantic-settings)
        │   ├── database.py          # PostgreSQL init + seeding
        │   ├── llm.py               # Provider/model factory (Gemma 4)
        │   └── schemas.py           # Shared Pydantic schemas
        ├── models/                  # SQLModel ORM models
        ├── services/
        │   ├── chat_graph.py        # LangGraph chat orchestration
        │   ├── audit_graph.py       # LangGraph audit orchestration
        │   ├── compliance_engine.py # Deterministic rule evaluator
        │   ├── embeddings.py        # MiniLM embeddings
        │   ├── rag_service.py       # pgvector retrieval
        │   └── ...
        ├── data/
        │   ├── rules/               # Structured visa rule JSON fixtures
        │   └── raw_data/            # Travel and policy seed data
        ├── static/                  # Frontend (HTML, CSS, vanilla JavaScript)
        │   ├── index.html
        │   ├── app.js
        │   └── styles.css
        └── tests/                   # Test suite
```

---

## ⚙️ Running the Project

### Prerequisites

- Python 3.11+
- PostgreSQL 15+ with the `pgvector` extension enabled
- A Google AI or Groq API key (for Gemma 4)

### 1. Clone and set up the environment

```bash
git clone <your-repo-url>
cd travel_forge_aws_hack/backend

python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```dotenv
DATABASE_URL=postgresql://user:password@localhost:5432/travelforge

# Provide at least one of these:
GOOGLE_AI_API_KEY=your_google_ai_key_here
GROQ_API_KEY=your_groq_api_key_here

# Provider selection (google_genai or groq)
PRIMARY_LLM_PROVIDER=google_genai
GEMMA_MODEL=gemma-4-26b-a4b-it
```

> **Never commit your `.env` file.** It is in `.gitignore`.

### 3. Create the database and run migrations

```bash
# In PostgreSQL, enable pgvector:
# CREATE EXTENSION IF NOT EXISTS vector;

alembic upgrade head
```

### 4. Run the server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open your browser at **http://localhost:8000** — the frontend is served from the same origin.

The API documentation is available at **http://localhost:8000/docs**.

### 5. Run tests

```bash
pytest backend/tests/ -v
```

Key test files:

| File | What it tests |
|---|---|
| `test_rule_evaluator.py` | Deterministic pass/fail/needs-review outcomes |
| `test_audit_graph.py` | LangGraph audit orchestration |
| `test_chat_graph.py` | LangGraph chat orchestration |
| `test_application_intake.py` | Intake extraction and case creation |
| `test_llm_factory.py` | Provider/model factory for Gemma 4 |
| `test_staff_cases.py` | Staff case queue and assignment |
| `test_auth.py` | Authentication and session management |
| `test_documents.py` | Document upload and OCR review |
| `test_portal.py` | End-to-end portal flow |

---

## 🤖 Gemma & AI Usage Disclosure

This project uses **Gemma 4** (`gemma-4-26b-a4b-it`), Google's open-source large language model, accessed via:

- **Google AI (primary):** `google-genai` / `langchain-google-genai`
- **Groq (fallback):** `groq` / `langchain-groq`

### Where AI is used

| Task | Model / Method |
|---|---|
| Applicant intake extraction | Gemma 4 → Pydantic-validated structured facts |
| Remediation advice | Gemma 4 grounded in deterministic evaluator output |
| Chat coordination | Gemma 4 via LangGraph chat graph |
| Audit summarisation | Gemma 4 via LangGraph audit graph |
| Policy context retrieval | pgvector RAG (MiniLM embeddings, no LLM call) |
| Rule pass/fail decisions | **Deterministic Python only — no LLM** |
| OCR verification | **Human review required — OCR output is unverified until confirmed** |

### Where AI is explicitly NOT used

- Visa pass/fail determinations (always deterministic)
- Document field verification (always requires human confirmation)
- Rule activation or modification (operator-only, requires explicit approval)
- Application submission (manual handoff only)

### AI tools used during development

- **Google Gemini (Antigravity)** — code generation, architecture design, debugging, documentation drafting
- All AI-generated code was reviewed, tested, and understood before being committed.

---

## ⚠️ Policy Data Disclaimer

The rule fixtures under `backend/app/data/rules/` are **synthetic demo data** used to demonstrate the evaluator workflow. They are clearly labelled as such in the UI and must not be treated as legal immigration guidance. No threshold, requirement, or outcome shown in this demo constitutes official UK Visas and Immigration advice.

---

## 📦 Credits & Acknowledgements

| Resource | Use |
|---|---|
| [Gemma](https://ai.google.dev/gemma) (Google DeepMind) | Open-source LLM powering intake and remediation |
| [LangGraph](https://langchain-ai.github.io/langgraph/) | Agentic workflow orchestration |
| [LangChain](https://python.langchain.com/) | LLM provider abstractions |
| [FastAPI](https://fastapi.tiangolo.com/) | Backend API framework |
| [SQLModel](https://sqlmodel.tiangolo.com/) | ORM with Pydantic integration |
| [pgvector](https://github.com/pgvector/pgvector) | PostgreSQL vector extension for RAG |
| [Sentence Transformers / MiniLM-L6-v2](https://www.sbert.net/) | Local embedding model |
| [Groq](https://groq.com/) | Gemma 4 inference provider (fallback) |

---

## 🔗 Submission Links

- **Public Repository:** *(add your GitHub URL here)*
- **Demo Video:** *(add your 2-minute demo video link here)*

---
-

*TravelForge supports preparation. Visa decisions are made only by the relevant authority.*
