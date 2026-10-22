"""Opt-in real browser coverage, reusing the Phase 4 CDP harness."""
import os
from threading import Event, Thread
import pytest
from test_auth import app, accounts, claims
from test_product_browser import browser_server, run_browser

pytestmark = pytest.mark.skipif(os.getenv("ASSUREX_BROWSER_TESTS") != "1", reason="Opt-in browser test; see documentation/claims.md")


def test_complete_claim_workflow(browser_server, app, claims, tmp_path):
    from backend.services.assessments import process_next
    app.config.update(GTM_PREDICTOR=lambda path: [.85, .1, .05], MODEL_CARD_PATH=str(tmp_path / "cards"))
    stopped = Event()
    def work():
        with app.app_context():
            while not stopped.wait(.25):
                process_next()
    thread = Thread(target=work, daemon=True)
    thread.start()
    try:
        run_browser(browser_server, "claims")
    finally:
        stopped.set()
        thread.join(timeout=30)


def test_role_workspaces_and_access_denied(browser_server, claims):
    run_browser(browser_server, "roles")
