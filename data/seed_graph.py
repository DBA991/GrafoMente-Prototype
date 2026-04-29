"""
data/seed_graph.py
------------------
Grafo seed di esempio: stack Express + Node.js (12 nodi).
Dimostra la struttura completa — UUID generati da make_stable_layer(),
relazioni referenziate per UUID del nodo destinazione.

Eseguito da run.py al primo avvio o con --rebuild-graph.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.graph import ConceptGraph, make_stable_layer
from core.vocabulary import Vocabulary

GRAPH_PATH = os.path.join(os.path.dirname(__file__), "graph/graph.json")
VOCAB_PATH = os.path.join(os.path.dirname(__file__), "vocabulary/vocab.json")


def build() -> ConceptGraph:
    g = ConceptGraph()

    # ── Fase 1: crea tutti i nodi (UUID generati automaticamente) ──────────

    middleware = g.add_node(make_stable_layer(
        node_id="express.middleware",
        label="middleware",
        node_type="function",
        signature="(req: Request, res: Response, next: NextFunction) => void",
        docstring="Una funzione middleware ha accesso all'oggetto request (req), "
                  "all'oggetto response (res) e alla funzione next nel ciclo request-response.",
        source_ref="github.com/expressjs/express@4.18.2",
        hierarchy_path=["express", "middleware"],
        semantic_field="middleware",
        intersections=["request_handling", "routing"],
        relations=[],   # popolate dopo
        prototypicality=1.0,
        synonyms=["middleware", "handler", "filter"],
        keywords=["middleware", "request", "response", "next", "express"]
    ))

    app_node = g.add_node(make_stable_layer(
        node_id="express.app",
        label="app",
        node_type="object",
        signature="const app = express()",
        docstring="L'oggetto app Express. Espone metodi per il routing HTTP, "
                  "la configurazione del middleware e il rendering delle view.",
        source_ref="github.com/expressjs/express@4.18.2",
        hierarchy_path=["express"],
        semantic_field="http_server",
        intersections=["middleware", "routing"],
        relations=[],
        prototypicality=1.0,
        synonyms=["app", "express", "application", "server"],
        keywords=["express", "app", "server", "http", "application"]
    ))

    router = g.add_node(make_stable_layer(
        node_id="express.router",
        label="Router",
        node_type="class",
        signature="express.Router(options?: RouterOptions)",
        docstring="Crea un nuovo oggetto router. È un mini-app isolata, "
                  "capace di eseguire middleware e routing.",
        source_ref="github.com/expressjs/express@4.18.2",
        hierarchy_path=["express", "Router"],
        semantic_field="routing",
        intersections=["middleware"],
        relations=[],
        prototypicality=1.0,
        synonyms=["Router", "router", "route"],
        keywords=["router", "routing", "express", "route", "subroute"]
    ))

    req_node = g.add_node(make_stable_layer(
        node_id="express.request",
        label="Request",
        node_type="object",
        signature="req: express.Request",
        docstring="L'oggetto request di Express. Contiene proprietà per la richiesta HTTP: "
                  "req.params, req.query, req.body, req.headers, req.method, req.path.",
        source_ref="github.com/expressjs/express@4.18.2",
        hierarchy_path=["express", "Request"],
        semantic_field="request_handling",
        intersections=["middleware"],
        relations=[],
        prototypicality=1.0,
        synonyms=["req", "request", "Request"],
        keywords=["request", "req", "params", "query", "body", "headers", "method", "path"]
    ))

    res_node = g.add_node(make_stable_layer(
        node_id="express.response",
        label="Response",
        node_type="object",
        signature="res: express.Response",
        docstring="L'oggetto response di Express. Metodi principali: "
                  "res.send(), res.json(), res.status(), res.redirect(), res.render().",
        source_ref="github.com/expressjs/express@4.18.2",
        hierarchy_path=["express", "Response"],
        semantic_field="request_handling",
        intersections=["middleware"],
        relations=[],
        prototypicality=1.0,
        synonyms=["res", "response", "Response"],
        keywords=["response", "res", "send", "json", "status", "redirect"]
    ))

    next_fn = g.add_node(make_stable_layer(
        node_id="express.next",
        label="NextFunction",
        node_type="function",
        signature="next: NextFunction = (err?: any) => void",
        docstring="Passa il controllo al prossimo middleware nella pila. "
                  "Se chiamato con un argomento, passa il controllo al gestore degli errori.",
        source_ref="github.com/expressjs/express@4.18.2",
        hierarchy_path=["express", "NextFunction"],
        semantic_field="middleware",
        intersections=["error_handling"],
        relations=[],
        prototypicality=0.9,
        synonyms=["next", "NextFunction", "nextFunction"],
        keywords=["next", "chain", "middleware", "error", "pass"]
    ))

    date_node = g.add_node(make_stable_layer(
        node_id="js.date",
        label="Date",
        node_type="class",
        signature="new Date() | Date.now() | new Date(value)",
        docstring="Oggetto built-in JavaScript per date e ore. "
                  "Date.now() restituisce il timestamp Unix in ms. "
                  "toISOString() restituisce data in formato ISO 8601.",
        source_ref="developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date",
        hierarchy_path=["JavaScript", "Global", "Date"],
        semantic_field="datetime",
        intersections=["logging"],
        relations=[],
        prototypicality=1.0,
        synonyms=["Date", "date", "datetime", "timestamp"],
        keywords=["date", "time", "timestamp", "now", "iso", "datetime", "log"]
    ))

    console_node = g.add_node(make_stable_layer(
        node_id="js.console",
        label="console",
        node_type="object",
        signature="console.log(...args) | console.error(...args) | console.warn(...args)",
        docstring="Oggetto globale per output su standard output/error. "
                  "console.log per output standard, console.error per errori.",
        source_ref="developer.mozilla.org/en-US/docs/Web/API/console",
        hierarchy_path=["JavaScript", "Global", "console"],
        semantic_field="logging",
        intersections=["error_handling", "datetime"],
        relations=[],
        prototypicality=1.0,
        synonyms=["console", "log", "logger"],
        keywords=["console", "log", "error", "warn", "debug", "output", "print"]
    ))

    fs_node = g.add_node(make_stable_layer(
        node_id="node.fs",
        label="fs",
        node_type="object",
        signature="import fs from 'fs' | require('fs')",
        docstring="Modulo Node.js per l'accesso al filesystem. "
                  "Metodi principali: fs.readFile, fs.writeFile, fs.readFileSync, fs.writeFileSync.",
        source_ref="nodejs.org/api/fs.html",
        hierarchy_path=["Node.js", "fs"],
        semantic_field="utilities",
        intersections=["logging", "configuration"],
        relations=[],
        prototypicality=0.9,
        synonyms=["fs", "filesystem", "file"],
        keywords=["file", "fs", "read", "write", "path", "stream", "node"]
    ))

    path_node = g.add_node(make_stable_layer(
        node_id="node.path",
        label="path",
        node_type="object",
        signature="import path from 'path' | require('path')",
        docstring="Modulo Node.js per la manipolazione dei percorsi file. "
                  "path.join(), path.resolve(), path.dirname(), path.basename(), path.extname().",
        source_ref="nodejs.org/api/path.html",
        hierarchy_path=["Node.js", "path"],
        semantic_field="utilities",
        intersections=["configuration"],
        relations=[],
        prototypicality=0.9,
        synonyms=["path", "filepath", "directory"],
        keywords=["path", "join", "resolve", "dirname", "basename", "extname", "node"]
    ))

    error_handler = g.add_node(make_stable_layer(
        node_id="express.error_handler",
        label="errorHandler",
        node_type="function",
        signature="(err: Error, req: Request, res: Response, next: NextFunction) => void",
        docstring="Middleware di gestione degli errori Express. "
                  "Riceve 4 argomenti (err, req, res, next). "
                  "Express lo riconosce automaticamente come error handler.",
        source_ref="github.com/expressjs/express@4.18.2",
        hierarchy_path=["express", "middleware", "error"],
        semantic_field="error_handling",
        intersections=["middleware", "request_handling"],
        relations=[],
        prototypicality=1.0,
        synonyms=["errorHandler", "error_handler", "onError", "handleError"],
        keywords=["error", "handler", "middleware", "exception", "status", "500"]
    ))

    json_mw = g.add_node(make_stable_layer(
        node_id="express.json_middleware",
        label="express.json",
        node_type="function",
        signature="express.json(options?: OptionsJson)",
        docstring="Middleware built-in di Express per il parsing del body JSON. "
                  "Popola req.body con l'oggetto JavaScript parsato.",
        source_ref="github.com/expressjs/express@4.18.2",
        hierarchy_path=["express", "middleware", "body"],
        semantic_field="middleware",
        intersections=["request_handling"],
        relations=[],
        prototypicality=0.95,
        synonyms=["express.json", "json", "bodyParser", "body_parser"],
        keywords=["json", "body", "parser", "middleware", "request", "express"]
    ))

    # ── Fase 2: relazioni per UUID ──────────────────────────────────────────
    # Le relazioni usano target_uuid — mai node_id.

    middleware.stable.relations = [
        {"target_uuid": req_node.stable.uuid,  "type": "receives"},
        {"target_uuid": res_node.stable.uuid,  "type": "receives"},
        {"target_uuid": next_fn.stable.uuid,   "type": "calls"},
        {"target_uuid": app_node.stable.uuid,  "type": "part_of"},
    ]

    app_node.stable.relations = [
        {"target_uuid": middleware.stable.uuid, "type": "uses"},
        {"target_uuid": router.stable.uuid,     "type": "uses"},
    ]

    router.stable.relations = [
        {"target_uuid": middleware.stable.uuid, "type": "uses"},
        {"target_uuid": app_node.stable.uuid,   "type": "part_of"},
        {"target_uuid": req_node.stable.uuid,   "type": "receives"},
        {"target_uuid": res_node.stable.uuid,   "type": "receives"},
    ]

    req_node.stable.relations = [
        {"target_uuid": middleware.stable.uuid, "type": "passed_to"},
    ]

    res_node.stable.relations = [
        {"target_uuid": middleware.stable.uuid, "type": "passed_to"},
    ]

    next_fn.stable.relations = [
        {"target_uuid": middleware.stable.uuid,   "type": "part_of"},
        {"target_uuid": error_handler.stable.uuid,"type": "triggers"},
    ]

    date_node.stable.relations = [
        {"target_uuid": console_node.stable.uuid, "type": "related"},
    ]

    console_node.stable.relations = [
        {"target_uuid": date_node.stable.uuid,    "type": "related"},
        {"target_uuid": error_handler.stable.uuid,"type": "used_in"},
    ]

    fs_node.stable.relations = [
        {"target_uuid": path_node.stable.uuid,    "type": "related"},
    ]

    path_node.stable.relations = [
        {"target_uuid": fs_node.stable.uuid,      "type": "related"},
    ]

    error_handler.stable.relations = [
        {"target_uuid": middleware.stable.uuid,   "type": "extends"},
        {"target_uuid": next_fn.stable.uuid,      "type": "receives"},
        {"target_uuid": req_node.stable.uuid,     "type": "receives"},
        {"target_uuid": res_node.stable.uuid,     "type": "receives"},
    ]

    json_mw.stable.relations = [
        {"target_uuid": middleware.stable.uuid,   "type": "is_a"},
        {"target_uuid": req_node.stable.uuid,     "type": "modifies"},
    ]

    return g


if __name__ == "__main__":
    print("Costruzione grafo seed Express + Node.js...")
    g = build()

    os.makedirs(os.path.dirname(GRAPH_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(VOCAB_PATH), exist_ok=True)

    g.save(GRAPH_PATH)
    print(f"✓ Grafo salvato: {GRAPH_PATH} ({len(g.all_nodes())} nodi)")

    vocab = Vocabulary()
    vocab.build_from_graph(g)
    vocab.save(VOCAB_PATH)
    print(f"✓ Vocabolario salvato: {VOCAB_PATH} ({len(vocab.all_terms())} termini)")

    # Verifica: stampa UUID e node_id di ogni nodo
    print("\nNodi nel grafo:")
    for node in g.all_nodes():
        s = node.stable
        print(f"  {s.node_id:35s} uuid={s.uuid[:8]}...  relazioni={len(s.relations)}")
