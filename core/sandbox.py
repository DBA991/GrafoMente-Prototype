"""
core/sandbox.py
---------------
Verifica e correzione del codice generato dall'LLM.

Pipeline:
  1. Blocco esplicito di pattern pericolosi (prima di qualsiasi esecuzione)
  2. Esecuzione in subprocess isolato con timeout
  3. In caso di errore, chiede all'LLM di correggerlo passando error_context
     (stesso RAG + errore) — max_attempts volte
"""

from __future__ import annotations
import subprocess, sys, re, tempfile, os
from dataclasses import dataclass
from typing import Optional


DANGEROUS_PATTERNS = [
    r"\bos\.system\b",
    r"\bos\.popen\b",
    r"\bos\.remove\b",
    r"\bos\.unlink\b",
    r"\bos\.rmdir\b",
    r"\bshutil\.rmtree\b",
    r"\bsubprocess\.",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"open\s*\([^)]*['\"]w['\"]",   # scrittura su file
    r"open\s*\([^)]*['\"]a['\"]",   # append su file
    r"\b__import__\s*\(",
    r"\bimportlib\.",
    r"\bctypes\.",
]


@dataclass
class SandboxResult:
    success: bool
    code: str
    output: str
    error: Optional[str]
    attempts: int
    blocked: bool = False      # True se bloccato da pattern pericoloso


class SandboxVerifier:

    def __init__(self, timeout: int = 10, max_attempts: int = 3):
        self.timeout = timeout
        self.max_attempts = max_attempts

    def verify_and_correct(self, code: str, language: str,
                           query: str, rag_context: str,
                           llm_connector) -> SandboxResult:
        current_code = code

        for attempt in range(1, self.max_attempts + 1):

            # 1. Blocco pattern pericolosi — prima di qualsiasi esecuzione
            blocked, reason = self._check_dangerous(current_code)
            if blocked:
                error_context = (
                    f"Il seguente codice contiene pattern non sicuri e non può essere eseguito:\n"
                    f"```{language}\n{current_code}\n```\n"
                    f"Pattern non consentito: {reason}\n"
                    f"Riscrivi il codice senza usare: os.system, subprocess, eval, exec, "
                    f"open in modalità scrittura, o altre operazioni di sistema."
                )
                if attempt < self.max_attempts:
                    corrected = llm_connector.generate(
                        rag_context=rag_context,
                        query=query,
                        error_context=error_context
                    )
                    current_code = self._extract_code(corrected, language) or current_code
                    continue
                return SandboxResult(
                    success=False, code=current_code, output="",
                    error=f"Pattern pericoloso bloccato: {reason}",
                    attempts=attempt, blocked=True
                )

            # 2. Esecuzione in subprocess isolato
            ok, output, error = self._run_in_sandbox(current_code, language)
            if ok:
                return SandboxResult(
                    success=True, code=current_code,
                    output=output, error=None, attempts=attempt
                )

            # 3. Loop di correzione: stesso RAG + errore nel contesto
            if attempt < self.max_attempts:
                error_context = (
                    f"Il seguente codice ha prodotto un errore:\n"
                    f"```{language}\n{current_code}\n```\n"
                    f"Errore:\n{error}"
                )
                corrected = llm_connector.generate(
                    rag_context=rag_context,
                    query=query,
                    error_context=error_context
                )
                current_code = self._extract_code(corrected, language) or current_code

        return SandboxResult(
            success=False, code=current_code, output="",
            error=error, attempts=self.max_attempts
        )

    def _check_dangerous(self, code: str) -> tuple:
        """
        Blocco esplicito di pattern pericolosi prima dell'esecuzione.
        Restituisce (blocked: bool, reason: str).
        """
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, code):
                return True, pattern
        return False, ""

    def _run_in_sandbox(self, code: str, language: str) -> tuple:
        """Esegue il codice in un processo isolato con timeout."""
        if language == "python":
            return self._run_python(code)
        # Altri linguaggi: nessuna esecuzione, solo conferma ricezione
        return True, "[esecuzione non supportata per questo linguaggio]", None

    def _run_python(self, code: str) -> tuple:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                        delete=False) as f:
            f.write(code)
            path = f.name
        try:
            result = subprocess.run(
                [sys.executable, path],
                capture_output=True, text=True, timeout=self.timeout,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            )
            if result.returncode == 0:
                return True, result.stdout, None
            return False, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", f"Timeout ({self.timeout}s superato)"
        except Exception as e:
            return False, "", str(e)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _extract_code(self, text: str, language: str) -> Optional[str]:
        patterns = [
            rf"```{language}\s*(.*?)```",
            r"```\s*(.*?)```",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.DOTALL)
            if m:
                return m.group(1).strip()
        return text.strip() if text.strip() else None
