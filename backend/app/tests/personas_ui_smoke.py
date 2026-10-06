"""Built-renderer UI smoke: `npm run build`, then `python -m app.tests.personas_ui_smoke`.

Starts a private mini backend and static renderer server. All research runs in Demo mode;
there are no live source calls or writes to the user's normal database.
"""
from __future__ import annotations

import socket
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright

from app import config, db
from app.routers import personas


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass


def _preview_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(personas.router)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/library")
    def library():
        return []

    @app.get("/marketing-plan/models")
    def models():
        return {"models": ["Auto"]}

    @app.get("/marketing-plan/keyword-surfer/runs")
    def surfer_runs():
        return {"runs": []}

    @app.get("/queue")
    def queue():
        return {"running": 0, "waiting": 0, "busy": False, "queued": False, "lanes": {}}

    return app


def main() -> None:
    renderer = Path(__file__).resolve().parents[3] / "electron" / "out" / "renderer"
    if not (renderer / "index.html").exists():
        raise RuntimeError("Build the renderer first: cd electron && npm run build")
    with tempfile.TemporaryDirectory(prefix="mraim-persona-ui-") as temporary:
        original_db_path = config.DB_PATH
        config.DB_PATH = Path(temporary) / "personas.sqlite3"
        db.init_db()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(renderer)))
        http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        http_thread.start()
        backend_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        backend_socket.bind(("127.0.0.1", 0))
        backend_socket.listen()
        backend_port = backend_socket.getsockname()[1]
        backend = uvicorn.Server(uvicorn.Config(_preview_app(), host="127.0.0.1", port=backend_port, log_level="error"))
        backend_thread = threading.Thread(target=backend.run, kwargs={"sockets": [backend_socket]}, daemon=True)
        backend_thread.start()
        try:
            for _ in range(50):
                try:
                    if requests.get(f"http://127.0.0.1:{backend_port}/health", timeout=.2).ok:
                        break
                except requests.RequestException:
                    time.sleep(.1)
            else:
                raise RuntimeError("Preview backend did not start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1180, "height": 850}, accept_downloads=True)
                page.add_init_script(f"""
                    window.api = {{
                      backendUrl: 'http://127.0.0.1:{backend_port}', apiToken: '', debugRoute: 'research',
                      update: {{ check: async () => {{ throw new Error('preview') }}, onState: () => () => {{}} }},
                      settings: {{
                        getHfToken: async () => null,
                        getAll: async () => ({{ hfToken: '', youtubeApiKey: '', mastodonInstance: '',
                          mastodonAccessToken: '', mastodonAccounts: [], setupWizard: {{ skippedAt: 'preview' }} }})
                      }}
                    }}
                """)
                page.goto(f"http://127.0.0.1:{httpd.server_address[1]}", wait_until="domcontentloaded")
                page.get_by_role("button", name="Audience Personas", exact=True).click()
                page.get_by_role("button", name="Try offline demo").click()
                page.get_by_role("heading", name="1. Interview").wait_for()
                assert page.locator("#persona-A1").input_value()
                page.get_by_role("button", name="Build research plan").click()
                page.get_by_role("heading", name="2. Research plan").wait_for()
                page.get_by_role("button", name="Approve & run").click()
                page.get_by_role("heading", name="4. Review candidate segments").wait_for(timeout=60000)
                page.get_by_role("button", name="Create personas").click()
                page.get_by_role("heading", name="5. Personas").wait_for()
                assert 2 <= page.locator(".persona-card").count() <= 4
                page.locator(".persona-claim").first.click()
                assert page.get_by_role("dialog", name="Claim evidence").is_visible()
                assert page.locator(".persona-evidence").count() > 0
                page.keyboard.press("Escape")
                assert not page.get_by_role("dialog", name="Claim evidence").is_visible()
                page.reload(wait_until="domcontentloaded")
                page.get_by_role("button", name="Audience Personas", exact=True).click()
                page.get_by_role("heading", name="5. Personas").wait_for(timeout=15000)
                page.set_viewport_size({"width": 460, "height": 800})
                page.locator(".persona-card").first.scroll_into_view_if_needed()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
                browser.close()
            print("Persona UI smoke passed: demo flow, evidence drawer, restart, narrow viewport")
        finally:
            backend.should_exit = True
            backend_thread.join(timeout=5)
            backend_socket.close()
            httpd.shutdown()
            httpd.server_close()
            http_thread.join(timeout=5)
            config.DB_PATH = original_db_path


if __name__ == "__main__":
    main()
