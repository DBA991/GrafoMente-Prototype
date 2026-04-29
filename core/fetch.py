"""
core/fetch.py
-------------
Fetch progressivo sul grafo concettuale.

Tutte le operazioni interne usano UUID come identificatore primario.
I node_id leggibili appaiono solo nei path_label per log e UI.

Criterio di arresto DOPPIO:
  1. Esaurimento delle istanze iniziali
  2. Esaurimento di tutti i percorsi pertinenti a partire da quelle istanze
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from core.graph import ConceptGraph, Node


@dataclass
class FetchResult:
    query: str
    activated_uuids: list          # UUID delle istanze iniziali
    pool: list                     # nodi raccolti, ordinati per rilevanza (dicts)
    abandoned_paths: list          # percorsi valutati e scartati (path_label leggibili)
    new_episodic_links: list       # connessioni emerse durante il fetch


class ProgressiveFetch:
    """
    Navigazione del grafo per inclusione ed esclusione attiva.
    Opera interamente per UUID — i node_id appaiono solo nei log.
    """

    def __init__(self, graph: ConceptGraph, max_depth: int = 4):
        self.graph = graph
        self.max_depth = max_depth

    def fetch(self, query: str, instance_uuids: list) -> FetchResult:
        """
        Esegue il fetch progressivo a partire dagli UUID delle istanze.
        """
        pool_uuids: list = []
        visited: set = set()
        abandoned: list = []
        new_links: list = []

        query_tokens = self._tokenize(query)

        for uuid in instance_uuids:
            node = self.graph.get_node_by_uuid(uuid)
            if not node:
                continue
            if uuid not in pool_uuids:
                pool_uuids.append(uuid)
            visited.add(uuid)

            self._traverse(
                node_uuid=uuid,
                depth=0,
                query_tokens=query_tokens,
                instance_uuids=instance_uuids,
                pool=pool_uuids,
                visited=visited,
                abandoned=abandoned,
                new_links=new_links
            )

        ordered = self._rank_pool(pool_uuids, query_tokens, instance_uuids)

        pool_dicts = []
        for uuid in ordered:
            node = self.graph.get_node_by_uuid(uuid)
            if node:
                pool_dicts.append(self._node_to_dict(node))

        # Registra nuovi link episodici (per UUID)
        for link_info in new_links:
            self.graph.record_episodic_link(
                from_uuid=link_info["from_uuid"],
                to_uuid=link_info["to_uuid"],
                intersection_field=link_info["field"],
                nature=link_info["nature"],
                mediator_uuid=link_info.get("mediator_uuid")
            )

        return FetchResult(
            query=query,
            activated_uuids=instance_uuids,
            pool=pool_dicts,
            abandoned_paths=abandoned,
            new_episodic_links=new_links
        )

    def _traverse(self, node_uuid: str, depth: int, query_tokens: list,
                  instance_uuids: list, pool: list,
                  visited: set, abandoned: list, new_links: list):
        if depth >= self.max_depth:
            return

        node = self.graph.get_node_by_uuid(node_uuid)
        if not node:
            return

        candidates = self._get_candidates(node, instance_uuids)

        for candidate_uuid, relevance_score, link_source in candidates:
            if candidate_uuid in visited:
                continue

            candidate_node = self.graph.get_node_by_uuid(candidate_uuid)
            if not candidate_node:
                continue

            # Path label leggibile per log/UI (usa node_id come alias)
            path_label = (f"{node.stable.node_id} → "
                          f"{candidate_node.stable.node_id}")

            visited.add(candidate_uuid)

            if self._is_relevant(candidate_node, query_tokens):
                pool.append(candidate_uuid)

                # Connessione nuova emersa dal fetch → registra nel layer episodico
                if link_source == "structural_new":
                    new_links.append({
                        "from_uuid": node_uuid,
                        "to_uuid": candidate_uuid,
                        "field": candidate_node.stable.semantic_field,
                        "nature": "direct",
                        "mediator_uuid": None
                    })

                self._traverse(candidate_uuid, depth + 1, query_tokens,
                                instance_uuids, pool, visited, abandoned, new_links)
            else:
                abandoned.append(path_label)

    def _get_candidates(self, node: Node, instance_uuids: list) -> list:
        """
        Candidati per la traversata: relazioni strutturali (UUID) + link episodici (UUID).
        Formato: (uuid, score, source)
        """
        candidates = []
        seen = set()

        # Relazioni strutturali — referenziate per UUID
        for rel in node.stable.relations:
            tuuid = rel.get("target_uuid", "")
            if tuuid and tuuid not in seen:
                candidates.append((tuuid, 1.0, "structural"))
                seen.add(tuuid)

        # Link episodici come scorciatoie (bonus proporzionale a use_count)
        for elink in node.episodic.links:
            if elink.target_uuid not in seen:
                bonus = min(1.0 + elink.use_count * 0.1, 2.0)
                candidates.append((elink.target_uuid, bonus, "episodic_shortcut"))
                seen.add(elink.target_uuid)

        return candidates

    def _is_relevant(self, node: Node, query_tokens: list) -> bool:
        node_terms = set(
            [node.stable.label.lower(), node.stable.semantic_field.lower()] +
            [s.lower() for s in node.stable.synonyms] +
            [k.lower() for k in node.stable.keywords] +
            [i.lower() for i in node.stable.intersections] +
            [p.lower() for p in node.stable.hierarchy_path]
        )
        # Lookup esatto
        if set(query_tokens) & node_terms:
            return True
        # Fuzzy-prefix: un termine del nodo matcha un token della query
        # se il token inizia con il termine (con max 3 char di prefisso)
        for term in node_terms:
            if len(term) < 3:
                continue
            pat = re.compile(r'^.{0,3}' + re.escape(term) + r'.*$', re.IGNORECASE)
            if any(pat.match(tok) for tok in query_tokens):
                return True
        return False

    def _rank_pool(self, pool: list, query_tokens: list, instance_uuids: list) -> list:
        def score(uuid: str) -> float:
            if uuid in instance_uuids:
                return 999.0
            node = self.graph.get_node_by_uuid(uuid)
            if not node:
                return 0.0
            node_terms = set(
                [node.stable.label.lower()] +
                [s.lower() for s in node.stable.synonyms] +
                [k.lower() for k in node.stable.keywords]
            )
            return node.stable.prototypicality + len(set(query_tokens) & node_terms) * 0.5
        return sorted(pool, key=score, reverse=True)

    def _node_to_dict(self, node: Node) -> dict:
        """Serializza un nodo per il RAG. Espone sia UUID che node_id."""
        s = node.stable
        return {
            "uuid": s.uuid,
            "id": s.node_id,
            "label": s.label,
            "type": s.node_type,
            "signature": s.signature,
            "doc": s.docstring,
            "source": s.source_ref,
            "hierarchy": " > ".join(s.hierarchy_path),
            "field": s.semantic_field,
            "intersections": s.intersections,
            "relations": [
                {"type": r.get("type", ""), "target_uuid": r.get("target_uuid", ""),
                 "target_id": self.graph.node_id_for(r.get("target_uuid", "")) or ""}
                for r in s.relations
            ],
            "prototypicality": s.prototypicality,
            "episodic_shortcuts": [
                {
                    "target_uuid": l.target_uuid,
                    "target_id": self.graph.node_id_for(l.target_uuid) or l.target_uuid,
                    "field": l.intersection_field,
                    "use_count": l.use_count
                }
                for l in node.episodic.links
            ]
        }

    def _tokenize(self, text: str) -> list:
        import re
        stopwords = {
            "un", "una", "il", "la", "lo", "i", "gli", "le", "di", "del", "della",
            "per", "che", "con", "in", "su", "da", "a", "e", "o", "non", "mi",
            "ma", "se", "come", "scrivimi", "crea", "genera", "fai", "voglio",
            "write", "create", "make", "a", "the", "an", "with", "for", "that",
            "che", "uno", "una", "degli", "delle", "nei", "alle", "dalla"
        }
        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]*", text.lower())
        return [t for t in tokens if t not in stopwords and len(t) > 1]
