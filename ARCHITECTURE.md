# GrafoMente: Architecture, Limits, and Future Directions

*What this prototype demonstrates, what it deliberately omits, and where a complete implementation would go*

---

## What this repository is

GrafoMente is a **toy application** — a proof of concept, not a production system. Its purpose is narrow and specific: to show that the core flow of the architecture described in the accompanying article series is mechanically sound. Twelve nodes, one technology stack, one language model connector. Nothing more.

This document explains what that demonstration covers, what it does not cover, how far it is from the complete architecture described in the articles, and what a path toward a fuller implementation might look like.

---

## What the prototype demonstrates

### 1. The flow is end-to-end and complete

The pipeline from natural-language query to generated and verified code works without interruption:

```
query → symbolic translation → human instance confirmation
      → progressive fetch → structured RAG → LLM generation
      → sandboxed verification → correction loop → output
```

Every step is implemented, observable, and individually testable via the REST API. Nothing is simulated or mocked.

### 2. Symbolic translation works on a controlled vocabulary

The `Vocabulary` class translates a free-text query into graph node UUIDs using three-pass matching: exact lookup, fuzzy-prefix pattern matching, and semantic field expansion. The fuzzy-prefix mechanism — patterns built from vocabulary terms and applied to query tokens — correctly captures morphological variants and loan words without requiring them to be listed explicitly as synonyms.

This is intentionally symbolic, not neural: it is deterministic, debuggable, and requires no embedding model.

### 3. Progressive fetch stops structurally, not heuristically

The fetch implements a dual stopping criterion: exhaustion of the initial instances *and* exhaustion of all pertinent paths opening from those instances. It does not stop because "enough has been gathered" — it stops because there is nothing left to explore. Paths that are evaluated and found irrelevant are recorded explicitly and shown in the interface.

This makes the system's navigation inspectable in a way that vector-similarity retrieval is not: every decision to include or abandon a node has an explicit, traceable reason.

### 4. The structured RAG is qualitatively different from a conventional RAG

A conventional RAG retrieves textual chunks by vector similarity and passes them raw to the LLM. The RAG produced by the progressive fetch is ordered by prototypical relevance and conceptual hierarchy, and includes typed signatures, official documentation, semantic fields, and structural relations. The LLM receives a context in which the problem's structure is partially resolved — not raw text to interpret from scratch.

### 5. The two-layer node structure is correctly implemented

Each node has a stable layer (invariant in use, referenced by UUID throughout all internal operations) and an episodic layer (append-only, accumulating contextual connections during use). The authority hierarchy — graph > stable layer > episodic layer — is structural in the code, not just a design principle.

The UUID/node_id distinction is correctly maintained throughout: UUID is the machine key for all internal operations; node_id is the human-readable alias exposed to the interface, logs, and vocabulary construction. No internal operation uses node_id as a reference — it is always resolved to UUID first.

### 6. The graph can be scanned from real repositories

The GitHub builder is a scanning tool: it traverses a repository via REST API, extracts candidate entities from source files, and presents them for human review before any import. The two-phase flow — analysis preview, then explicit human confirmation — implements the principle that the graph expands by decision, not by automation. What the builder does not do is decide which entities deserve to become nodes, or where their field boundaries lie: that judgment belongs to the human, assisted or not by other tools.

---

## What the prototype does not demonstrate

### Scalability of the fetch

Twelve nodes is a domain where every path terminates quickly and the fetch always produces a clean, bounded RAG. On a real graph — hundreds of nodes, thousands of structural relations, connections to multiple external graphs — the fetch's behavior under load has not been tested. The dual stopping criterion is correct in principle; its computational cost at scale is an open empirical question.

### Code generation quality

The quality of the generated code depends on two things: the precision of the RAG and the capability of the chosen language model. A well-structured RAG reduces the space for statistical invention — the LLM cannot hallucinate an API that is not in the context — but it does not eliminate semantic errors, architectural misjudgments, or subtle logic bugs. The sandbox correction loop catches execution failures; it does not catch correct-looking code that does the wrong thing.

### Centroid updating and the full episodic mechanism

The current `EpisodicLink` carries a single dimension: `use_count`, the frequency of activation. The complete architecture requires two orthogonal dimensions:

- **Weight**: frequency of activation (already implemented as `use_count`)
- **Distance**: how far the connected node lies from the symbol's centroid in semantic space. Each symbol has a centroid — the most representative point of its conceptual field, established during training. Some nodes cluster tightly around that center (a `middleware` function is close to the centroid of the `middleware` symbol); others sit at the field's periphery, reachable but not defining (a generic `EventEmitter` touched during a logging query is far from it). Distance is this position relative to the center: low distance means the connection touches the core of the concept, high distance means it grazes its edge.

These dimensions are independent and govern different operations. Frequency governs pruning and promotion. Distance governs centroid recalibration. Without the distance dimension, the episodic layer accumulates evidence but cannot use it to update the symbol's field — which is the mechanism that allows the system to adapt to specialized use over time without modifying the underlying graph.

### Graph switching and dormant graphs

The prototype loads a single graph and keeps it active throughout a session. The complete architecture requires the ability to manage multiple graphs with distinct activation states:

- **Active**: directly pertinent to the current project stack, loaded and navigable
- **Dormant**: not pertinent to the current context but reachable if an active symbol calls them through a relevant link — "awakened" by the progressive fetch applied at the graph level rather than the node level
- **Suspended**: archived for a different project context, not reachable in the current session

A Django + React project loads Django and React as primary graphs; Python and JavaScript remain dormant, activated only if a symbol in the active graphs links to them through a pertinent connection. When the project changes to FastAPI + Vue, the Django and React episodic layers are suspended and the FastAPI and Vue layers are loaded. The underlying graph does not change: the access points do.

This is not implemented in the prototype. It requires a session management layer that does not currently exist.

### Symbol centroid recalibration as a periodic operation

In the prototype, the graph is static between sessions and the episodic layer only grows (or is pruned manually). In the complete architecture, accumulated episodic evidence is periodically used to recalibrate each symbol's centroid: if the nodes most frequently reached with lowest distance consistently differ from the prototype established in training, the centroid shifts toward the actual usage pattern.

This recalibration happens in batch, not in real time — continuous updating would make the symbol dependent on query order, destroying its stability as a reference point. The optimal frequency is an open design question: human-supervised batch maintenance is the most controllable approach; automated agents triggering recalculation when episodic divergence from the current centroid exceeds a statistical threshold are an alternative. Neither option excludes the other.

In the programming domain specifically, this mechanism is largely irrelevant in practice: `express.middleware` corresponds to a precise, unambiguous concept within the graph, and no connected node will ever accumulate enough weight and proximity to shift its centroid. The field coincides with the formal API definition — no drift is possible. Centroid recalibration matters in open domains (natural language, conceptual search, text analysis) where the field of a symbol can vary significantly between users or usage periods.

### Symbol selection criteria (upstream of the prompt)

The vocabulary's fuzzy-prefix matching is a deliberate approximation for a harder problem: given a user prompt, what are the right criteria for identifying which symbols to activate? Regex on vocabulary terms works for a controlled domain with a small, stable lexicon. It does not scale to ambiguous queries, polysemous terms, or domains where the relevant symbols are not obvious from the surface form of the query.

Better criteria for symbol selection — whether rule-based, statistically derived, or AI-assisted with human confirmation — are the right direction. An agent that proposes candidate instances from the prompt with explicit reasoning about relevance, presented to the human for confirmation before the fetch begins, would be a concrete step. The human confirmation step already exists in the prototype; what is missing is a smarter upstream proposal mechanism.

### The GitHub builder as a graph construction tool

The current `github_builder.py` is a functional but limited scanning tool. Its limitations are not primarily in the regex extraction — that is an intentional approximation pending better criteria — but in its scope: it only reads source code files, and only from GitHub repositories. A complete graph construction pipeline would need to handle documentation sites, API reference pages, changelogs, type definition packages, and other structured sources that carry information about a domain that source code alone does not contain. The builder will likely need to be substantially redesigned to support heterogeneous input sources, not just incrementally improved.

### The linguistic layer as a separate, bounded component

In the current prototype, the LLM receives the structured RAG and generates code. The model is configurable (local via Ollama or remote via any OpenAI-compatible endpoint), but the boundary between the retrieval layer and the linguistic layer is not as strict as the architecture requires. In a complete implementation, the linguistic layer should have no access to the graph directly — it should only see the serialized RAG. The risk is that a sufficiently capable LLM partially compensates for a poor RAG by drawing on its parametric knowledge, masking retrieval failures. The architecture's value depends on keeping these layers cleanly separated.

---

## The gap between prototype and complete architecture

The table below maps each component of the complete architecture against its status in the prototype.

| Component | Complete architecture | Prototype status |
|---|---|---|
| Graph node — stable layer | UUID, typed signature, semantic field, centroid, prototype weight, structural relations by UUID | ✅ Implemented |
| Graph node — stable layer — `label` | Array of categorizations, updated in batch with centroid | ❌ Single static string; array structure and update mechanism absent |
| Graph node — episodic layer | Processed weight (incremented only on traversal, not on selection or discard) + position in `label` distribution; centroid + `label` updated in batch | ⚠️ Weight only (`use_count`), incremented correctly on traversal; distribution reading, centroid update, and `label` update absent |
| Symbolic translation | Exact → fuzzy-prefix → semantic field expansion | ✅ Implemented |
| Human instance confirmation | Pre-fetch confirmation step | ✅ Implemented |
| Progressive fetch — dual stopping criterion | Instance exhaustion + pertinent path exhaustion | ✅ Implemented |
| Progressive fetch — episodic shortcuts | Use_count bonus on traversal | ✅ Implemented |
| Structured RAG serialization | Ordered by prototypicality and hierarchy | ✅ Implemented |
| Linguistic layer — LLM connector | Local (Ollama) + remote (OpenAI-compatible) | ✅ Implemented |
| Sandboxed verification + correction loop | Pattern blocking + subprocess + retry with error context | ✅ Implemented — Python only; other languages return code without execution |
| Graph construction from GitHub | REST API, two-phase import with preview | ✅ Implemented — scanning tool functional |
| Graph construction — source coverage | Code + docs + type defs + changelogs + API references | ❌ Source code only; builder needs redesign for heterogeneous sources |
| Symbol selection criteria (prompt → instances) | Better criteria, AI-assisted proposal + human confirmation | ⚠️ Fuzzy-prefix regex as deliberate interim approximation |
| Episodic distance dimension | Position in the `label` weight distribution — internal to the system, no external embeddings required | ❌ Not implemented |
| Centroid recalibration | Batch update based on episodic weight + distance distribution | ❌ Not implemented |
| Graph switching — activation states | Active / dormant / suspended per graph | ❌ Not implemented |
| Session management | Context-aware graph loading by project stack | ❌ Not implemented |
| External graph alignment | Source-specific parsers + PARIS/PRASE alignment | ❌ Not implemented |

---

## Directions for a fuller implementation

These are not a roadmap — they are a description of what the architecture requires, ordered from most foundational to most speculative.

### 1. The `label` array in the stable layer

In the current prototype, `label` is a single static string assigned at node creation and never updated — a placeholder. In the complete architecture, `label` is an **array of categorizations**: the set of symbols that have *traversed and processed* this node during fetch navigation, accumulated with their processed weights. A node born with `label: ["os"]` that is subsequently traversed in contexts relating to `component` and `filesystem` will carry, after the next batch update, `label: ["os", "component", "filesystem"]`. This accumulation is constitutive, not descriptive: the array records what the node has proven to be across different usage contexts, and the intersection of those categorizations defines the node's field. The `label` is not a description assigned at creation — it is a characterization that emerges from processed use and is made explicit in the stable layer during the periodic batch operation.

### 2. Weight, distribution, and distance — all within the label

The weight in `use_count` increases only when a node is traversed and included in the pool — not when it is selected as an initial instance, and not when the fetch encounters and discards it. This means the `label` distribution reflects processed relevance, not surface frequency. Together with the human confirmation of instances upstream — which already provides a domain-coherent starting set — this makes the distribution internally meaningful without external grounding. Distance from the centroid is read directly from it: a symbol that traverses this node with high weight is close to the center; one with low weight is peripheral. No external embeddings are required. The most impactful addition to the existing codebase is extending the batch operation to read and interpret this weight distribution already present in the `label` — not adding a separate distance field computed in an external vector space.

### 3. Batch centroid recalibration, `label` update, and node splitting

Once the weight distribution of the `label` is read during the batch operation, a recalibration routine can update centroid and `label` together — the two are inseparable. The centroid records *where* the node sits; the `label` records *what* it has proven to be. Updating one without the other produces an inconsistency that corrupts both fetch navigation and field boundary determination. In the code domain, the sandbox feedback loop adds a further signal: test outcomes retroact on search queries, refining which instances to activate in future navigations and improving the quality of the entry point over time independently of centroid drift.

When the distribution is unimodal — one dominant traversal context — the centroid shifts toward it and the `label` reflects the updated categorization. When plurimodal — several distinct traversing symbols with similar weights and no dominant center — recalibrating a single centroid would be misleading. The batch operation should flag the symbol as a candidate for splitting into two or more distinct nodes, each anchored to one of the peaks. The human decides whether to split, merge, or leave unchanged. Node splitting is the mechanism by which the graph grows in precision guided by actual usage. The graph is only modified after explicit human approval.

### 4. Graph switching and session management

A session layer that maps a project context (defined by a set of active graph IDs) to a set of active and dormant symbols. Loading a new project context suspends the episodic layers of the previous context and activates the relevant graphs. Dormant graphs respond to "awakening" calls from active symbols through the same progressive fetch mechanism used for node traversal — the fetch simply extends across graph boundaries when a pertinent link is found.

### 5. Better symbol selection criteria (upstream of the prompt)

The fuzzy-prefix vocabulary is the current approximation for a harder problem: identifying the right symbols from a user prompt before navigation begins. A more complete approach would pair the existing human confirmation step with a smarter upstream proposal: an agent that reads the prompt, reasons about which graph nodes are likely relevant given the active domain, and generates a ranked candidate list for the human to confirm, adjust, or reject. The fetch mechanism does not change; only the quality of its starting point improves.

### 6. GitHub builder redesign for heterogeneous sources

The current builder reads source code files from GitHub repositories. A complete graph construction pipeline needs to handle documentation sites, API reference pages, type definition packages, changelogs, and other structured sources — because source code alone does not contain all the information that makes a graph node useful (usage patterns, deprecation notes, behavioral contracts, edge cases). The builder will likely need to be substantially redesigned around the notion of a pluggable source adapter, not incrementally extended from its current form.

### 7. Strict RAG/LLM boundary enforcement

Instrument the linguistic layer to log cases where the LLM's output diverges from what the RAG context contains — a signal that the model is drawing on parametric knowledge rather than the structured context. Use this signal to identify gaps in the graph (nodes that should exist but do not) and to evaluate whether the RAG is sufficiently precise for a given query domain.

---

## What this architecture is for — and what it is not for

The architecture is suited to **bounded, verifiable domains** where the conceptual structure of the domain is explicit, stable, and can be represented as a graph of typed nodes with formal relations. Code is the clearest example: the dependency structure is given by the language itself, sources are authoritative and versioned, and correctness can be partially verified by execution.

It is not suited — at least not in its current form — to open-ended generative tasks where the value lies precisely in the model's ability to synthesize across domains, combine concepts in novel ways, or produce text that goes beyond what any structured context could contain. For those tasks, a general-purpose LLM without a graph layer is the right tool.

The distinction matters because it defines where the architecture's cost — building and maintaining a graph, writing parsers, supervising centroid updates — is justified by its benefit: responses that are traceable to specific sources, errors that have an identifiable origin, and a retrieval mechanism that does not depend on the statistical memory of a model trained on data the operator cannot inspect.

---

*This document is part of the GrafoMente repository. The article series that describes the theoretical foundations of the architecture in detail is listed in the main README.*
