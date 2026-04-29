# GrafoMente

**Structured RAG from a conceptual graph for code generation.**

GrafoMente is a Python prototype that generates code not by relying on the statistical memory of a language model, but by navigating a graph of prototypical concepts. The LLM receives a structured context built by the graph — not raw text — and only then produces the requested code.

This is a research prototype, not a production system. Its purpose is to demonstrate that the mechanism works on a restricted domain. The architecture is described in detail in the article series this project accompanies.

---

## Table of contents

- [The idea](#the-idea)
- [Architecture](#architecture)
- [How it works: step by step](#how-it-works-step-by-step)
- [Project structure](#project-structure)
- [Requirements and installation](#requirements-and-installation)
- [Configuration](#configuration)
- [Starting the system](#starting-the-system)
- [Building the graph from a GitHub repository](#building-the-graph-from-a-github-repository)
- [API reference](#api-reference)
- [Limits](#limits)
- [Article series](#article-series)

---

## The idea

A conventional RAG retrieves textual chunks by vector similarity and passes them to an LLM. GrafoMente does something structurally different.

The system maintains a graph of **prototypical conceptual nodes** — not word embeddings, but structured objects with typed signatures, official documentation, semantic fields, and weighted relations. When the user writes a query, the system translates it into graph instances, navigates the network by active inclusion and exclusion, and serializes the collected nodes into a structured context. The LLM only ever sees this context — never the raw corpus.

The consequence is that every generated response is traceable to a specific node and an authorized source. Errors have an explicit, identifiable, and correctable origin.

---

## Architecture

The system has three distinct layers with separate responsibilities.

### Layer 1 — The conceptual graph

The permanent structure of nodes, their symbols, and their relations. It is never modified during ordinary use: it is the system's long-term memory. Each node is a `Node` object composed of two separate layers.

**Stable layer** — built in training, invariant in use:

| Field | Description |
|---|---|
| `uuid` | Unique identifier, auto-generated at creation, never modified. Machine primary key for all internal operations. |
| `node_id` | Human-readable alias (`"express.middleware"`). Used in vocabularies, logs, and UI. Not the operational key. |
| `label` | Display name |
| `node_type` | `"function"` \| `"class"` \| `"object"` \| `"method"` |
| `signature` | Full typed signature |
| `docstring` | Official documentation |
| `source_ref` | Authorized source (`"github.com/expressjs/express@4.18.2"`) |
| `hierarchy_path` | Position in the conceptual hierarchy (e.g. `["express", "Router"]`) |
| `semantic_field` | Primary semantic area |
| `intersections` | Semantic fields this node can intersect with |
| `relations` | Structural edges — each references the destination node by UUID |
| `prototypicality` | Rosch typicality weight (1.0 = center of the field) |
| `synonyms` / `keywords` | Vocabulary for query translation |

**Episodic layer** — append-only, accumulated during use:

Each `EpisodicLink` records a contextual connection that emerged during navigation. It stores the `target_uuid` of the connected node (not its label), the intersection field, the nature of the link (`"direct"` \| `"mediated"` \| `"external_bridge"`), and a `use_count` counter that governs pruning. Connections that are never reused are removed during manual maintenance; frequent ones can be promoted to the stable layer.

**Authority hierarchy:** graph > stable layer > episodic layer. The episodic layer cannot modify the stable layer; the stable layer cannot modify the graph. Promotion operations are always manual.

### Layer 2 — Progressive fetch

The mechanism that navigates the graph to build the RAG context. Described in detail in [the next section](#how-it-works-step-by-step).

### Layer 3 — Linguistic layer

An LLM that receives the structured RAG as context and generates the requested code. Configurable as local (Ollama) or remote (any OpenAI-compatible endpoint). The system currently includes a sandbox that verifies the generated code before returning it to the user.

---

## How it works: step by step

### Step 1 — Query → instances

The user writes a natural-language query. The `Vocabulary` class translates it into a list of node UUIDs via three-pass matching:

1. **Exact lookup** — direct match between query token and vocabulary terms.
2. **Fuzzy-prefix** — pre-compiled pattern `^.{0,3}<term>.*$` applied to the query token. Matches morphological variants and loan words: `log` → matches `logging`, `logga`, `re-log`, `prelogging`. The maximum 3-character prefix rule limits noise (`catalog` does not match `log`; `dialog` does).
3. **Semantic field expansion** — if neither lookup finds anything, the token is compared against the field names of all nodes.

The result is a list of UUIDs — the **initial instances** from which navigation starts. These are shown to the user in the interface before the fetch begins. The user can remove non-pertinent instances or add others by `node_id`. The fetch does not start without explicit confirmation.

### Step 2 — Progressive fetch

`ProgressiveFetch` navigates the graph starting from the activated instances. The stopping criterion is **dual**: the fetch stops when all initial instances have been exhausted *and* when all pertinent paths starting from those instances have been evaluated or abandoned.

For each node encountered:
- **If relevant** to the query (by exact or fuzzy-prefix match on its semantic field, label, synonyms, or keywords): included in the pool, traversal continues from it.
- **If not relevant**: marked as abandoned, added to `visited` to prevent re-exploration from other paths.

Candidates for traversal come from two sources: **structural relations** of the stable layer (referenced by UUID) and **episodic shortcuts** of the episodic layer (bonus proportional to `use_count`). The system never explores the entire graph — only the paths that the instances open and that the query makes pertinent.

At the end of navigation, new connections that emerged for the first time are recorded in the episodic layer of the involved nodes. The structural graph is never modified.

### Step 3 — Structured RAG

The collected pool is ranked by prototypicality and lexical overlap with the query. The `RAGSerializer` serializes it into structured text — not raw text, but an already-interpreted context that includes signatures, documentation, semantic fields, hierarchy, and relations. The LLM receives this and is instructed to use only the APIs present in the context.

### Step 4 — Code generation and sandboxed verification

The `LLMConnector` sends the RAG to the configured model. The generated code passes through `SandboxVerifier`:

1. **Explicit blocking of dangerous patterns** before any execution: `os.system`, `subprocess`, `eval`, `exec`, file writes, `__import__`, `ctypes`, and others.
2. **Execution in an isolated subprocess** with configurable timeout.
3. **Correction loop**: if execution fails, the error message is re-inserted into the prompt (same RAG context + error) and generation is retried, up to `max_attempts` times.

If the code does not pass the sandbox after all attempts, it is returned to the user with an explicit warning.

---

## Project structure

```
grafo_mente/
├── core/
│   ├── graph.py            — data structures (Node, StableLayer, EpisodicLayer)
│   ├── vocabulary.py       — query → instances translation
│   ├── fetch.py            — progressive fetch with dual stopping criterion
│   ├── rag.py              — RAG serialization and LLM connector
│   ├── sandbox.py          — sandboxed verification with correction loop
│   └── github_builder.py   — graph construction from GitHub via REST API
├── api/
│   └── server.py           — FastAPI backend (orchestrator)
├── ui/
│   └── index.html          — browser interface (HTML/CSS/JS)
├── data/
│   ├── seed_graph.py       — example seed graph (12 nodes, Express + Node.js)
│   ├── config.json         — LLM, fetch, and sandbox configuration
│   ├── graph/
│   │   └── graph.json      — persisted graph
│   ├── vocabulary/
│   │   └── vocab.json      — vocabulary for query translation
│   └── episodic/           — episodic data (managed by the server)
├── run.py                  — startup script
└── requirements.txt
```

---

## Requirements and installation

**Python 3.10+** is required.

```bash
# Clone the repository
git clone https://github.com/your-username/grafo_mente.git
cd grafo_mente

# Install dependencies
pip install -r requirements.txt
```

Dependencies: `fastapi`, `uvicorn[standard]`, `requests`, `pydantic`. No additional dependencies are required for the graph, vocabulary, fetch, and builder modules.

**For local model usage**, [Ollama](https://ollama.com) must be installed separately:

```bash
# Install Ollama (macOS/Linux)
curl -fsSL https://ollama.com/install.sh | sh

# Download a code model
ollama pull deepseek-coder
# or
ollama pull codellama
```

---

## Configuration

`data/config.json` controls the three configurable subsystems. The file is also editable from the interface (Configuration tab) without restarting the server.

```json
{
  "llm": {
    "mode": "local",
    "url": "http://localhost:11434",
    "model": "deepseek-coder",
    "api_key": "",
    "max_tokens": 2048,
    "temperature": 0.2
  },
  "fetch": {
    "max_depth": 4,
    "max_rag_nodes": 15
  },
  "sandbox": {
    "enabled": true,
    "timeout": 10,
    "max_attempts": 3,
    "language": "python"
  }
}
```

| Field | Description |
|---|---|
| `llm.mode` | `"local"` (Ollama) or `"remote"` (OpenAI-compatible endpoint) |
| `llm.url` | Ollama URL or remote API endpoint (e.g. `https://api.deepseek.com`) |
| `llm.model` | Model name (e.g. `deepseek-coder`, `gpt-4o`, `deepseek-coder-v2`) |
| `llm.api_key` | API key for remote mode; leave empty for local mode |
| `fetch.max_depth` | Maximum traversal depth from each instance (default: 4) |
| `fetch.max_rag_nodes` | Maximum number of nodes in the RAG context (default: 15) |
| `sandbox.enabled` | Enable sandboxed verification |
| `sandbox.timeout` | Maximum execution time in seconds (default: 10) |
| `sandbox.max_attempts` | Maximum correction loop attempts (default: 3) |
| `sandbox.language` | Language for sandbox execution (`"python"` supported; other languages receive the generated code without execution) |

---

## Seed graph: the 12 nodes

The default graph covers an **Express + Node.js** stack. These are the nodes available out of the box — the `node_id` is what you use to add instances manually in the interface.

| `node_id` | Type | Semantic field | What it represents |
|---|---|---|---|
| `express.middleware` | function | middleware | Core middleware signature `(req, res, next) => void` |
| `express.app` | object | http_server | The Express application object |
| `express.router` | class | routing | `express.Router()` — isolated mini-app for routing |
| `express.request` | object | request_handling | The `req` object — params, query, body, headers |
| `express.response` | object | request_handling | The `res` object — send, json, status, redirect |
| `express.next` | function | middleware | `NextFunction` — passes control to the next middleware or error handler |
| `express.error_handler` | function | error_handling | 4-argument error middleware `(err, req, res, next) => void` |
| `express.json_middleware` | function | middleware | `express.json()` — built-in body parser for JSON requests |
| `js.date` | class | datetime | JavaScript `Date` — `Date.now()`, `toISOString()`, timestamps |
| `js.console` | object | logging | `console.log/error/warn` — standard output |
| `node.fs` | object | utilities | Node.js `fs` module — readFile, writeFile, streams |
| `node.path` | object | utilities | Node.js `path` module — join, resolve, dirname, basename |

**Semantic fields in use:** `middleware`, `http_server`, `routing`, `request_handling`, `error_handling`, `datetime`, `logging`, `utilities`.

**Example queries the seed graph handles well:**
- *"write an Express middleware that logs method, URL, and timestamp"*
- *"create an error handler that returns JSON with status 500"*
- *"add body JSON parsing to an Express app"*
- *"write a router with a GET route that reads a file and returns its content"*

Queries involving frameworks or APIs outside this list (React, Prisma, databases, authentication) will produce sparse or empty fetch results — the graph simply does not contain those nodes. Use the **Import from GitHub** panel to extend coverage.

> **Note on `label` in the current prototype:** each node currently carries a single-string `label` (e.g. `"middleware"`, `"Router"`, `"Date"`) assigned at creation and never updated. This is a static approximation. In the complete architecture, `label` is an array of categorizations that accumulates from use and is updated together with the centroid during the periodic batch operation — see `ARCHITECTURE.md` for details.

```bash
# First start — builds the seed graph automatically
python run.py

# Rebuild the seed graph from scratch
python run.py --rebuild-graph
```

The interface opens automatically in the browser. The backend runs on `localhost:8000` and stays active until `Ctrl+C`.

### Interface walkthrough

The interface guides the user through four explicit steps:

**Step 1 — Query**: write the request in natural language (Italian or English, both supported).

**Step 2 — Instances**: the system shows the identified instances (node labels with their semantic field). The user can remove non-pertinent ones or add others by `node_id`. Only after explicit confirmation does the fetch start.

**Step 3 — Fetch results**: after navigation, the system shows the collected pool (nodes with signatures and relations), the textual structured RAG, the abandoned paths (transparency panel showing what was evaluated and discarded), and the new episodic links created.

**Step 4 — Generated code**: the code appears with the sandbox status (green = verified, amber = not verified), the number of attempts used, and a copy button.

**Graph panel**: shows all nodes with their stable and episodic layers (expandable). Includes the episodic pruning button — a manual maintenance operation that removes links with `use_count` below the configured threshold.

**Import from GitHub panel**: described in the next section.

---

## Building the graph from a GitHub repository

`github_builder.py` constructs graph nodes from a GitHub repository using exclusively the REST API — no local clone, no additional dependencies.

The pipeline has five phases:

1. **Fetching the tree** — single call to `/git/trees?recursive=1` for the complete file list.
2. **Filtering** — selects `.js`, `.ts`, `.py`, `.jsx`, `.tsx`, `.md` files; excludes `node_modules`, `dist`, `build`, `coverage`, `.next`, and similar folders; discards test files.
3. **Extraction by regex** — extracts exported functions, classes, TypeScript interfaces, `module.exports`, Python functions and classes with their docstrings. JSDoc comments are paired with the following entity.
4. **Categorization by path** — maps the file path to the semantic field using a priority-ordered pattern list (`middleware/` → `"middleware"`, `auth/` → `"authentication"`, `db/` → `"database"`, etc.). The entity name refines categorization when the path is insufficient.
5. **Relation inference** — reconstructs structural relations by path hierarchy and semantic field. All relations reference the destination node by UUID.

Each extracted node has the same structure as a manually built node: UUID auto-generated by `make_stable_layer()`, readable `node_id` derived from path and entity name (e.g. `"middleware.logger"`), and all stable layer fields. Once imported, the node is indistinguishable from one built by hand.

**From the interface** (Import from GitHub tab):
- Enter owner, repository name, branch (default `main`), optional path filter, and optional GitHub token.
- The system shows a **preview** of extracted nodes with their field, type, and prototypicality before any import.
- Only after explicit confirmation are nodes imported into the graph and the vocabulary regenerated.

A GitHub token is not required for public repositories, but is recommended: without it the rate limit is 60 requests/hour; with token it rises to 5,000/hour.

> **Note on coverage**: regex-based extraction works well on well-structured repositories with standard conventions. On highly dynamic or idiosyncratic codebases, human review of the preview before import is recommended. It is not a substitute for full AST parsing — it is a practicable approximation for a prototype.

---

## API reference

The backend exposes the following endpoints at `localhost:8000`. Full documentation is available at `localhost:8000/docs` (Swagger UI) after starting the server.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/status` | System status and LLM connection check |
| `GET` | `/graph/nodes` | List of all nodes (stable + episodic layer) |
| `GET` | `/graph/node/{node_id}` | Single node by `node_id` |
| `GET` | `/graph/node/uuid/{uuid}` | Single node by UUID |
| `POST` | `/query/translate` | Translate query → instances (returns UUID + node_id for the UI) |
| `POST` | `/query/fetch` | Progressive fetch from confirmed UUIDs; returns pool + RAG |
| `POST` | `/query/generate` | Full pipeline: fetch + generation + sandbox |
| `POST` | `/graph/episodic/prune` | Remove episodic links below `min_use_count` |
| `POST` | `/graph/build/github` | Analyze a GitHub repository and write nodes to staging |
| `POST` | `/graph/build/commit` | Import staging nodes into the graph (requires confirmation) |
| `GET` | `/graph/build/staging` | Preview of nodes in staging |
| `DELETE` | `/graph/build/staging` | Delete staging without importing |
| `GET` | `/config` | Read current configuration |
| `POST` | `/config` | Update configuration |

---

## Limits

The prototype demonstrates that the mechanism works. It does not demonstrate scalability or code generation quality in a general sense.

**Domain coverage**: the seed graph has 12 nodes on an Express + Node.js stack. A real graph for a complete framework (e.g. Next.js 14 + TypeScript + Prisma) would require hundreds of nodes and thousands of relations. Fetch calibration on a larger graph requires empirical testing not provided here.

**Code generation quality**: depends on the chosen model and the precision of the RAG. A correct RAG reduces the error space but does not eliminate it. The sandbox correction loop catches execution errors, not subtle semantic errors.

**Regex-based extraction**: works well on well-organized repositories. Performs worse on codebases with non-standard or heavily dynamic conventions.

**Sandbox**: currently supports Python for execution. For other languages (JavaScript, TypeScript), the code is returned without execution verification.

These are not defects to be corrected in future versions: they are the honest limits of what a prototype of this kind can demonstrate.

---

## Article series

GrafoMente is the working prototype described in the fourth article of a series that develops the theoretical and architectural foundations of the system:

1. **Towards a Digital *Characteristica Universalis*** — philosophical foundations, the ideographic hypothesis, Word Models as a research direction.
2. **The Graph as Mind** — prototypical graph architecture, the composite two-layer symbol, progressive fetch by instances, technical objections and answers.
3. **From Graph to Code** — application to code generation, DeepSeek as linguistic layer, available technology stack.
4. **GrafoMente: A Prototype** *(this repository)* — working implementation, code description, honest limits.

---

*Writing, source verification, and code were developed with the support of Claude (Anthropic). The central proposals — graph with stable and episodic layers, progressive fetch with dual stopping criterion, structured RAG, GitHub builder via REST API, vocabulary with fuzzy-prefix matching — are the author's own.*
