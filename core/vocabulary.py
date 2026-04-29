"""
core/vocabulary.py
------------------
Traduzione query → UUID delle istanze nel grafo.

La _term_map e la _field_map mappano termini → UUID (non node_id).
Il risultato di translate() è una lista di UUID pronti per il fetch.
I node_id leggibili vengono esposti dall'API per la UI, ma non sono
la chiave operativa del sistema.
"""

from __future__ import annotations
import json, os, re
from core.graph import ConceptGraph


class Vocabulary:
    """
    Mappa termini → UUID.
    Costruita a partire da synonyms e keywords del layer stabile di ogni nodo.

    Matching fuzzy-prefix (lookup passaggio 2):
      Per ogni termine del vocabolario viene precompilato il pattern
        ^.{0,3}<termine>.*$
      che viene applicato ai token della query.
      Questo consente di trovare 'logging', 'logga', 'loggare', 're-log'
      a partire dal termine 'log' nel vocabolario — il token della query
      deve contenere il termine con al massimo 3 caratteri di prefisso
      e qualsiasi suffisso.
      Il matching avviene termine→token (non token→termine):
      il vocabolario definisce i concetti, la query usa varianti.
    """

    def __init__(self):
        self._term_map: dict = {}        # termine → [uuid, ...]
        self._field_map: dict = {}       # campo semantico → [uuid, ...]
        self._fuzzy_patterns: list = []  # [(pattern, [uuid, ...]), ...]

    def build_from_graph(self, graph: ConceptGraph):
        """
        Costruisce gli indici a partire dal grafo.
        Ogni termine viene mappato all'UUID del nodo.
        I pattern fuzzy vengono precompilati per termine.
        """
        self._term_map = {}
        self._field_map = {}
        self._fuzzy_patterns = []

        for node in graph.all_nodes():
            s = node.stable
            uuid = s.uuid

            terms = (
                [s.node_id, s.label] +
                s.synonyms +
                s.keywords +
                [p.lower() for p in s.hierarchy_path]
            )
            for term in terms:
                t = term.lower().strip()
                if not t:
                    continue
                self._term_map.setdefault(t, [])
                if uuid not in self._term_map[t]:
                    self._term_map[t].append(uuid)

            field = s.semantic_field.lower()
            self._field_map.setdefault(field, [])
            if uuid not in self._field_map[field]:
                self._field_map[field].append(uuid)

        # Precompila pattern fuzzy per ogni termine
        # Pattern: ^.{0,3}<termine>.*$  applicato al token della query
        seen_patterns = {}
        for term, uuids in self._term_map.items():
            if len(term) < 3:   # termini troppo corti generano troppo rumore
                continue
            pat_str = r'^.{0,3}' + re.escape(term) + r'.*$'
            if pat_str not in seen_patterns:
                seen_patterns[pat_str] = {
                    "pattern": re.compile(pat_str, re.IGNORECASE),
                    "uuids": list(uuids)
                }
            else:
                for uuid in uuids:
                    if uuid not in seen_patterns[pat_str]["uuids"]:
                        seen_patterns[pat_str]["uuids"].append(uuid)

        self._fuzzy_patterns = [
            (entry["pattern"], entry["uuids"])
            for entry in seen_patterns.values()
        ]

    def translate(self, query: str, graph: ConceptGraph) -> list:
        """
        Traduce la query in una lista di UUID.
        Ordine di lookup: esatto → fuzzy-prefix → campo semantico.

        Fuzzy-prefix (passaggio 2):
          Per ogni token della query, viene testato contro i pattern
          precompilati dai termini del vocabolario.
          Un token matcha un termine se il termine compare nel token
          con al massimo 3 caratteri di prefisso e qualsiasi suffisso.
          Es: token='logga' matcha il termine 'log' (pattern ^.{0,3}log.*$).
              token='prelogging' matcha 'log' (pre = 3 char, logging = log+ging).
              token='catalog' NON matcha 'log' (cata = 4 char > 3).
        """
        tokens = self._tokenize(query)
        found_uuids = []

        # 1. Lookup esatto
        for token in tokens:
            if token in self._term_map:
                for uuid in self._term_map[token]:
                    if uuid not in found_uuids:
                        found_uuids.append(uuid)

        # 2. Fuzzy-prefix: pattern dal termine, applicato al token della query
        if not found_uuids:
            for token in tokens:
                for pattern, uuids in self._fuzzy_patterns:
                    if pattern.match(token):
                        for uuid in uuids:
                            if uuid not in found_uuids:
                                found_uuids.append(uuid)

        # 3. Espansione per campo semantico
        if not found_uuids:
            for token in tokens:
                for field, uuids in self._field_map.items():
                    if token in field:
                        for uuid in uuids:
                            if uuid not in found_uuids:
                                found_uuids.append(uuid)

        return found_uuids

    def all_terms(self) -> list:
        return list(self._term_map.keys())

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "term_map": self._term_map,
                "field_map": self._field_map
            }, f, indent=2)

    def load(self, path: str):
        if not os.path.exists(path):
            return
        with open(path) as f:
            data = json.load(f)
        self._term_map = data.get("term_map", {})
        self._field_map = data.get("field_map", {})
        # Ricostruisce i pattern fuzzy dal term_map caricato
        self._fuzzy_patterns = []
        seen = {}
        for term, uuids in self._term_map.items():
            if len(term) < 3:
                continue
            pat_str = r'^.{0,3}' + re.escape(term) + r'.*$'
            if pat_str not in seen:
                seen[pat_str] = {
                    "pattern": re.compile(pat_str, re.IGNORECASE),
                    "uuids": list(uuids)
                }
            else:
                for uuid in uuids:
                    if uuid not in seen[pat_str]["uuids"]:
                        seen[pat_str]["uuids"].append(uuid)
        self._fuzzy_patterns = [
            (e["pattern"], e["uuids"]) for e in seen.values()
        ]

    def _tokenize(self, text: str) -> list:
        stopwords = {
            "un", "una", "il", "la", "lo", "i", "gli", "le", "di", "del", "della",
            "per", "che", "con", "in", "su", "da", "a", "e", "o", "non", "mi",
            "ma", "se", "come", "scrivimi", "crea", "genera", "fai", "voglio",
            "write", "create", "make", "the", "an", "with", "for", "that",
            "uno", "degli", "delle", "nei", "alle", "dalla", "usando", "use"
        }
        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]*", text.lower())
        return [t for t in tokens if t not in stopwords and len(t) > 1]
