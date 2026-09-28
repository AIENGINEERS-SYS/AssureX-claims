"""Opt-in headless Chrome/Edge tests using Node's built-in WebSocket/CDP support."""
import os
from pathlib import Path
import shutil
import subprocess
from threading import Thread
import pytest
from werkzeug.serving import make_server
from test_auth import app, accounts, PASSWORD

pytestmark = pytest.mark.skipif(os.getenv("ASSUREX_BROWSER_TESTS") != "1", reason="Opt-in browser test; see documentation/products.md")


@pytest.fixture
def browser_server(app,accounts,tmp_path,monkeypatch):
    from datetime import date
    from backend.services import warranty_calculations
    import backend.web
    monkeypatch.setattr(warranty_calculations,"current_date",lambda:date(2026,9,26))
    monkeypatch.setattr(backend.web,"current_date",lambda:date(2026,9,26))
    browser = os.getenv("ASSUREX_BROWSER_PATH")
    if not browser:
        candidates = [shutil.which("google-chrome"),shutil.which("chromium"),shutil.which("msedge"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
        browser = next((item for item in candidates if item and Path(item).is_file()),None)
    if not browser or not shutil.which("node"):
        pytest.fail("Browser tests need Node 22+ and Chrome/Edge; set ASSUREX_BROWSER_PATH if necessary")
    server = make_server("127.0.0.1",0,app,threaded=True)
    thread = Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}",browser,str(tmp_path/"browser-profile")
    finally:
        server.shutdown(); thread.join(timeout=5); server.server_close()


def run_browser(browser_server,scenario):
    url,browser,profile = browser_server
    result = subprocess.run(["node","tests/browser_products.mjs",scenario,url,browser,profile],
        env=os.environ | {"ASSUREX_TEST_PASSWORD":PASSWORD},capture_output=True,text=True,timeout=100)
    assert result.returncode == 0,result.stdout+result.stderr


def test_complete_product_workflow_and_mobile(browser_server):
    run_browser(browser_server,"workflow")


def test_untrusted_product_text_is_not_executed(browser_server,app):
    from datetime import date
    from backend.db.models import Product,User
    from backend.extensions import db
    from sqlalchemy import select
    name = '<img src=x onerror="window.attacked=true">'
    with app.app_context():
        user = db.session.scalar(select(User).where(User.email == "customer@example.com"))
        db.session.add(Product(user_id=user.id,name=name,brand="Example",category="<script>alert(1)</script>",
            model_number="M1",serial_number="XSS-1",purchase_date=date(2025,1,1),purchase_price=1,retailer="Shop"))
        db.session.commit()
    run_browser(browser_server,"xss")
