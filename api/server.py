"""
api/server.py
-------------
Backend FastAPI — orchestratore principale del sistema.

Tutti gli endpoint che ricevono istanze lavorano con UUID internamente.
Verso l'esterno (UI) vengono esposti anche i node_id leggibili come alias.

Endpoint:
  GET  /status
  GET  /graph/nodes
  GET  /graph/node/{node_id}
  POST /query/translate         → restituisce uuid + node_id per la UI
  POST /query/fetch             → riceve uuid, restituisce pool + RAG
  POST /query/generate          → pipeline completa
  POST /graph/episodic/prune
  POST /graph/build/github      → staging
  POST /graph/build/commit      → import confermato
  GET  /graph/build/staging
  DELETE /graph/build/staging
  GET  /config
  POST /config
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import json

from core.graph import ConceptGraph, make_stable_layer
from core.vocabulary import Vocabulary
from core.fetch import ProgressiveFetch
from core.rag import RAGSerializer, LLMConnector
from core.sandbox import SandboxVerifier

# ──────────────────────────────────────────
# Inizializzazione
# ──────────────────────────────────────────

app = FastAPI(
    title="GrafoMente",
    description="RAG strutturato da grafo concettuale per la generazione di codice",
    version="0.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
GRAPH_PATH = os.path.join(BASE_DIR, "data/graph/graph.json")
VOCAB_PATH = os.path.join(BASE_DIR, "data/vocabulary/vocab.json")
CONFIG_PATH = os.path.join(BASE_DIR, "data/config.json")
STAGING_PATH = os.path.join(BASE_DIR, "data/graph/staging.json")

DEFAULT_CONFIG = {
    "llm": {
        "mode": "local",
        "url": "http://localhost:11434",
        "model": "deepseek-coder",
        "api_key": "",
        "max_tokens": 2048,
        "temperature": 0.2
    },
    "fetch": {"max_depth": 4, "max_rag_nodes": 15},
    "sandbox": {"enabled": True, "timeout": 10, "max_attempts": 3, "language": "python"}
}

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return DEFAULT_CONFIG

def save_config(config: dict):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)

# Carica componenti all'avvio
graph = ConceptGraph.load(GRAPH_PATH)
vocabulary = Vocabulary()
vocabulary.load(VOCAB_PATH)
if not vocabulary.all_terms():
    vocabulary.build_from_graph(graph)

# ──────────────────────────────────────────
# Modelli Pydantic
# ──────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str

class FetchRequest(BaseModel):
    query: str
    instance_uuids: list   # UUID confermati dall'utente

class GenerateRequest(BaseModel):
    query: str
    instance_uuids: list
    run_sandbox: bool = True
    sandbox_language: str = "javascript"

class PruneRequest(BaseModel):
    min_use_count: int = 2

class ConfigUpdateRequest(BaseModel):
    config: dict

# ──────────────────────────────────────────
# Helper
# ──────────────────────────────────────────

def node_to_summary(node) -> dict:
    """Riassunto di un nodo per liste e UI — espone sia uuid che node_id."""
    s = node.stable
    return {
        "uuid": s.uuid,
        "id": s.node_id,
        "label": s.label,
        "type": s.node_type,
        "field": s.semantic_field,
        "hierarchy": s.hierarchy_path,
        "signature": s.signature,
        "prototypicality": s.prototypicality,
        "episodic_links": len(node.episodic.links)
    }

def node_to_detail(node) -> dict:
    """Dettaglio completo di un nodo — layer stabile + layer episodico."""
    s = node.stable
    return {
        "stable": {
            "uuid": s.uuid,
            "id": s.node_id,
            "label": s.label,
            "type": s.node_type,
            "signature": s.signature,
            "doc": s.docstring,
            "source": s.source_ref,
            "hierarchy": s.hierarchy_path,
            "field": s.semantic_field,
            "intersections": s.intersections,
            "relations": [
                {
                    "type": r.get("type", ""),
                    "target_uuid": r.get("target_uuid", ""),
                    "target_id": graph.node_id_for(r.get("target_uuid", "")) or ""
                }
                for r in s.relations
            ],
            "prototypicality": s.prototypicality,
            "synonyms": s.synonyms,
            "keywords": s.keywords
        },
        "episodic": {
            "links": [
                {
                    "target_uuid": l.target_uuid,
                    "target_id": graph.node_id_for(l.target_uuid) or l.target_uuid,
                    "field": l.intersection_field,
                    "nature": l.link_nature,
                    "use_count": l.use_count
                }
                for l in node.episodic.links
            ]
        }
    }

# ──────────────────────────────────────────
# Endpoint
# ──────────────────────────────────────────

@app.get("/status")
def status():
    config = load_config()
    llm = LLMConnector(config["llm"])
    conn = llm.test_connection()
    return {
        "graph_nodes": len(graph.all_nodes()),
        "vocabulary_terms": len(vocabulary.all_terms()),
        "llm_connected": conn.get("ok", False),
        "llm_mode": config["llm"]["mode"],
        "llm_model": config["llm"]["model"],
        "llm_url": config["llm"]["url"],
        "connection_detail": conn
    }

@app.get("/graph/nodes")
def get_nodes(field: Optional[str] = None, limit: int = 200):
    nodes = graph.all_nodes()
    if field:
        nodes = [n for n in nodes if n.stable.semantic_field == field]
    return {
        "nodes": [node_to_summary(n) for n in nodes[:limit]],
        "total": len(graph.all_nodes())
    }

@app.get("/graph/node/{node_id}")
def get_node_detail(node_id: str):
    """Accesso per node_id (alias leggibile) — risolve UUID internamente."""
    node = graph.get_node_by_id(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Nodo '{node_id}' non trovato")
    return node_to_detail(node)

@app.get("/graph/node/uuid/{uuid}")
def get_node_detail_by_uuid(uuid: str):
    """Accesso diretto per UUID."""
    node = graph.get_node_by_uuid(uuid)
    if not node:
        raise HTTPException(status_code=404, detail=f"UUID '{uuid}' non trovato")
    return node_to_detail(node)

@app.post("/query/translate")
def translate_query(req: QueryRequest):
    """
    Traduce la query nelle istanze del grafo.
    Restituisce uuid + node_id per ogni istanza:
      - uuid: usato dal sistema per le operazioni successive
      - node_id: mostrato all'utente per la conferma
    Il fetch riceverà gli uuid, non i node_id.
    """
    instance_uuids = vocabulary.translate(req.query, graph)
    instances = []
    for uuid in instance_uuids:
        node = graph.get_node_by_uuid(uuid)
        if node:
            instances.append({
                "uuid": uuid,
                "id": node.stable.node_id,
                "label": node.stable.label,
                "field": node.stable.semantic_field,
                "signature": node.stable.signature
            })
    return {
        "query": req.query,
        "instances": instances,
        "message": "Conferma, aggiungi o rimuovi istanze prima di avviare il fetch."
    }

@app.post("/query/fetch")
def run_fetch(req: FetchRequest):
    """
    Fetch progressivo. Riceve uuid delle istanze confermate.
    Salva il layer episodico aggiornato dopo la navigazione.
    """
    if not req.instance_uuids:
        raise HTTPException(status_code=400, detail="Nessuna istanza fornita.")

    config = load_config()
    fetcher = ProgressiveFetch(graph, max_depth=config["fetch"]["max_depth"])
    result = fetcher.fetch(req.query, req.instance_uuids)
    graph.save(GRAPH_PATH)

    serializer = RAGSerializer()
    rag_text = serializer.serialize(result, max_nodes=config["fetch"]["max_rag_nodes"])

    # Per la UI: risolve gli uuid attivati in label leggibili
    activated_labels = []
    for uuid in result.activated_uuids:
        node = graph.get_node_by_uuid(uuid)
        if node:
            activated_labels.append(node.stable.node_id)

    return {
        "query": result.query,
        "activated_uuids": result.activated_uuids,
        "activated_labels": activated_labels,
        "pool_size": len(result.pool),
        "abandoned_paths": len(result.abandoned_paths),
        "new_episodic_links": len(result.new_episodic_links),
        "pool": result.pool,
        "rag_text": rag_text,
        "abandoned_detail": result.abandoned_paths[:20]
    }

@app.post("/query/generate")
def generate_code(req: GenerateRequest):
    """Pipeline completa: fetch → RAG → LLM → sandbox."""
    if not req.instance_uuids:
        raise HTTPException(status_code=400, detail="Nessuna istanza fornita.")

    config = load_config()

    # 1. Fetch
    fetcher = ProgressiveFetch(graph, max_depth=config["fetch"]["max_depth"])
    fetch_result = fetcher.fetch(req.query, req.instance_uuids)
    graph.save(GRAPH_PATH)

    # 2. RAG
    serializer = RAGSerializer()
    rag_text = serializer.serialize(fetch_result, max_nodes=config["fetch"]["max_rag_nodes"])

    # 3. LLM
    llm = LLMConnector(config["llm"])
    raw_output = llm.generate(rag_context=rag_text, query=req.query)

    # 4. Sandbox (opzionale)
    sandbox_result = None
    if req.run_sandbox and config["sandbox"]["enabled"]:
        verifier = SandboxVerifier(
            timeout=config["sandbox"]["timeout"],
            max_attempts=config["sandbox"]["max_attempts"]
        )
        code = verifier._extract_code(raw_output, req.sandbox_language) or raw_output
        result_obj = verifier.verify_and_correct(
            code=code, language=req.sandbox_language,
            query=req.query, rag_context=rag_text, llm_connector=llm
        )
        sandbox_result = {
            "success": result_obj.success,
            "output": result_obj.output,
            "error": result_obj.error,
            "attempts": result_obj.attempts,
            "final_code": result_obj.code
        }

    return {
        "query": req.query,
        "pool_size": len(fetch_result.pool),
        "rag_preview": rag_text[:600] + "..." if len(rag_text) > 600 else rag_text,
        "llm_output": raw_output,
        "sandbox": sandbox_result
    }

@app.post("/graph/episodic/prune")
def prune_episodic(req: PruneRequest):
    """Potatura layer episodico — operazione di manutenzione umana."""
    pruned_total = 0
    for node in graph.all_nodes():
        before = len(node.episodic.links)
        node.episodic.prune(req.min_use_count)
        pruned_total += before - len(node.episodic.links)
    graph.save(GRAPH_PATH)
    return {"pruned_links": pruned_total, "message": "Layer episodico potato."}

@app.post("/graph/build/github")
def build_from_github(req: dict):
    """
    Costruisce nodi dal repository GitHub.
    Fase 1 di 2: produce nodi in staging per revisione umana.
    I nodi in staging hanno già UUID e relazioni per UUID.
    Il commit nel grafo richiede conferma separata.
    """
    from core.github_builder import GitHubGraphBuilder

    owner = req.get("owner", "").strip()
    repo_name = req.get("repo", "").strip()
    if not owner or not repo_name:
        raise HTTPException(status_code=400, detail="'owner' e 'repo' sono obbligatori.")

    builder = GitHubGraphBuilder(
        token=req.get("token", ""),
        max_files=int(req.get("max_files", 100))
    )
    result = builder.build(
        owner=owner, repo=repo_name,
        ref=req.get("ref", ""),
        path_filter=req.get("path_filter", "")
    )

    if not result.nodes:
        return {"status": "empty", "stats": result.stats, "errors": result.errors, "preview": []}

    preview = [
        {
            "uuid": n.uuid,
            "id": n.node_id,
            "label": n.label,
            "type": n.node_type,
            "field": n.semantic_field,
            "signature": n.signature[:120],
            "hierarchy": n.hierarchy_path,
            "prototypicality": n.prototypicality,
        }
        for n in result.nodes[:50]
    ]

    # Salva in staging: serializza uuid + tutti i campi stabile
    staging_data = [
        {
            "uuid": n.uuid,
            "node_id": n.node_id, "label": n.label, "node_type": n.node_type,
            "signature": n.signature, "docstring": n.docstring,
            "source_ref": n.source_ref, "hierarchy_path": n.hierarchy_path,
            "semantic_field": n.semantic_field, "intersections": n.intersections,
            "relations": n.relations,  # già per target_uuid
            "prototypicality": n.prototypicality,
            "synonyms": n.synonyms, "keywords": n.keywords
        }
        for n in result.nodes
    ]
    os.makedirs(os.path.dirname(STAGING_PATH), exist_ok=True)
    with open(STAGING_PATH, "w") as f:
        json.dump(staging_data, f, indent=2)

    return {
        "status": "staged",
        "stats": result.stats,
        "errors": result.errors[:10],
        "preview": preview,
        "total_nodes": len(result.nodes),
        "message": f"{len(result.nodes)} nodi pronti. Usa /graph/build/commit per importarli."
    }

@app.post("/graph/build/commit")
def commit_staged_nodes(req: dict):
    """
    Importa i nodi in staging nel grafo.
    I nodi mantengono i loro UUID originali — non vengono rigenerate chiavi.
    Richiede {"confirm": true}.
    """
    if not req.get("confirm"):
        raise HTTPException(status_code=400, detail="Invia {'confirm': true} per procedere.")

    from core.github_builder import merge_into_graph

    if not os.path.exists(STAGING_PATH):
        raise HTTPException(status_code=404,
                            detail="Nessun dato in staging. Esegui prima /graph/build/github.")

    with open(STAGING_PATH) as f:
        staging_data = json.load(f)

    # Ricostruisce StableLayer conservando gli UUID dello staging
    from core.graph import StableLayer
    new_nodes = [StableLayer(**d) for d in staging_data]
    stats = merge_into_graph(graph, new_nodes, overwrite=req.get("overwrite", False))

    vocabulary.build_from_graph(graph)
    vocabulary.save(VOCAB_PATH)
    graph.save(GRAPH_PATH)
    os.remove(STAGING_PATH)

    return {
        "status": "committed",
        "merge_stats": stats,
        "total_nodes": len(graph.all_nodes()),
        "vocabulary_terms": len(vocabulary.all_terms()),
        "message": "Nodi importati. Vocabolario aggiornato."
    }

@app.get("/graph/build/staging")
def get_staging():
    if not os.path.exists(STAGING_PATH):
        return {"status": "empty", "nodes": []}
    with open(STAGING_PATH) as f:
        data = json.load(f)
    return {"status": "ready", "count": len(data), "nodes": data[:20]}

@app.delete("/graph/build/staging")
def clear_staging():
    if os.path.exists(STAGING_PATH):
        os.remove(STAGING_PATH)
    return {"status": "cleared"}

@app.get("/config")
def get_config():
    return load_config()

@app.post("/config")
def update_config(req: ConfigUpdateRequest):
    current = load_config()
    for key, val in req.config.items():
        if isinstance(val, dict) and key in current:
            current[key].update(val)
        else:
            current[key] = val
    save_config(current)
    return {"message": "Configurazione aggiornata.", "config": current}
