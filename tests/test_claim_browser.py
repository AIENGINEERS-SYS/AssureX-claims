"""Opt-in real browser coverage, reusing the Phase 4 CDP harness."""
import os
import pytest
from test_auth import app, accounts, claims
from test_product_browser import browser_server, run_browser

pytestmark = pytest.mark.skipif(os.getenv("ASSUREX_BROWSER_TESTS") != "1", reason="Opt-in browser test; see documentation/claims.md")


def test_complete_claim_workflow(browser_server, claims):
    run_browser(browser_server, "claims")
