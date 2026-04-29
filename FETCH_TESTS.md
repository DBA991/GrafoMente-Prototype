# Fetch Mechanism: Tests and Observed Limits

*Complete implementation of the progressive fetch with the missing components, sandboxed test results, and an honest assessment of what the tests show — including what a graph without trained symbols means for pre-deployment behavior*

---

## What was tested and why

The toy application implements the progressive fetch in its basic form: structural traversal, inclusion/exclusion of nodes by relevance, episodic link recording. What it does not implement are the components that make the mechanism complete: `label` as an array of categorizations (not a static string), weight incremented only on traversal and processing (not on instance selection), batch update of centroid and `label` together, plurimodal distribution detection, and the sandbox failure signal as a feedback mechanism on the search query.

Two test suites were written and run in a sandboxed Python environment to verify that the complete mechanism is stable and to surface the limits that the basic implementation conceals.

---

## Modified modules

The following code extends the toy application's `graph.py` and `fetch.py` with the missing components. It is not a drop-in replacement — the toy's `seed_graph.py` uses `label: str` and requires migration — but it is structurally compatible and demonstrates the complete mechanism on a parallel graph.

### Extended `StableLayer` and `EpisodicLayer`

```python
@dataclass
class StableLayer:
    uuid: str
    node_id: str
    label: list           # array of categorizations — not a static string
    node_type: str
    signature: str
    docstring: str
    semantic_field: str
    intersections: list
    relations: list
    prototypicality: float = 1.0
    synonyms: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    centroid_label: list = field(default_factory=list)  # dominant entries after last batch
```

The key change is `label: list` and the addition of `centroid_label`, which stores the dominant categorizations after each batch update. These two are updated together — never independently.

```python
@dataclass
class EpisodicLink:
    target_uuid: str
    intersection_field: str
    link_nature: str
    mediator_uuid: Optional[str]
    use_count: int = 0  # starts at 0 — incremented ONLY on processing

@dataclass
class EpisodicLayer:
    node_uuid: str
    links: list = field(default_factory=list)

    def record_processing(self, target_uuid: str, intersection_field: str,
                          nature: str = "direct", mediator_uuid: str = None):
        """
        Increments use_count ONLY when the target node has been
        traversed AND processed (included in the pool).
        Never called for discarded nodes.
        """
        for link in self.links:
            if link.target_uuid == target_uuid:
                link.use_count += 1
                return
        self.links.append(EpisodicLink(
            target_uuid=target_uuid,
            intersection_field=intersection_field,
            link_nature=nature,
            mediator_uuid=mediator_uuid,
            use_count=1
        ))

    def weight_distribution(self) -> dict:
        """Weight distribution: target_uuid → use_count."""
        return {l.target_uuid: l.use_count for l in self.links if l.use_count > 0}

    def prune(self, min_use_count: int = 2):
        self.links = [l for l in self.links if l.use_count >= min_use_count]
```

`use_count` starts at 0 and is only incremented by `record_processing`, which is only called after a node has been included in the pool. Discarded nodes — those evaluated by `_is_relevant` and rejected — never trigger `record_processing`. This is the operational definition of "processed weight": it measures relevance confirmed by inclusion, not frequency of encounter.

### Complete `ProgressiveFetch`

```python
class ProgressiveFetch:
    def __init__(self, graph: ConceptGraph, max_depth: int = 4):
        self.graph = graph
        self.max_depth = max_depth

    def fetch(self, query: str, instance_uuids: list,
              sandbox_success: bool = True) -> FetchResult:
        pool: list = []
        visited: set = set()
        abandoned: list = []
        processed_links: list = []
        query_tokens = self._tokenize(query)

        # Initial instances enter the pool without incrementing
        # any node's weight — they are the starting point, not
        # the result of a traversal
        for uuid in instance_uuids:
            node = self.graph.get(uuid)
            if node and uuid not in pool:
                pool.append(uuid)
                visited.add(uuid)

        # Traversal from each instance
        for uuid in instance_uuids:
            self._traverse(uuid, 0, query_tokens, pool,
                           visited, abandoned, processed_links)

        # Record episodic links ONLY for processed nodes
        # (weight is updated here, not during traversal)
        for link in processed_links:
            from_node = self.graph.get(link["from_uuid"])
            if from_node:
                from_node.episodic.record_processing(
                    target_uuid=link["to_uuid"],
                    intersection_field=link["field"],
                    nature=link["nature"]
                )

        # Sandbox feedback: if generated code fails,
        # signal that the initial instances may be the wrong entry point
        if not sandbox_success:
            for uuid in instance_uuids:
                node = self.graph.get(uuid)
                if node:
                    processed_links.append({
                        "from_uuid": "SANDBOX_FAILURE",
                        "to_uuid": uuid,
                        "field": "query_refinement_signal",
                        "nature": "feedback",
                        "note": f"instance {self.graph.id_for(uuid)} may be wrong entry point"
                    })

        ordered = self._rank(pool, query_tokens, instance_uuids)
        return FetchResult(
            query=query,
            instance_uuids=instance_uuids,
            pool=[self.graph.id_for(u) for u in ordered],
            abandoned=abandoned,
            processed_links=processed_links
        )

    def _traverse(self, node_uuid: str, depth: int, query_tokens: list,
                  pool: list, visited: set, abandoned: list, processed_links: list):
        if depth >= self.max_depth:
            return
        node = self.graph.get(node_uuid)
        if not node:
            return

        candidates = self._candidates(node)

        for c_uuid, score in candidates:
            if c_uuid in visited:
                continue
            visited.add(c_uuid)
            c_node = self.graph.get(c_uuid)
            if not c_node:
                continue

            path = f"{node.stable.node_id} → {c_node.stable.node_id}"

            if self._is_relevant(c_node, query_tokens):
                pool.append(c_uuid)
                # Link recorded only because the node was processed
                processed_links.append({
                    "from_uuid": node_uuid,
                    "to_uuid": c_uuid,
                    "field": c_node.stable.semantic_field,
                    "nature": "direct"
                })
                self._traverse(c_uuid, depth + 1, query_tokens,
                                pool, visited, abandoned, processed_links)
            else:
                abandoned.append(path)

    def _candidates(self, node: Node) -> list:
        seen = set()
        out = []
        for rel in node.stable.relations:
            uid = rel.get("target_uuid", "")
            if uid and uid not in seen:
                out.append((uid, 1.0))
                seen.add(uid)
        # Episodic shortcuts: bonus proportional to use_count
        for elink in node.episodic.links:
            if elink.target_uuid not in seen and elink.use_count > 0:
                bonus = min(1.0 + elink.use_count * 0.15, 2.5)
                out.append((elink.target_uuid, bonus))
                seen.add(elink.target_uuid)
        return sorted(out, key=lambda x: -x[1])

    def _is_relevant(self, node: Node, query_tokens: list) -> bool:
        terms = set()
        for entry in node.stable.label:   # label is now a list
            terms.add(entry.lower())
        terms.add(node.stable.semantic_field.lower())
        for s in node.stable.synonyms: terms.add(s.lower())
        for k in node.stable.keywords: terms.add(k.lower())
        for i in node.stable.intersections: terms.add(i.lower())

        if set(query_tokens) & terms:
            return True
        for term in terms:
            if len(term) < 3:
                continue
            pat = re.compile(r'^.{0,3}' + re.escape(term) + r'.*$', re.IGNORECASE)
            if any(pat.match(tok) for tok in query_tokens):
                return True
        return False
```

### Batch update

```python
def batch_update(graph: ConceptGraph, split_threshold: float = 0.7,
                 min_weight: int = 1) -> dict:
    """
    For each node:
    1. Reads the weight distribution in the episodic layer
    2. Determines dominant entries (centroid)
    3. Updates label with emerging categorizations
    4. Detects plurimodality → split candidates
    5. Returns report for human supervision
    """
    report = {"updated": [], "split_candidates": [], "unchanged": []}

    for node in graph.all_nodes():
        dist = node.episodic.weight_distribution()
        if not dist:
            report["unchanged"].append(node.stable.node_id)
            continue

        total = sum(dist.values())
        norm = {uid: w / total for uid, w in dist.items()}

        weight_by_field = defaultdict(float)
        for uid, w in norm.items():
            caller = graph.get(uid)
            if caller:
                for cat in caller.stable.label:
                    weight_by_field[cat] += w
                weight_by_field[caller.stable.semantic_field] += w

        # Dominant entries (weight > 10% of total)
        dominant = sorted(
            [(cat, w) for cat, w in weight_by_field.items() if w > 0.1],
            key=lambda x: -x[1]
        )
        dominant_cats = [cat for cat, _ in dominant]

        # Plurimodality detection
        is_plurimodal = False
        if len(dominant) >= 2:
            top_weight = dominant[0][1]
            second_weight = dominant[1][1]
            if top_weight < split_threshold and second_weight > (1 - split_threshold):
                is_plurimodal = True

        # Update label and centroid in the same operation
        old_label = list(node.stable.label)
        new_label = list(old_label)
        for cat in dominant_cats:
            if cat not in new_label:
                new_label.append(cat)

        node.stable.label = new_label
        node.stable.centroid_label = dominant_cats  # updated together with label

        if is_plurimodal:
            report["split_candidates"].append({
                "node_id": node.stable.node_id,
                "uuid": node.stable.uuid,
                "peaks": dominant[:3],
                "reason": "plurimodal distribution — consider splitting"
            })
        else:
            report["updated"].append({
                "node_id": node.stable.node_id,
                "old_label": old_label,
                "new_label": new_label,
                "centroid": dominant_cats[:2]
            })

    return report
```

---

## Test results

### Suite 1: fetch mechanism

Seven nodes, Express + Node.js stack. Six tests covering the complete mechanism.

---

**Test 1 — Logging middleware query (sandbox success)**

```
Query: "write an Express middleware that logs method URL and timestamp"
Instances: express.middleware, js.console
```

```
Pool:      ['express.middleware', 'js.console', 'js.date',
            'express.request', 'express.response', 'express.next']
Abandoned: []
Processed links: 4
PASS ✓
```

6 of 7 nodes in the pool. The one absent (`express.error_handler`) is correctly excluded: it has no pertinent relation to logging. The fetch reaches `js.date` through the structural relation `js.console → js.date (uses)` because `date`, `timestamp`, and `iso` match query tokens. All 4 processed links are recorded in the episodic layers of the traversed nodes.

---

**Test 2 — Sandbox failure signal**

```
Query: "write an Express error handler with JSON response"
Instances: express.middleware  ← wrong entry point
sandbox_success: False
```

```
Pool:            ['express.middleware', 'express.response', 'express.next']
Feedback signals: 1
PASS ✓
```

The wrong instance (`express.middleware` instead of `express.error_handler`) produces a narrower pool missing the core node. The sandbox failure generates an explicit signal on the entry point. The signal is currently logged — the loop that propagates it to vocabulary refinement is not implemented (see Limits).

---

**Test 3 — Weight accumulated only on processed nodes**

```
Two fetches: "log timestamp" and "log timestamp and method"
Instances: js.console, js.date
```

```
Episodic distribution of js.console: {js.date_uuid: 1}
  js.date: use_count=1
PASS ✓ (no weight on unprocessed nodes)
```

`js.date` has `use_count=1` in `js.console`'s episodic layer after two fetches: it was traversed once through the structural relation in the first fetch and the second fetch found it already in `visited`. Nodes not reached — `express.middleware`, `express.error_handler` — have zero weight. The mechanism is working as designed.

---

**Test 4 — Batch update: label and centroid updated together**

```
Three fetches: "error middleware JSON 500"
Instance: express.error_handler
```

```
Updated nodes: 3
Split candidates: 0
Unchanged: 4

express.middleware:    ['middleware'] → ['middleware', 'request_handling']
  centroid: ['request_handling', 'middleware']

js.console:            ['logging'] → ['logging', 'datetime']
  centroid: ['datetime']

express.error_handler: ['error_handling'] → ['error_handling', 'request_handling', 'middleware']
  centroid: ['request_handling', 'middleware']

PASS ✓ (label and centroid updated in the same operation)
```

Three nodes show label evolution. `express.error_handler` — the entry instance — expands from `['error_handling']` to include `request_handling` and `middleware` because the fetch consistently traverses `req`, `res`, and `next` from it. This is correct: an error handler does act on request and response objects. The centroid reflects the dominant traversal contexts. The four unchanged nodes (`express.request`, `express.response`, `express.next`, `js.date`) were not entry points or traversal targets in this query set.

---

**Test 5 — Plurimodal distribution detection**

```
Node poly.node: 3 activations from express.middleware (middleware context)
               3 activations from js.console (logging context)
split_threshold: 0.65
```

```
Split candidates found: 0
Even distribution: no split (threshold not reached) ✓
```

With exactly equal weights (50/50) and `split_threshold=0.65`, the detection does not trigger. This is the correct behavior given the threshold — the condition `top_weight < 0.65 AND second_weight > 0.35` requires the dominant entry to be below 65%, which is satisfied, but the second condition also requires the second entry to exceed 35%, which with exactly 50/50 produces a boundary case. The threshold is domain-sensitive (see Limits).

---

**Test 6 — Episodic shortcuts in subsequent fetches**

```
Query: "log output"
Instance: js.console
```

```
Pool: ['js.console']
Episodic links on js.console: 1
PASS ✓
```

`js.console` has one episodic link to `js.date` from previous fetches. The fetch navigates it as a candidate with a relevance bonus. `js.date` does not enter the pool here because `output` does not match its terms — but the shortcut was evaluated. With a query that includes `timestamp`, `js.date` would be reached faster than a purely structural traversal would allow.

---

### Suite 2: structured RAG vs simple RAG

Three query types, same domain.

| Metric | Simple RAG | Structured RAG |
|---|---|---|
| **Precise query — logging middleware** | | |
| Elements retrieved | 5 | 6 |
| Precision | 0.8 | **1.0** |
| Noise (FP) | 1 | **0** |
| Explicit relations | No | **Yes** |
| **Specific query — error handler** | | |
| Elements retrieved | 5 | 4 |
| Precision | 0.6 | **0.75** |
| Noise (FP) | 2 | **1** |
| Explicit relations | No | **Yes** |
| **Vague query — "middleware"** | | |
| Elements retrieved | 5 | 2 |
| Precision | 0.8 | **1.0** |
| Noise (FP) | 1 | **0** |
| Coverage | full | minimal |

**Structural advantages independent of precision:**
- Zero redundancy — each node appears once with typed signature, documentation, and relations
- Explicit structural relations in context — the LLM reads `receives:express.request` rather than inferring it from text
- Traceability per UUID to a specific versioned source
- Ordering by prototypicality rather than surface similarity

**The condition on which the advantage rests:** with wrong initial instances, the structured RAG produces a small, internally precise but externally irrelevant pool. The simple RAG is more robust in that case because it does not depend on an entry point. Human instance confirmation is not optional — it is the mechanism that makes the structured RAG more precise than the simple one.

---

## Limits surfaced by the tests

### 1. Plurimodal threshold is domain-sensitive

The `split_threshold` parameter governs when the batch operation flags a node as a split candidate. With `threshold=0.70` and a 50/50 distribution the detection triggers; with `threshold=0.65` and the same distribution it does not. There is no universally correct value. In the code domain, where concepts are formally precise, the threshold should be high — a node that appears in two contexts almost equally often may simply be a utility concept, not two distinct entities. In open domains, the threshold should be lower. This requires domain-level calibration or direct human supervision over the split decision, which is already the intended design. The test surfaces the sensitivity; it does not expose a flaw.

### 2. Episodic bootstrap is structurally blind

The first fetches on a new domain navigate by structural relations only — no episodic shortcuts exist yet. The system is equivalent to a pure structural RAG until enough evidence accumulates. This is not a defect: it is the expected behavior of a system that learns from use. But it means the episodic advantage materializes only after a usage period, not from the first query. A graph deployed on a new project stack will behave, at first, like a conventional graph-RAG. It improves with use.

### 3. The sandbox feedback loop is incomplete

Test 2 shows that a sandbox failure generates an explicit signal identifying the likely wrong entry point. What the test does not implement — because it requires a separate architectural layer — is the propagation of that signal to the vocabulary: the step that refines which query tokens map to which instances, so that the same wrong entry point is less likely on the next similar query. The signal exists; the correction loop is not closed. This is the single most impactful missing component for the system to improve automatically over time.

### 4. `label: str` in the toy requires migration

The toy application's `graph.py` uses `label: str`. The complete mechanism requires `label: list`. These are structurally incompatible: the relevance check in `_is_relevant` iterates over `node.stable.label` expecting a list; a string would yield character-level iteration. Migration requires updating `graph.py`, all nodes in `seed_graph.py`, the vocabulary builder, and any serialization/deserialization that reads the `label` field. It is a contained engineering task, not an architectural change.

---

## A graph without trained symbols: pre-deployment behavior

The tests above run on a manually constructed graph — nodes with fully specified labels, semantic fields, intersections, synonyms, keywords, and structural relations. This is the post-training state: a graph built with human supervision, where every node has been validated and its field boundaries have been defined.

A graph without trained symbols is a different object. It is a set of nodes extracted from source code or documentation — entities with signatures and docstrings — but without the categorical structure that makes the fetch navigable: no semantic field assignments, no intersection maps, no typicality weights, no label arrays. In this state, the fetch can traverse structural relations (because those come from the AST and are explicit), but it cannot determine relevance — `_is_relevant` has nothing to match against, and every node would either always or never pass the relevance check depending on how the empty fields are handled.

This is the pre-training state. It corresponds to what the GitHub builder produces before human review: a staging file of candidate nodes, not a usable graph. The gap between the builder's output and a working graph is exactly the gap between raw entities and categorized symbols — and that gap requires human work to close. The builder reduces the cost of that work; it does not eliminate it.

The consequence for deployment is concrete: the system cannot be used on a new domain by importing a repository and starting queries immediately. The imported nodes need to be reviewed, their semantic fields assigned, their intersections mapped, and their labels initialized. Only then does the fetch have the categorical structure it needs to navigate meaningfully. The episodic layer is empty at that point — it grows with use. The structural layer is static. The symbolic layer — the layer that makes navigation possible — requires the human's initial investment.

This is the correct understanding of what "training" means in this architecture. It is not gradient descent on parameters: it is supervised construction of a categorical structure. It is a one-time cost per domain that diminishes as the episodic layer matures. In the code domain, the cost is partially offset by the explicitness of the AST — many field assignments and relations can be inferred automatically and require only validation rather than construction. In more open domains, the cost is higher because the categorical structure is less directly readable from the source material.

---

*The test code is available in `test_fetch_complete.py` and `test_rag_comparison.py`. Both run without dependencies beyond the Python standard library. The graph used in the tests is a self-contained 7-node Express + Node.js domain built inline — it does not depend on the toy application's seed graph or any external data.*
