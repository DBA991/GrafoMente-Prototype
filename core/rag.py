"""
core/rag.py
-----------
Serializzazione del pool di fetch in testo RAG strutturato per l'LLM.
LLMConnector: interfaccia verso modelli locali (Ollama) o API (OpenAI-compatible).
"""

from __future__ import annotations
import requests
from core.fetch import FetchResult


class RAGSerializer:
    """Converte il pool di FetchResult in prompt strutturato per l'LLM."""

    def serialize(self, result: FetchResult, max_nodes: int = 15) -> str:
        lines = [
            f"# Contesto RAG per: {result.query}",
            f"# Istanze attivate: {len(result.activated_uuids)} | "
            f"Nodi nel pool: {len(result.pool)}",
            ""
        ]
        for node in result.pool[:max_nodes]:
            lines.append(f"## {node['label']} ({node['type']})")
            lines.append(f"UUID: {node['uuid']}")
            lines.append(f"ID: {node['id']}")
            lines.append(f"Firma: {node['signature']}")
            if node.get("doc"):
                doc = node["doc"][:300] + ("..." if len(node["doc"]) > 300 else "")
                lines.append(f"Doc: {doc}")
            lines.append(f"Campo: {node['field']} | Gerarchia: {node['hierarchy']}")
            if node.get("relations"):
                rels = ", ".join(
                    f"{r['type']}:{r['target_id'] or r['target_uuid']}"
                    for r in node["relations"][:5]
                )
                lines.append(f"Relazioni: {rels}")
            if node.get("episodic_shortcuts"):
                shortcuts = ", ".join(
                    f"{s['target_id'] or s['target_uuid']} [{s['field']}] x{s['use_count']}"
                    for s in node["episodic_shortcuts"][:3]
                )
                lines.append(f"Scorciatoie episodiche: {shortcuts}")
            lines.append(f"Fonte: {node['source']}")
            lines.append("")

        if result.abandoned_paths:
            lines.append(f"# Percorsi valutati e scartati: {len(result.abandoned_paths)}")
            for p in result.abandoned_paths[:5]:
                lines.append(f"  - {p}")

        return "\n".join(lines)


class LLMConnector:
    """
    Interfaccia verso l'LLM configurato.
    Supporta Ollama (locale) e endpoint OpenAI-compatible.

    generate() accetta error_context: se presente, viene inserito nel prompt
    come contesto di correzione — è il meccanismo del loop sandbox.
    """

    def __init__(self, config: dict):
        self.config = config
        self.mode = config.get("mode", "local")
        self.url = config.get("url", "http://localhost:11434")
        self.model = config.get("model", "deepseek-coder")
        self.api_key = config.get("api_key", "")
        self.max_tokens = config.get("max_tokens", 2048)
        self.temperature = config.get("temperature", 0.2)

    def test_connection(self) -> dict:
        try:
            if self.mode == "local":
                r = requests.get(f"{self.url}/api/tags", timeout=3)
                models = [m["name"] for m in r.json().get("models", [])]
                return {"ok": True, "models": models}
            else:
                return {"ok": True, "note": "API mode — connessione non testata preventivamente"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def generate(self, rag_context: str, query: str, error_context: str = "") -> str:
        """
        Genera codice a partire dal RAG context e dalla query.
        error_context: se non vuoto, viene incluso nel prompt come errore
        da correggere — usato dal loop di correzione della sandbox.
        """
        prompt = self._build_prompt(rag_context, query, error_context)
        try:
            if self.mode == "local":
                return self._ollama_generate(prompt)
            else:
                return self._api_generate(prompt)
        except Exception as e:
            return f"[Errore LLM: {e}]"

    def _build_prompt(self, rag_context: str, query: str,
                      error_context: str = "") -> str:
        system = (
            "Sei un assistente specializzato nella generazione di codice.\n"
            "Ti viene fornito un contesto strutturato estratto da un grafo concettuale.\n"
            "Usa SOLO le API presenti nel contesto."
        )
        user = rag_context + "\n\n"
        if error_context:
            user += f"ERRORE PRECEDENTE (correggere):\n{error_context}\n\n"
        user += f"Richiesta: {query}\n\nCodice:"
        return f"{system}\n\n{user}"

    def _ollama_generate(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens
            }
        }
        r = requests.post(f"{self.url}/api/generate", json=payload, timeout=120)
        r.raise_for_status()
        return r.json().get("response", "")

    def _api_generate(self, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature
        }
        r = requests.post(f"{self.url}/v1/chat/completions",
                          headers=headers, json=payload, timeout=120)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
