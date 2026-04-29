#!/usr/bin/env python3
"""
run.py — Avvio del sistema GrafoMente
--------------------------------------
1. Genera il grafo seed (se non esiste)
2. Avvia il server FastAPI
3. Apre l'interfaccia nel browser

Utilizzo:
  python run.py                    # avvio normale
  python run.py --rebuild-graph    # rigenera grafo e vocabolario da zero
"""

import sys
import os
import subprocess
import argparse
import time
import webbrowser
import threading

BASE_DIR = os.path.dirname(__file__)
GRAPH_PATH = os.path.join(BASE_DIR, "data/graph/graph.json")
VOCAB_PATH = os.path.join(BASE_DIR, "data/vocabulary/vocab.json")

def build_graph():
    print("→ Costruzione grafo seed...")
    result = subprocess.run(
        [sys.executable, os.path.join(BASE_DIR, "data/seed_graph.py")],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(result.stdout.strip())
    else:
        print("⚠ Errore nella costruzione del grafo:")
        print(result.stderr)
        sys.exit(1)

def open_browser_delayed():
    time.sleep(2)
    ui_path = os.path.join(BASE_DIR, "ui/index.html")
    webbrowser.open(f"file://{os.path.abspath(ui_path)}")
    print(f"→ UI aperta: {ui_path}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild-graph", action="store_true",
                        help="Rigenera grafo e vocabolario da zero")
    parser.add_argument("--no-browser", action="store_true",
                        help="Non aprire il browser automaticamente")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    # Costruisci grafo se mancante o se richiesto
    if args.rebuild_graph or not os.path.exists(GRAPH_PATH):
        build_graph()
    else:
        print(f"✓ Grafo esistente: {GRAPH_PATH}")

    # Apri browser in background
    if not args.no_browser:
        t = threading.Thread(target=open_browser_delayed, daemon=True)
        t.start()

    # Avvia server
    print(f"→ Server FastAPI su http://localhost:{args.port}")
    print("  Premi Ctrl+C per fermare.\n")

    server_path = os.path.join(BASE_DIR, "api/server.py")
    subprocess.run([
        sys.executable, "-m", "uvicorn",
        "api.server:app",
        "--host", "0.0.0.0",
        "--port", str(args.port),
        "--reload"
    ], cwd=BASE_DIR)

if __name__ == "__main__":
    main()
