"""
core/github_builder.py
----------------------
Costruisce nodi StableLayer a partire da un repository GitHub,
usando l'API REST ufficiale (no scraping DOM).

Pipeline:
  1. Recupera l'albero del repository (ricorsivo) via /git/trees
  2. Filtra i file sorgente per estensione
  3. Per ogni file: scarica il contenuto raw, estrae entità con regex
  4. Categorizza ogni entità in base al percorso + contenuto
  5. Produce nodi StableLayer compatibili con ConceptGraph
     — ogni nodo ha UUID generato da make_stable_layer()
     — node_id è l'alias leggibile (repo.path.nome)
  6. Inferisce relazioni strutturali tra i nodi per UUID

Autenticazione: token GitHub opzionale ma consigliato
(senza: 60 req/h; con token: 5000 req/h).
"""

from __future__ import annotations
import re, os, time, logging
from dataclasses import dataclass, field
import requests

from core.graph import StableLayer, make_stable_layer

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".py", ".jsx", ".md"}
IGNORED_EXTENSIONS = {
    ".json", ".txt", ".lock", ".yaml", ".yml",
    ".css", ".scss", ".less", ".html", ".svg", ".png",
    ".jpg", ".gif", ".ico", ".woff", ".woff2", ".ttf",
    ".map", ".d.ts",
}
IGNORED_DIRS = {
    "node_modules", ".git", "dist", "build", "coverage",
    "__pycache__", ".pytest_cache", "vendor", "tmp", ".next",
    "out", ".turbo", "storybook-static",
}

PATH_TO_FIELD: list = [
    ("middleware",    "middleware"),
    ("router",        "routing"),
    ("route",         "routing"),
    ("controller",    "routing"),
    ("handler",       "request_handling"),
    ("request",       "request_handling"),
    ("response",      "request_handling"),
    ("auth",          "authentication"),
    ("login",         "authentication"),
    ("session",       "authentication"),
    ("token",         "authentication"),
    ("log",           "logging"),
    ("logger",        "logging"),
    ("error",         "error_handling"),
    ("exception",     "error_handling"),
    ("database",      "database"),
    ("db",            "database"),
    ("model",         "database"),
    ("schema",        "database"),
    ("config",        "configuration"),
    ("settings",      "configuration"),
    ("env",           "configuration"),
    ("util",          "utilities"),
    ("helper",        "utilities"),
    ("common",        "utilities"),
    ("hook",          "state_management"),
    ("store",         "state_management"),
    ("context",       "state_management"),
    ("component",     "ui_components"),
    ("view",          "ui_components"),
    ("page",          "ui_components"),
    ("test",          "testing"),
    ("spec",          "testing"),
    ("service",       "services"),
    ("api",           "api_layer"),
    ("client",        "api_layer"),
    ("server",        "http_server"),
    ("app",           "http_server"),
]

JS_PATTERNS = {
    "exported_function": re.compile(
        r"export\s+(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)",
        re.MULTILINE
    ),
    "exported_arrow": re.compile(
        r"export\s+const\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*(?::[^=]+)?\s*=>",
        re.MULTILINE
    ),
    "exported_class": re.compile(
        r"export\s+(?:default\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?",
        re.MULTILINE
    ),
    "module_exports_fn": re.compile(
        r"module\.exports\s*=\s*(?:async\s+)?function\s*(\w*)\s*\(([^)]*)\)",
        re.MULTILINE
    ),
    "jsdoc": re.compile(
        r"/\*\*\s*(.*?)\s*\*/\s*(?:export\s+)?(?:async\s+)?(?:function|class|const)\s+(\w+)",
        re.DOTALL
    ),
    "ts_interface": re.compile(
        r"export\s+interface\s+(\w+)(?:\s+extends\s+[\w,\s]+)?\s*\{([^}]{0,400})\}",
        re.DOTALL
    ),
}

PY_PATTERNS = {
    "def": re.compile(
        r"^(?:async\s+)?def\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*([^\n:]+))?:",
        re.MULTILINE
    ),
    "class": re.compile(
        r"^class\s+(\w+)(?:\(([^)]*)\))?:",
        re.MULTILINE
    ),
}


@dataclass
class RawEntity:
    name: str
    entity_type: str
    signature: str
    docstring: str
    file_path: str
    source_ref: str
    language: str
    is_exported: bool = True


class GitHubClient:
    BASE = "https://api.github.com"

    def __init__(self, token: str = ""):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def get(self, path: str, params: dict = None):
        url = self.BASE + path
        for attempt in range(3):
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 403:
                reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(reset - time.time(), 1)
                logger.warning(f"Rate limit. Attendo {wait:.0f}s...")
                time.sleep(min(wait, 60))
                continue
            if resp.status_code == 404:
                raise ValueError(f"Non trovato: {url}")
            resp.raise_for_status()
        raise RuntimeError(f"Impossibile raggiungere {url} dopo 3 tentativi")

    def get_raw(self, url: str) -> str:
        for _ in range(3):
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 403:
                time.sleep(5)
                continue
            if resp.status_code == 404:
                return ""
            resp.raise_for_status()
        return ""

    def get_repo_info(self, owner: str, repo: str) -> dict:
        return self.get(f"/repos/{owner}/{repo}")

    def get_tree(self, owner: str, repo: str, sha: str = "HEAD") -> list:
        data = self.get(f"/repos/{owner}/{repo}/git/trees/{sha}", params={"recursive": "1"})
        return data.get("tree", [])

    def get_file_content(self, owner: str, repo: str, path: str, ref: str) -> str:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
        return self.get_raw(url)


MD_IGNORED_FILES = {
    "changelog", "changes", "contributing", "license", "licence",
    "code_of_conduct", "security", "authors", "contributors",
    "todo", "roadmap", "funding", "codeowners",
}

MD_SKIP_HEADINGS = {
    "table of contents", "contents", "toc", "installation", "changelog",
    "license", "contributing", "credits", "acknowledgements",
}


class MarkdownExtractor:
    """
    Estrae sezioni da file .md come entità di tipo 'doc'.

    Strategia:
      - File con H2 → un nodo per sezione H2
      - File con solo H1 → un nodo per file
      - File di infrastruttura (CHANGELOG, LICENSE, ecc.) → ignorati

    name      = titolo sezione in snake_case
    signature = titolo originale + percorso
    docstring = contenuto condensato (testo + max 3 blocchi di codice)
    """

    def extract(self, content: str, file_path: str, source_ref: str) -> list:
        basename = os.path.splitext(os.path.basename(file_path))[0].lower()
        if basename in MD_IGNORED_FILES or basename.startswith("."):
            return []

        sections = self._split_sections(content)
        entities = []
        for title, body in sections:
            if not title or title.lower() in MD_SKIP_HEADINGS:
                continue
            if len(body.strip()) < 30:
                continue
            docstring = self._condense(body)
            signature = f"{title}  [{file_path}]"
            entities.append(RawEntity(
                name=self._title_to_name(title),
                entity_type="doc",
                signature=signature,
                docstring=docstring,
                file_path=file_path,
                source_ref=source_ref,
                language="md",
                is_exported=True
            ))
        return entities

    def _split_sections(self, content: str) -> list:
        lines = content.split("\n")
        h1 = next((l.lstrip("# ").strip() for l in lines
                   if l.startswith("# ") and not l.startswith("## ")), "")
        h2_indices = [i for i, l in enumerate(lines) if l.startswith("## ")]

        if not h2_indices:
            return [(h1, "\n".join(lines))] if h1 else []

        sections = []
        if h2_indices[0] > 0:
            intro = "\n".join(lines[:h2_indices[0]])
            if intro.strip():
                sections.append((h1 or "Introduction", intro))
        for i, idx in enumerate(h2_indices):
            title = lines[idx].lstrip("# ").strip()
            end = h2_indices[i + 1] if i + 1 < len(h2_indices) else len(lines)
            body = "\n".join(lines[idx + 1:end])
            sections.append((title, body))
        return sections

    def _condense(self, body: str, max_chars: int = 800) -> str:
        lines = body.split("\n")
        result = []
        in_code = False
        code_buf = []
        code_count = 0
        for line in lines:
            if line.strip().startswith("```"):
                if in_code:
                    if code_count < 3:
                        clean = [l for l in code_buf
                                 if l.strip() and not l.strip().startswith("//")
                                 and not l.strip().startswith("#")][:6]
                        if clean:
                            result.append("```\n" + "\n".join(clean) + "\n```")
                            code_count += 1
                    code_buf = []
                    in_code = False
                else:
                    in_code = True
                continue
            if in_code:
                code_buf.append(line)
                continue
            stripped = line.strip()
            if (stripped.startswith("![") or stripped.startswith("<") or
                    stripped.startswith("---") or stripped.startswith(":::") or
                    (stripped.startswith("[") and stripped.endswith(")")
                     and "http" in stripped)):
                continue
            if stripped:
                result.append(line)
        return "\n".join(result)[:max_chars]

    def _title_to_name(self, title: str) -> str:
        name = re.sub(r"[^\w\s]", "", title.lower())
        name = re.sub(r"\s+", "_", name.strip())
        return name[:60] or "section"


class DocCategorizer:
    """
    Categorizza entità 'doc' per contenuto testuale invece che per percorso.
    Restituisce (field, intersections, keywords, synonyms).
    """

    TOPIC_MAP = [
        # regexp prima di tutto: termini altamente specifici
        (["regex", "regexp", "regular expression", "pattern", "match", "capture",
          "lookahead", "lookbehind", "quantifier", "anchor", "flag", "group",
          "character class", "boundary", "alternation", "backreference",
          r"\d", r"\w", r"\s", r"\b", "greedy", "lazy", "sticky"],
         "regexp"),
        (["install", "setup", "getting started", "quickstart", "prerequisite"],
         "setup"),
        (["api", "endpoint", "route", "rest", "http"],
         "api_layer"),
        (["auth", "authentication", "authorization", "token", "jwt", "oauth", "login",
          "permission", "role", "session", "security"],
         "authentication"),
        (["config", "configuration", "setting", "environment", "env"],
         "configuration"),
        (["database", "db", "query", "schema", "model", "migration", "orm", "sql",
          "postgres", "mysql", "mongo"],
         "database"),
        (["error", "exception", "debug", "troubleshoot", "issue"],
         "error_handling"),
        (["log", "logging", "monitor", "trace", "metric"],
         "logging"),
        (["test", "testing", "spec", "unit", "integration", "mock"],
         "testing"),
        (["deploy", "docker", "container", "ci", "cd", "pipeline", "production"],
         "deployment"),
        (["middleware", "plugin", "hook", "extension", "interceptor"],
         "middleware"),
        (["component", "ui", "interface", "render", "view", "template"],
         "ui_components"),
        (["class", "function", "module", "export", "import", "syntax", "type"],
         "code_reference"),
        (["example", "tutorial", "guide", "walkthrough", "howto", "introduction",
          "overview", "step"],
         "documentation"),
    ]

    def categorize_doc(self, entity: RawEntity) -> tuple:
        combined = (entity.name + " " + entity.signature +
                    " " + entity.docstring).lower()
        field = "documentation"
        for keywords, candidate in self.TOPIC_MAP:
            if any(kw in combined for kw in keywords):
                field = candidate
                break
        intersections = []
        for keywords, candidate in self.TOPIC_MAP:
            if candidate != field and any(kw in combined for kw in keywords):
                intersections.append(candidate)
        intersections = list(dict.fromkeys(intersections))[:4]

        stopwords = {"this", "that", "with", "from", "have", "will", "when",
                     "then", "also", "some", "more", "like", "used", "using",
                     "which", "where", "what", "there", "their", "been"}
        title_words = re.findall(r"[a-zA-Z]{3,}", entity.name.replace("_", " "))
        body_words = re.findall(r"\b[a-zA-Z]{4,}\b", entity.docstring.lower())
        kw = [w.lower() for w in title_words if w.lower() not in stopwords]
        kw += [w for w in body_words[:30] if w not in stopwords and w not in kw]
        keywords = list(dict.fromkeys(kw))[:15]

        title_clean = entity.name.replace("_", " ")
        synonyms = list({
            title_clean,
            title_clean.lower(),
            entity.signature.split("[")[0].strip(),
        })
        return field, intersections, keywords, synonyms


class EntityExtractor:
    def extract(self, content: str, file_path: str,
                source_ref: str, language: str) -> list:
        if language == "md":
            return MarkdownExtractor().extract(content, file_path, source_ref)
        if language in ("js", "ts", "jsx", "tsx"):
            return self._extract_js(content, file_path, source_ref, language)
        if language == "py":
            return self._extract_py(content, file_path, source_ref)
        return []

    def _extract_js(self, content, path, ref, lang) -> list:
        entities = []
        docs = {}
        for m in JS_PATTERNS["jsdoc"].finditer(content):
            doc_text = re.sub(r"\s*\*\s*", " ", m.group(1)).strip()
            docs[m.group(2)] = doc_text

        for m in JS_PATTERNS["exported_function"].finditer(content):
            name = m.group(1)
            entities.append(RawEntity(name=name, entity_type="function",
                signature=f"function {name}({m.group(2).strip()})",
                docstring=docs.get(name, ""), file_path=path,
                source_ref=ref, language=lang))

        for m in JS_PATTERNS["exported_arrow"].finditer(content):
            name = m.group(1)
            entities.append(RawEntity(name=name, entity_type="function",
                signature=f"const {name} = ({m.group(2).strip()}) => ...",
                docstring=docs.get(name, ""), file_path=path,
                source_ref=ref, language=lang))

        for m in JS_PATTERNS["exported_class"].finditer(content):
            name = m.group(1)
            ext = m.group(2) or ""
            sig = f"class {name}" + (f" extends {ext}" if ext else "")
            entities.append(RawEntity(name=name, entity_type="class",
                signature=sig, docstring=docs.get(name, ""),
                file_path=path, source_ref=ref, language=lang))

        for m in JS_PATTERNS["module_exports_fn"].finditer(content):
            name = m.group(1) or os.path.splitext(os.path.basename(path))[0]
            entities.append(RawEntity(name=name, entity_type="function",
                signature=f"module.exports = function {name}({m.group(2).strip()})",
                docstring=docs.get(name, ""), file_path=path,
                source_ref=ref, language=lang))

        for m in JS_PATTERNS["ts_interface"].finditer(content):
            name = m.group(1)
            entities.append(RawEntity(name=name, entity_type="interface",
                signature=f"interface {name} {{ {m.group(2).strip()[:150]} }}",
                docstring=docs.get(name, ""), file_path=path,
                source_ref=ref, language=lang))

        return entities

    def _extract_py(self, content, path, ref) -> list:
        entities = []
        for m in PY_PATTERNS["def"].finditer(content):
            name = m.group(1)
            if name.startswith("_"):
                continue
            ret = m.group(3).strip() if m.group(3) else ""
            sig = f"def {name}({m.group(2).strip()})" + (f" -> {ret}" if ret else "")
            doc_m = re.match(r'\s*"""(.*?)"""', content[m.end():], re.DOTALL)
            doc = doc_m.group(1).strip()[:300] if doc_m else ""
            entities.append(RawEntity(name=name, entity_type="function",
                signature=sig, docstring=doc, file_path=path,
                source_ref=ref, language="py"))

        for m in PY_PATTERNS["class"].finditer(content):
            name = m.group(1)
            if name.startswith("_"):
                continue
            base = m.group(2) or ""
            sig = f"class {name}" + (f"({base})" if base else "")
            doc_m = re.match(r'\s*"""(.*?)"""', content[m.end():], re.DOTALL)
            doc = doc_m.group(1).strip()[:300] if doc_m else ""
            entities.append(RawEntity(name=name, entity_type="class",
                signature=sig, docstring=doc, file_path=path,
                source_ref=ref, language="py"))

        return entities


class Categorizer:
    def categorize(self, entity: RawEntity) -> tuple:
        combined = (entity.file_path + " " + entity.name + " " + entity.docstring).lower()
        field = "utilities"
        for pattern, candidate in PATH_TO_FIELD:
            if pattern in entity.file_path.lower():
                field = candidate
                break
        if field == "utilities":
            for pattern, candidate in PATH_TO_FIELD:
                if pattern in entity.name.lower():
                    field = candidate
                    break
        intersections = []
        for _, candidate in PATH_TO_FIELD:
            if candidate != field and candidate.replace("_", " ") in combined:
                intersections.append(candidate)
        intersections = list(set(intersections))[:5]
        keywords = self._extract_keywords(entity)
        return field, intersections, keywords

    def _extract_keywords(self, entity: RawEntity) -> list:
        words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+",
                           entity.name + " " + entity.signature)
        words = [w.lower() for w in words if len(w) > 2]
        stopwords = {"the", "and", "for", "with", "this", "that", "from",
                     "are", "was", "has", "have", "can", "will", "not", "use"}
        doc_words = [w for w in re.findall(r"\b[a-z]{3,}\b", entity.docstring.lower())[:20]
                     if w not in stopwords]
        return list(dict.fromkeys(words + doc_words))[:15]


@dataclass
class BuildResult:
    nodes: list
    stats: dict
    errors: list


class GitHubGraphBuilder:
    """
    Costruisce nodi StableLayer da un repository GitHub.
    Ogni nodo ha:
      - uuid: generato da make_stable_layer() alla creazione
      - node_id: alias leggibile derivato da repo + percorso + nome entità
    Le relazioni strutturali referenziano il nodo destinazione per uuid,
    non per node_id.
    """

    def __init__(self, token: str = "", max_files: int = 100):
        self.client = GitHubClient(token)
        self.extractor = EntityExtractor()
        self.categorizer = Categorizer()
        self.max_files = max_files

    def build(self, owner: str, repo: str, ref: str = "",
              path_filter: str = "", progress_callback=None) -> BuildResult:
        errors = []
        stats = {"files_scanned": 0, "files_skipped": 0,
                 "entities_found": 0, "nodes_created": 0}

        def log(msg):
            logger.info(msg)
            if progress_callback:
                progress_callback(msg)

        log(f"Recupero info repo: {owner}/{repo}")
        try:
            repo_info = self.client.get_repo_info(owner, repo)
        except Exception as e:
            return BuildResult(nodes=[], stats=stats, errors=[str(e)])

        if not ref:
            ref = repo_info.get("default_branch", "main")
        source_base = f"github.com/{owner}/{repo}@{ref}"
        repo_name = repo_info.get("name", repo)
        log(f"Branch: {ref}")

        log("Recupero albero file...")
        try:
            tree = self.client.get_tree(owner, repo, ref)
        except Exception as e:
            return BuildResult(nodes=[], stats=stats, errors=[str(e)])

        source_files = []
        for item in tree:
            if item.get("type") != "blob":
                continue
            path = item.get("path", "")
            parts = path.split("/")
            if any(p in IGNORED_DIRS for p in parts[:-1]):
                continue
            if path_filter and not path.startswith(path_filter):
                continue
            _, ext = os.path.splitext(path)
            if ext in IGNORED_EXTENSIONS or ext not in SUPPORTED_EXTENSIONS:
                continue
            if any(t in path.lower() for t in ["__test__", ".test.", ".spec.", "_test"]):
                stats["files_skipped"] += 1
                continue
            source_files.append(path)

        if len(source_files) > self.max_files:
            log(f"Limitato a {self.max_files} file su {len(source_files)}")
            priority = [f for f in source_files
                        if f.count("/") <= 1 or f.startswith(("src/", "lib/", "index"))]
            rest = [f for f in source_files if f not in priority]
            source_files = (priority + rest)[:self.max_files]

        log(f"File da analizzare: {len(source_files)}")

        all_entities = []
        for i, file_path in enumerate(source_files):
            log(f"[{i+1}/{len(source_files)}] {file_path}")
            try:
                content = self.client.get_file_content(owner, repo, file_path, ref)
                if not content:
                    stats["files_skipped"] += 1
                    continue
                _, ext = os.path.splitext(file_path)
                lang = ext.lstrip(".")
                if lang in ("mjs", "cjs"):
                    lang = "js"
                entities = self.extractor.extract(
                    content, file_path, f"{source_base}/{file_path}", lang)
                all_entities.extend(entities)
                stats["files_scanned"] += 1
                stats["entities_found"] += len(entities)
                time.sleep(0.1)
            except Exception as e:
                errors.append(f"{file_path}: {e}")
                stats["files_skipped"] += 1

        log(f"Entità estratte: {stats['entities_found']}")

        # Fase 1: crea nodi con UUID (senza relazioni ancora)
        nodes = self._entities_to_nodes(all_entities, repo_name, source_base)
        stats["nodes_created"] = len(nodes)

        # Fase 2: inferisce relazioni usando UUID (non node_id)
        nodes = self._infer_relations(nodes)

        log(f"Nodi creati: {stats['nodes_created']}")
        return BuildResult(nodes=nodes, stats=stats, errors=errors)

    def _entities_to_nodes(self, entities: list, repo_name: str,
                           source_base: str) -> list:
        nodes = []
        seen_ids = set()
        doc_categorizer = DocCategorizer()

        for ent in entities:

            # ── Branch doc: entità estratte da file .md ──────────────────────
            if ent.entity_type == "doc":
                # node_id: repo.docs.percorso_file.nome_sezione
                path_parts = ent.file_path.replace("\\", "/").split("/")
                path_parts[-1] = os.path.splitext(path_parts[-1])[0]
                clean_parts = [p for p in path_parts
                               if p not in (".", "") and p.lower() not in
                               ("docs", "doc", "documentation", "readme", "article")]
                node_id = ".".join(
                    [repo_name.lower().replace("-", "_")] +
                    [p.lower().replace("-", "_") for p in clean_parts] +
                    [ent.name]
                )
                node_id = re.sub(r"\.{2,}", ".", node_id)
                if node_id in seen_ids:
                    continue
                seen_ids.add(node_id)

                field, intersections, keywords, synonyms = \
                    doc_categorizer.categorize_doc(ent)

                hierarchy = (
                    [repo_name] +
                    [p for p in clean_parts] +
                    [ent.name.replace("_", " ").title()]
                )

                node = make_stable_layer(
                    node_id=node_id,
                    label=ent.name.replace("_", " ").title(),
                    node_type="doc",
                    signature=ent.signature,
                    docstring=ent.docstring,
                    source_ref=ent.source_ref,
                    hierarchy_path=hierarchy,
                    semantic_field=field,
                    intersections=intersections,
                    relations=[],
                    prototypicality=0.9,
                    synonyms=synonyms,
                    keywords=keywords
                )
                nodes.append(node)
                continue

            # ── Branch code: entità estratte da file sorgente ────────────────
            path_parts = ent.file_path.replace("\\", "/").split("/")
            path_parts[-1] = os.path.splitext(path_parts[-1])[0]
            clean_parts = [p for p in path_parts
                           if p not in ("src", "lib", "index", "main", ".", "")]
            node_id_parts = ([repo_name.lower().replace("-", "_")] +
                             clean_parts + [ent.name.lower()])
            node_id = ".".join(node_id_parts)
            node_id = re.sub(r"\b(\w+)\.(\1)\b", r"\1", node_id)
            if node_id in seen_ids:
                continue
            seen_ids.add(node_id)

            hierarchy = [repo_name] + [p.replace("-", "_") for p in clean_parts]
            field, intersections, keywords = self.categorizer.categorize(ent)

            proto = 1.0
            if ent.entity_type in ("interface", "type"):
                proto = 0.7
            if "test" in ent.file_path.lower():
                proto = 0.5
            if len(clean_parts) > 3:
                proto *= 0.9

            synonyms = list({
                ent.name,
                ent.name.lower(),
                re.sub(r"([A-Z])", r"_\1", ent.name).lower().lstrip("_"),
            })

            # make_stable_layer genera UUID automaticamente
            node = make_stable_layer(
                node_id=node_id,
                label=ent.name,
                node_type=ent.entity_type,
                signature=ent.signature,
                docstring=ent.docstring,
                source_ref=ent.source_ref,
                hierarchy_path=hierarchy,
                semantic_field=field,
                intersections=intersections,
                relations=[],           # popolate nella fase successiva per UUID
                prototypicality=proto,
                synonyms=synonyms,
                keywords=keywords
            )
            nodes.append(node)

        return nodes

    def _infer_relations(self, nodes: list) -> list:
        """
        Inferisce relazioni strutturali tra nodi.
        Le relazioni usano target_uuid (UUID del nodo destinazione),
        non node_id. Il node_id viene usato solo durante il lookup
        all'interno di questa stessa sessione di build.
        """
        # Indice temporaneo node_id → uuid (solo per questa sessione di build)
        id_to_uuid = {n.node_id: n.uuid for n in nodes}
        field_map: dict = {}
        for n in nodes:
            field_map.setdefault(n.semantic_field, []).append(n.uuid)

        for node in nodes:
            relations = []

            # Relazione part_of: nodo con gerarchia più corta nella stessa area
            if len(node.hierarchy_path) > 1:
                parent_hierarchy = node.hierarchy_path[:-1]
                for candidate in nodes:
                    if (candidate.uuid != node.uuid and
                            candidate.hierarchy_path == parent_hierarchy):
                        relations.append({
                            "target_uuid": candidate.uuid,
                            "type": "part_of"
                        })
                        break

            # Relazioni per campo semantico (max 3)
            same_field_uuids = [u for u in field_map.get(node.semantic_field, [])
                                if u != node.uuid][:3]
            for u in same_field_uuids:
                relations.append({"target_uuid": u, "type": "related"})

            # Relazioni per intersezioni (max 2 per campo)
            for intersect_field in node.intersections:
                for u in field_map.get(intersect_field, [])[:2]:
                    if u != node.uuid:
                        relations.append({"target_uuid": u, "type": "intersects"})

            # Deduplica
            seen = set()
            unique = []
            for r in relations:
                key = r["target_uuid"] + r["type"]
                if key not in seen:
                    seen.add(key)
                    unique.append(r)

            node.relations = unique[:8]

        return nodes


def merge_into_graph(existing_graph, new_nodes: list, overwrite: bool = False) -> dict:
    """
    Aggiunge nodi al grafo esistente.
    I nodi portano già UUID generati da make_stable_layer() —
    non vengono creati nuovi UUID durante il merge.
    Se overwrite=False, salta i nodi con node_id già presenti.
    """
    added, skipped, overwritten = 0, 0, 0
    for node in new_nodes:
        existing = existing_graph.get_node_by_id(node.node_id)
        if existing is not None:
            if overwrite:
                existing_graph._nodes_by_uuid[existing.stable.uuid].stable = node
                overwritten += 1
            else:
                skipped += 1
        else:
            existing_graph.add_node(node)
            added += 1
    return {"added": added, "skipped": skipped, "overwritten": overwritten}
