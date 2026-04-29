"""
core/graph.py
-------------
Struttura dati del grafo concettuale.

Ogni nodo ha due layer:
  - stabile:   costruito in addestramento, invariante in uso.
               L'UUID è la chiave primaria — mai modificabile, mai riassegnato.
               Ogni operazione interna usa UUID; node_id è un alias leggibile.
  - episodico: connessioni contestuali accumulate durante l'uso (append-only).
               Ogni EpisodicLink referenzia il nodo connesso per UUID.

Il grafo ha autorità sul layer stabile.
Il layer stabile ha autorità sul layer episodico.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import json, os, uuid as _uuid


# ──────────────────────────────────────────
# Strutture dati
# ──────────────────────────────────────────

@dataclass
class StableLayer:
    """
    Dati invarianti del nodo, definiti in addestramento con supervisione umana.
    Non vengono mai modificati durante l'uso.

    uuid    — identificatore univoco, generato alla creazione, mai modificato.
              Chiave primaria per tutte le operazioni interne ed esterne.
    node_id — alias leggibile (es. "express.middleware").
              Usato per vocabolari, log e interfaccia utente.
              Non è la chiave operativa: due nodi in grafi diversi possono
              avere lo stesso node_id ma avranno sempre UUID distinti.
    """
    uuid: str
    node_id: str
    label: str
    node_type: str
    signature: str
    docstring: str
    source_ref: str
    hierarchy_path: list
    semantic_field: str
    intersections: list
    relations: list          # [{target_uuid, type}] — referenziati per UUID
    prototypicality: float = 1.0
    synonyms: list = field(default_factory=list)
    keywords: list = field(default_factory=list)


def make_stable_layer(node_id: str, **kwargs) -> StableLayer:
    """
    Costruisce un StableLayer con UUID generato automaticamente.
    Unico punto di creazione di nuovi nodi.
    """
    return StableLayer(uuid=str(_uuid.uuid4()), node_id=node_id, **kwargs)


@dataclass
class EpisodicLink:
    """
    Singola connessione contestuale nel layer episodico.
    target_uuid    — UUID del nodo connesso (non il node_id).
                     Rimane valido anche se l'etichetta del target cambia.
    mediator_uuid  — UUID del nodo intermedio (se link mediato).
    """
    target_uuid: str
    intersection_field: str
    link_nature: str                      # "direct" | "mediated" | "external_bridge"
    mediator_uuid: Optional[str]
    use_count: int = 1


@dataclass
class EpisodicLayer:
    node_uuid: str
    links: list = field(default_factory=list)

    def add_link(self, target_uuid: str, intersection_field: str,
                 nature: str, mediator_uuid: str = None):
        """Aggiunge o rinforza un link episodico (per UUID)."""
        for link in self.links:
            if link.target_uuid == target_uuid and link.intersection_field == intersection_field:
                link.use_count += 1
                return
        self.links.append(EpisodicLink(
            target_uuid=target_uuid,
            intersection_field=intersection_field,
            link_nature=nature,
            mediator_uuid=mediator_uuid
        ))

    def prune(self, min_use_count: int = 2):
        self.links = [l for l in self.links if l.use_count >= min_use_count]


@dataclass
class Node:
    stable: StableLayer
    episodic: EpisodicLayer


# ──────────────────────────────────────────
# Grafo principale
# ──────────────────────────────────────────

class ConceptGraph:
    """
    Grafo concettuale con nodi prototipici.
    Autorità: grafo > layer stabile > layer episodico.

    _nodes_by_uuid   — dizionario principale: uuid → Node
    _uuid_by_node_id — indice di lookup: node_id → uuid (per vocabolario e UI)
    """

    def __init__(self):
        self._nodes_by_uuid: dict = {}
        self._uuid_by_node_id: dict = {}

    def add_node(self, stable: StableLayer) -> Node:
        node = Node(stable=stable, episodic=EpisodicLayer(node_uuid=stable.uuid))
        self._nodes_by_uuid[stable.uuid] = node
        self._uuid_by_node_id[stable.node_id] = stable.uuid
        return node

    # Accesso per UUID (chiave primaria)
    def get_node_by_uuid(self, uuid: str) -> Optional[Node]:
        return self._nodes_by_uuid.get(uuid)

    # Accesso per node_id (alias leggibile — risolve UUID internamente)
    def get_node_by_id(self, node_id: str) -> Optional[Node]:
        uuid = self._uuid_by_node_id.get(node_id)
        return self._nodes_by_uuid.get(uuid) if uuid else None

    def uuid_for(self, node_id: str) -> Optional[str]:
        return self._uuid_by_node_id.get(node_id)

    def node_id_for(self, uuid: str) -> Optional[str]:
        node = self._nodes_by_uuid.get(uuid)
        return node.stable.node_id if node else None

    def all_nodes(self) -> list:
        return list(self._nodes_by_uuid.values())

    def all_uuids(self) -> list:
        return list(self._nodes_by_uuid.keys())

    def get_related_by_uuid(self, uuid: str) -> list:
        node = self.get_node_by_uuid(uuid)
        if not node:
            return []
        related = []
        for rel in node.stable.relations:
            target = self.get_node_by_uuid(rel.get("target_uuid", ""))
            if target:
                related.append(target)
        return related

    def record_episodic_link(self, from_uuid: str, to_uuid: str,
                              intersection_field: str, nature: str,
                              mediator_uuid: str = None):
        node = self.get_node_by_uuid(from_uuid)
        if node:
            node.episodic.add_link(to_uuid, intersection_field, nature, mediator_uuid)

    def save(self, path: str):
        data = {}
        for uid, node in self._nodes_by_uuid.items():
            s = node.stable
            data[uid] = {
                "stable": {
                    "uuid": s.uuid, "node_id": s.node_id, "label": s.label,
                    "node_type": s.node_type, "signature": s.signature,
                    "docstring": s.docstring, "source_ref": s.source_ref,
                    "hierarchy_path": s.hierarchy_path, "semantic_field": s.semantic_field,
                    "intersections": s.intersections, "relations": s.relations,
                    "prototypicality": s.prototypicality,
                    "synonyms": s.synonyms, "keywords": s.keywords
                },
                "episodic": {
                    "node_uuid": node.episodic.node_uuid,
                    "links": [
                        {"target_uuid": l.target_uuid,
                         "intersection_field": l.intersection_field,
                         "link_nature": l.link_nature,
                         "mediator_uuid": l.mediator_uuid,
                         "use_count": l.use_count}
                        for l in node.episodic.links
                    ]
                }
            }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: str) -> "ConceptGraph":
        """
        Carica il grafo da disco.
        Supporta il formato nuovo (chiave = UUID) e il formato legacy
        (senza uuid nel payload) per retrocompatibilità.
        """
        g = cls()
        if not os.path.exists(path):
            return g
        with open(path) as f:
            data = json.load(f)

        for key, entry in data.items():
            s = entry["stable"]
            if "uuid" not in s:
                s["uuid"] = str(_uuid.uuid4())

            stable = StableLayer(**s)
            node = Node(stable=stable, episodic=EpisodicLayer(node_uuid=stable.uuid))

            for l in entry["episodic"].get("links", []):
                # Retrocompatibilità: vecchio formato usava target_node_id
                if "target_node_id" in l:
                    l["target_uuid"] = l.pop("target_node_id")
                if "mediator_id" in l:
                    l["mediator_uuid"] = l.pop("mediator_id")
                node.episodic.links.append(EpisodicLink(
                    target_uuid=l.get("target_uuid", ""),
                    intersection_field=l.get("intersection_field", ""),
                    link_nature=l.get("link_nature", "direct"),
                    mediator_uuid=l.get("mediator_uuid"),
                    use_count=l.get("use_count", 1)
                ))

            g._nodes_by_uuid[stable.uuid] = node
            g._uuid_by_node_id[stable.node_id] = stable.uuid

        return g
