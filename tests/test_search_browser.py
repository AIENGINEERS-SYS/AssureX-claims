"""Opt-in browser search, filters, saved searches and mobile checks."""
import os
import pytest
from test_auth import app, accounts, claims  # noqa: F401
from test_product_browser import browser_server, run_browser  # noqa: F401

pytestmark = pytest.mark.skipif(os.getenv('ASSUREX_BROWSER_TESTS') != '1', reason='Opt-in real browser test')


def test_search_workspace(browser_server, claims):
    run_browser(browser_server, 'search')
