"""Real browser report preview, export, history and mobile layout coverage."""
import os
from threading import Event, Thread
import pytest
from test_auth import app, accounts, claims  # noqa: F401
from test_product_browser import browser_server, run_browser  # noqa: F401

pytestmark = pytest.mark.skipif(os.getenv('ASSUREX_BROWSER_TESTS') != '1', reason='Opt-in real browser coverage')


def test_report_center(browser_server, app, claims, tmp_path):
    from backend.services.report_jobs import process_next
    app.config['REPORT_STORAGE_PATH'] = str(tmp_path / 'reports')
    stopped = Event()

    def work():
        with app.app_context():
            while not stopped.wait(.25):
                process_next()

    thread = Thread(target=work, daemon=True)
    thread.start()
    try:
        run_browser(browser_server, 'reports')
    finally:
        stopped.set();thread.join(timeout=10)
