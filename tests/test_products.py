"""Phase 4 calendar boundaries, product workflows, ownership and filtering."""
from datetime import date, timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from backend.db.models import Claim, Product, Warranty
from backend.extensions import db
from backend.services import warranty_calculations as calc
from test_auth import app, client, accounts, bearer, login

TODAY = date(2026, 9, 26)


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr(calc, "current_date", lambda: TODAY)
    return TODAY


@pytest.fixture
def headers(client, accounts, today):
    return bearer(login(client)["access_token"])


def payload(**changes):
    return {"name": "Living room television", "brand": "Example", "category": "Electronics",
        "model_number": "TV-42", "serial_number": "SN-001", "purchase_date": "2025-01-01",
        "purchase_price": "75000.25", "retailer": "Example Store", "warranty_duration": 2,
        "warranty_duration_unit": "years", "coverage": "Parts and labour",
        "exclusions": ["Accidental damage"], "service_center_conditions": "Authorized centers only"} | changes


def register(client, headers, **changes):
    response = client.post("/api/products", headers=headers, json=payload(**changes))
    assert response.status_code == 201, response.json
    return response.json["product"]


def warranty_input(**changes):
    return {"provider": "Extended Care", "start_date": "2027-01-02", "duration": 12,
            "duration_unit": "months", "coverage": "Parts", "exclusions": [],
            "service_center_conditions": "Authorized centers"} | changes


@pytest.mark.parametrize("start,duration,unit,expected", [
    (date(2025,1,1),2,"years",date(2027,1,1)),
    (date(2024,2,29),1,"years",date(2025,2,28)),
    (date(2025,1,31),1,"months",date(2025,2,28)),
    (date(2024,1,31),1,"months",date(2024,2,29)),
    (date(2025,12,31),2,"months",date(2026,2,28)),
])
def test_calendar_expiry(start,duration,unit,expected):
    assert calc.calculate_warranty_expiry(start,duration,unit) == expected


@pytest.mark.parametrize("purchase,expected", [
    (TODAY,"0 days"), (TODAY+timedelta(days=1),"0 days"),
    (date(2025,1,1),"1 year, 8 months"), (date(2023,9,26),"3 years"),
    (date(2026,9,25),"1 day"), (date(2026,3,26),"6 months")])
def test_product_age(purchase,expected):
    assert calc.calculate_product_age(purchase,TODAY) == expected


def record(expiry, *, start=date(2025,1,1), extended=False, id=1):
    return SimpleNamespace(id=id,start_date=start,expiry_date=expiry,extended_warranty=extended)


@pytest.mark.parametrize("days,expected,remaining", [
    (-1,"Expired","Expired"), (0,"Near Expiry","0 days remaining"),
    (1,"Near Expiry","1 day remaining"), (27,"Near Expiry","27 days remaining"),
    (30,"Near Expiry","1 month remaining"), (31,"Active","1 month remaining")])
def test_expiry_boundaries(days,expected,remaining):
    expiry = TODAY+timedelta(days=days)
    assert calc.calculate_warranty_status([record(expiry)],TODAY) == expected
    assert calc.calculate_warranty_remaining(expiry,TODAY) == remaining


def test_extensions_future_gaps_and_legacy_overlap():
    original = record(TODAY-timedelta(days=2))
    extension = record(TODAY+timedelta(days=15), start=TODAY-timedelta(days=1), extended=True,id=2)
    assert calc.calculate_warranty_status([original,extension],TODAY) == "Extended Warranty"
    future = record(date(2028,1,1),start=date(2027,1,2),extended=True,id=3)
    assert calc.calculate_warranty_status([original,future],TODAY) == "Expired"
    assert calc.calculate_warranty_status([future],TODAY) == "Not Started"
    assert calc.calculate_warranty_status([],TODAY) == "No Warranty"
    active_original = record(date(2027,1,1))
    assert calc.calculate_warranty_status([active_original,future],TODAY) == "Active"
    assert calc.calculate_warranty_status([active_original,extension],TODAY) == "Extended Warranty"
    assert calc.calculate_warranty_status([record(TODAY+timedelta(days=20))],TODAY,near_expiry_days=10) == "Active"


def test_successful_registration_and_calculations(client,app,headers):
    product = register(client,headers)
    assert product["product_age"] == "1 year, 8 months"
    assert product["warranty_expiry"] == "2027-01-01"
    assert product["warranty_remaining"] == "3 months remaining"
    assert product["warranty_status"] == "Active"
    assert product["purchase_price"] == "75000.25"
    assert product["warranty_duration"] == 2 and product["warranty_duration_unit"] == "years"
    assert len(product["warranties"]) == 1
    assert product["current_warranty"]["provider"] == "Example"
    assert product["current_warranty"]["coverage"] == "Parts and labour"
    assert any(event["kind"] == "today" for event in product["timeline"])
    with app.app_context():
        assert db.session.scalar(select(Product)).warranties[0].expiry_date == date(2027,1,1)


@pytest.mark.parametrize("field", ["name","brand","category","model_number","serial_number","purchase_date",
                                   "purchase_price","retailer","warranty_duration","warranty_duration_unit"])
def test_missing_required_fields(client,headers,field):
    data = payload(); del data[field]
    response = client.post("/api/products",headers=headers,json=data)
    assert response.status_code == 400
    assert field in response.json["error"]["details"]


@pytest.mark.parametrize("changes", [
    {"purchase_date":"2026-09-27"}, {"purchase_date":"2025-02-30"}, {"purchase_price":"-1"},
    {"purchase_price":"NaN"}, {"purchase_price":"Infinity"}, {"purchase_price":"1.001"},
    {"purchase_price":"10000000000"}, {"purchase_price":True}, {"warranty_duration":0},
    {"warranty_duration":1.5}, {"warranty_duration":True}, {"warranty_duration":101,"warranty_duration_unit":"years"},
    {"warranty_duration_unit":"days"}, {"serial_number":"  "}, {"user_id":5}, {"expiry_date":"2030-01-01"},
    {"warranty_start_date":"2024-12-31"}, {"warranty_start_date":"9999-12-31"}
])
def test_invalid_product_data_rolls_back(client,app,headers,changes):
    assert client.post("/api/products",headers=headers,json=payload(**changes)).status_code == 400
    with app.app_context():
        assert db.session.scalar(select(Product.id)) is None
        assert db.session.scalar(select(Warranty.id)) is None


def test_duplicate_serial_scoped_to_brand_model(client,headers):
    register(client,headers)
    response = client.post("/api/products",headers=headers,json=payload(brand=" example ",model_number="tv-42",serial_number=" sn-001 "))
    assert response.status_code == 409
    assert "serial" in response.json["error"]["message"]
    assert register(client,headers,brand="Another brand")["id"]


def test_registration_today_and_zero_price(client,headers):
    product = register(client,headers,purchase_date=TODAY.isoformat(),purchase_price="0")
    assert product["product_age"] == "0 days"
    assert product["purchase_price"] == "0.00"


def test_search_filters_sort_pagination_and_status_parity(client,app,headers):
    first = register(client,headers,name="Zulu TV")
    register(client,headers,name="Alpha phone",serial_number="SN-002",category="Phones")
    with app.app_context():
        original = db.session.get(Warranty,first["warranties"][0]["id"])
        original.expiry_date = TODAY+timedelta(days=30)
        db.session.commit()
    response = client.get("/api/products?sort=name&per_page=1",headers=headers)
    assert response.status_code == 200,response.json
    assert response.json["items"][0]["name"] == "Alpha phone" and response.json["total"] == 2
    assert response.json["summary"]["by_status"]["Near Expiry"] == 1
    assert client.get("/api/products?q=phone",headers=headers).json["total"] == 1
    assert client.get("/api/products?q=%25",headers=headers).json["total"] == 0
    assert client.get("/api/products?category=Phones",headers=headers).json["items"][0]["category"] == "Phones"
    assert client.get("/api/products?warranty_status=Near%20Expiry",headers=headers).json["items"][0]["id"] == first["id"]
    assert client.get("/api/products?page=2&per_page=1",headers=headers).json["items"]
    assert client.get("/api/products?sort=expiry_date",headers=headers).json["items"][0]["id"] == first["id"]
    assert client.get("/api/products?per_page=101",headers=headers).status_code == 400
    assert client.get("/api/products?warranty_status=invalid",headers=headers).status_code == 400


def test_extensions_and_history(client,headers):
    product = register(client,headers)
    path = f"/api/products/{product['id']}/warranties"
    result = client.post(path,headers=headers,json=warranty_input())
    assert result.status_code == 201,result.json
    assert result.json["warranty"]["is_extended"]
    assert result.json["warranty"]["expiry_date"] == "2028-01-02"
    detail = client.get(f"/api/products/{product['id']}",headers=headers).json["product"]
    assert detail["warranty_status"] == "Active"
    assert detail["warranties"][1]["warranty_status"] == "Not Started"
    assert len(client.get(path,headers=headers).json["items"]) == 2
    assert client.post(path,headers=headers,json=warranty_input(start_date="2027-01-01")).status_code == 400
    assert client.post(path,headers=headers,json=warranty_input(start_date="2027-08-01")).status_code == 400
    assert client.post(path,headers=headers,json=warranty_input(start_date="2028-01-03")).status_code == 201
    assert client.delete(f"/api/warranties/{product['warranties'][0]['id']}",headers=headers).status_code == 409


def test_active_extension_and_expired_gap_filters(client,headers,monkeypatch):
    product = register(client,headers)
    path = f"/api/products/{product['id']}"
    assert client.post(path+"/warranties",headers=headers,json=warranty_input(start_date="2027-02-01")).status_code == 201
    monkeypatch.setattr(calc,"current_date",lambda:date(2027,1,2))
    assert client.get(path,headers=headers).json["product"]["warranty_status"] == "Expired"
    assert client.get("/api/products?warranty_status=Expired",headers=headers).json["total"] == 1
    monkeypatch.setattr(calc,"current_date",lambda:date(2027,2,1))
    assert client.get(path,headers=headers).json["product"]["warranty_status"] == "Extended Warranty"
    assert client.get("/api/products?warranty_status=Extended%20Warranty",headers=headers).json["total"] == 1


def test_product_and_warranty_edits_and_deletes(client,headers):
    product = register(client,headers)
    path = f"/api/products/{product['id']}"
    data = {key:payload()[key] for key in ("name","brand","category","model_number","serial_number","purchase_date","purchase_price","retailer")}
    result = client.put(path,headers=headers,json=data | {"name":"Renamed TV","purchase_price":"123.45"})
    assert result.status_code == 200 and result.json["product"]["name"] == "Renamed TV"
    warranty_id = product["warranties"][0]["id"]
    result = client.put(f"/api/warranties/{warranty_id}",headers=headers,json=warranty_input(start_date="2025-01-01",duration=3,duration_unit="years"))
    assert result.status_code == 200,result.json
    assert result.json["warranty"]["expiry_date"] == "2028-01-01"
    assert client.delete(f"/api/warranties/{warranty_id}",headers=headers).status_code == 200
    assert client.get(path,headers=headers).json["product"]["warranty_status"] == "No Warranty"
    assert client.get("/api/products?warranty_status=No%20Warranty",headers=headers).json["total"] == 1
    result = client.post(path+"/warranties",headers=headers,json=warranty_input(start_date="2027-01-01"))
    assert result.status_code == 201 and not result.json["warranty"]["is_extended"]
    assert client.get("/api/products?warranty_status=Not%20Started",headers=headers).json["total"] == 1
    assert client.delete(path,headers=headers).status_code == 200
    assert client.get(path,headers=headers).status_code == 404


def test_referenced_records_cannot_be_destroyed(client,app,headers,accounts):
    product = register(client,headers)
    path = f"/api/products/{product['id']}"
    claim = client.post("/api/claims",headers=headers,json={"product_id":product["id"],"warranty_id":product["warranties"][0]["id"],
        "fault_date":"2026-09-01","fault_type":"power","fault_description":"No power"})
    assert claim.status_code == 201,claim.json
    assert client.delete(path,headers=headers).status_code == 409
    assert client.delete(f"/api/warranties/{product['warranties'][0]['id']}",headers=headers).status_code == 409
    assert client.put(f"/api/warranties/{product['warranties'][0]['id']}",headers=headers,json=warranty_input()).status_code == 409
    data = {key:payload()[key] for key in ("name","brand","category","model_number","serial_number","purchase_date","purchase_price","retailer")}
    assert client.put(path,headers=headers,json=data | {"serial_number":"CHANGED"}).status_code == 409
    assert client.put(path,headers=headers,json=data | {"name":"New display name"}).status_code == 200


def test_ownership_and_role_enforcement(client,headers,accounts):
    product = register(client,headers)
    other = bearer(login(client,"other")["access_token"])
    admin = bearer(login(client,"admin")["access_token"])
    assert client.get("/api/products",headers=other).json["total"] == 0
    for method,path,data in [
        ("get",f"/api/products/{product['id']}",None),
        ("put",f"/api/products/{product['id']}",{}),
        ("delete",f"/api/products/{product['id']}",None),
        ("get",f"/api/products/{product['id']}/warranties",None),
        ("post",f"/api/products/{product['id']}/warranties",warranty_input()),
        ("put",f"/api/warranties/{product['warranties'][0]['id']}",warranty_input()),
        ("delete",f"/api/warranties/{product['warranties'][0]['id']}",None),
    ]:
        request = getattr(client,method)
        assert request(path,headers=other,json=data).status_code == 404
        assert request(path,json=data).status_code == 401
    for role in ("employee","reviewer"):
        denied = bearer(login(client,role)["access_token"])
        assert client.get("/api/products",headers=denied).status_code == 403
        assert client.post("/api/products",headers=denied,json=payload()).status_code == 403
    assert client.get(f"/api/products/{product['id']}",headers=admin).status_code == 200


def test_preview_and_config_threshold(client,app,headers):
    result = client.post("/api/warranties/preview",headers=headers,json={"start_date":"2024-02-29","duration":1,"duration_unit":"years"})
    assert result.status_code == 200 and result.json["expiry_date"] == "2025-02-28"
    app.config["WARRANTY_NEAR_EXPIRY_DAYS"] = 100
    product = register(client,headers)
    assert product["warranty_status"] == "Near Expiry"
    assert client.get("/api/products?warranty_status=Near%20Expiry",headers=headers).json["total"] == 1


def test_ui_shell_and_csp(client):
    for path in ("/products","/products/new","/products/1","/products/1/edit"):
        response = client.get(path)
        assert response.status_code == 200
        assert b"Products &amp; warranties" in response.data or b"Products & warranties" in response.data
        assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert "script-src" not in client.get("/api/products").headers["Content-Security-Policy"]
    assert client.get("/assets/products.js").status_code == 200


def test_legacy_coverage_survives_warranty_edit(client,app,headers):
    product = register(client,headers)
    warranty_id = product["warranties"][0]["id"]
    with app.app_context():
        db.session.get(Warranty,warranty_id).coverage_conditions = {"parts":True,"labour":False}
        db.session.commit()
    response = client.put(f"/api/warranties/{warranty_id}",headers=headers,
        json=warranty_input(start_date="2025-01-01",duration=2,duration_unit="years",coverage=""))
    assert response.status_code == 200
    assert response.json["warranty"]["coverage_conditions"] == {"parts":True,"labour":False,"description":""}


def test_database_enforces_serial_identity_on_orm_writes(client,app,headers,accounts):
    from sqlalchemy.exc import IntegrityError
    register(client,headers)
    with app.app_context():
        db.session.add(Product(user_id=accounts["other"],name="Duplicate",brand="EXAMPLE",category="Other",
            model_number="tv-42",serial_number="sn-001",purchase_date=TODAY,purchase_price=1,retailer="Store"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
