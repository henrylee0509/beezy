"""HTTP API: authentication, tenant isolation, RBAC, approval enforcement, validation, assistant, uploads, exports."""
import io
import time

from backend.tests.conftest import login


def test_login_and_me(client):
    assert client.post("/api/auth/login", json={"email": "owner@brewbean.demo", "password": "wrong"}).status_code == 401
    h = login(client)
    me = client.get("/api/auth/me", headers=h).json()
    assert me["role"] == "OWNER" and "payroll.approve" in me["permissions"]
    assert client.get("/api/auth/me").status_code == 401


def test_logout_revokes_token(client):
    h = login(client)
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_register_validation_and_new_business(client):
    bad = client.post("/api/auth/register", json={"name": "A", "email": "nope", "password": "short", "business_name": "X"})
    assert bad.status_code == 422
    r = client.post("/api/auth/register", json={"name": "Lena", "email": "lena@shop.test", "password": "longpassword",
                                                 "business_name": "Lena's Shop", "currency": "eur"})
    assert r.status_code == 200 and r.json()["business"]["currency"] == "EUR"
    h = {"Authorization": "Bearer " + r.json()["token"]}
    assert client.get("/api/inventory", headers=h).json()["products"] == []


def test_tenant_isolation(client, owner):
    pos = client.post("/api/procurement/draft-restock", json={}, headers=owner).json()["created"]
    po_id = pos[0]["po_id"]
    product_id = client.get("/api/inventory", headers=owner).json()["products"][0]["id"]
    other = client.post("/api/auth/register", json={"name": "Eve", "email": "eve@other.test", "password": "longpassword",
                                                    "business_name": "Other Co"}).json()
    h2 = {"Authorization": "Bearer " + other["token"]}
    assert client.get(f"/api/purchase-orders/{po_id}", headers=h2).status_code == 404
    assert client.post(f"/api/purchase-orders/{po_id}/approve", headers=h2).status_code == 404
    assert client.post(f"/api/inventory/{product_id}/adjust", json={"delta": -1}, headers=h2).status_code == 404
    assert client.get("/api/contracts", headers=h2).json()["contracts"] == []
    assert client.post("/api/auth/switch", json={"business_id": 1}, headers=h2).status_code == 403


def test_role_based_access(client):
    staff, hr, fin = login(client, "staff"), login(client, "hr"), login(client, "finance")
    assert client.get("/api/finance/summary", headers=staff).status_code == 403
    assert client.get("/api/payroll", headers=fin).status_code == 403
    assert client.get("/api/payroll", headers=hr).status_code == 200
    mgr = login(client, "manager")
    emps = client.get("/api/employees", headers=mgr).json()
    assert emps["salary_visible"] is False and emps["employees"][0]["base_salary"] is None
    ov = client.get("/api/dashboard/overview", headers=staff).json()
    assert "revenue" not in ov["kpis"]


def test_manager_spending_limit_and_owner_approval(client, owner):
    created = client.post("/api/procurement/draft-restock", json={}, headers=owner).json()["created"]
    big = next(c for c in created if c["total"] > 2000)
    small = next(c for c in created if c["total"] < 2000)
    mgr = login(client, "manager")
    assert client.post(f"/api/approvals/{big['approval_id']}/approve", headers=mgr).status_code == 403
    assert client.post(f"/api/approvals/{small['approval_id']}/approve", headers=mgr).status_code == 200
    staff = login(client, "staff")
    assert client.post(f"/api/approvals/{big['approval_id']}/approve", headers=staff).status_code == 403
    r = client.post(f"/api/approvals/{big['approval_id']}/approve", headers=owner)
    assert r.status_code == 200 and r.json()["result"]["status"] == "SUBMITTED"
    assert client.post(f"/api/approvals/{big['approval_id']}/approve", headers=owner).status_code == 400  # already decided


def test_salary_change_requires_owner(client):
    hr = login(client, "hr")
    emp = client.get("/api/employees", headers=hr).json()["employees"][0]
    assert client.put(f"/api/employees/{emp['id']}", json={"base_salary": 9999}, headers=hr).status_code == 403
    assert client.put(f"/api/employees/{emp['id']}", json={"job_title": "Lead"}, headers=hr).status_code == 200


def test_validation_errors(client, owner):
    r = client.post("/api/inventory", json={"sku": "X", "name": "Y", "max_stock": -5}, headers=owner)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert client.put("/api/business/settings", json={"low_stock_pct": 4}, headers=owner).status_code == 400


def test_stock_event_triggers_automation(client, owner):
    oat = next(p for p in client.get("/api/inventory", headers=owner).json()["products"] if p["name"] == "Oat Milk")
    r = client.post(f"/api/inventory/{oat['id']}/adjust", json={"delta": -50, "kind": "sale"}, headers=owner).json()
    assert r["product"]["level"] == "LOW"
    assert any(a["rule"] == "Automatic Restock Preparation" for a in r["automations"])
    assert len(client.get("/api/approvals", headers=owner).json()["approvals"]) >= 1


def _wait_run(client, h, sid, run_id):
    for _ in range(100):
        e = client.get(f"/api/assistant/sessions/{sid}/events?run_id={run_id}", headers=h).json()
        if e["run"] and e["run"]["status"] != "running":
            return e
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_assistant_mock_run_end_to_end(client, owner):
    r = client.post("/api/assistant/chat", json={"message": "Restock all products below 20% and check cash flow"}, headers=owner).json()
    assert r["mode"] == "mock"
    ev = _wait_run(client, owner, r["session_id"], r["run_id"])
    kinds = [e["kind"] for e in ev["events"]]
    assert "plan" in kinds and "tool_call" in kinds and "verification" in kinds and kinds[-1] == "final"
    msg = client.get(f"/api/assistant/sessions/{r['session_id']}", headers=owner).json()["messages"][-1]
    assert msg["meta"]["mode"] == "mock" and "Mock mode" in msg["content"]
    assert len(msg["meta"]["actions"]) == 3 and all(a["approval_id"] for a in msg["meta"]["actions"])
    assert msg["meta"]["verification"]["status"] == "verified"
    # sessions are private to the user
    mgr = login(client, "manager")
    assert client.get(f"/api/assistant/sessions/{r['session_id']}", headers=mgr).status_code == 404


def test_assistant_respects_role_permissions(client):
    staff = login(client, "staff")
    r = client.post("/api/assistant/chat", json={"message": "How is cash flow and payroll?"}, headers=staff).json()
    _wait_run(client, staff, r["session_id"], r["run_id"])
    msg = client.get(f"/api/assistant/sessions/{r['session_id']}", headers=staff).json()["messages"][-1]
    assert "Finance Agent" not in msg["meta"]["agents"] and "Not available for your role" in msg["content"]


def test_voice_unavailable_in_mock(client, owner):
    r = client.post("/api/assistant/voice", files={"audio": ("a.wav", b"RIFF0000WAVE", "audio/wav")}, headers=owner)
    assert r.status_code == 503


def test_contract_upload_and_search(client, owner):
    text = ("SERVICE AGREEMENT between Brew & Bean Cafe and SparkleClean Services (Demo) (\"Provider\").\n"
            "Commences on 1 December 2026 and expires on 30 November 2027. Fees $300 per month, payment terms Net 14.\n"
            "This agreement shall automatically renew unless 30 days' written notice is given.")
    r = client.post("/api/contracts/upload", files={"file": ("sparkle.txt", text.encode(), "text/plain")}, headers=owner)
    assert r.status_code == 200, r.text
    c = r.json()["contract"]
    assert c["end_date"] == "2027-11-30" and c["auto_renew"] == 1 and c["category"] == "Service Agreement"
    hits = client.get("/api/contracts/search?q=SparkleClean", headers=owner).json()["results"]
    assert hits and hits[0]["matches"][0]["page"] == 1
    bad = client.post("/api/contracts/upload", files={"file": ("x.exe", b"MZ", "application/octet-stream")}, headers=owner)
    assert bad.status_code == 400


def test_reports_and_exports(client, owner):
    r = client.post("/api/reports/generate", json={"kind": "INVENTORY", "ai_summary": True}, headers=owner).json()
    assert r["narrative_source"] == "template (mock mode)"
    pdf = client.get(f"/api/reports/{r['id']}/export?format=pdf", headers=owner)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    csv = client.get(f"/api/reports/{r['id']}/export?format=csv", headers=owner)
    assert "Coffee Beans" in csv.text
    fin = login(client, "finance")
    assert client.post("/api/reports/generate", json={"kind": "PAYROLL"}, headers=fin).status_code == 403


def test_payroll_api_flow_and_audit(client, owner):
    run = client.post("/api/payroll/prepare", json={}, headers=owner).json()
    assert run["status"] == "AWAITING_APPROVAL"
    assert client.post(f"/api/payroll/{run['id']}/pay", headers=owner).status_code == 400
    assert client.post(f"/api/payroll/{run['id']}/approve", headers=owner).json()["status"] == "APPROVED"
    assert client.post(f"/api/payroll/{run['id']}/pay", headers=owner).json()["status"] == "PAID"
    actions = [a["action"] for a in client.get("/api/audit?limit=200", headers=owner).json()["items"]]
    assert {"payroll.prepare", "payroll.approve", "payroll.paid"} <= set(actions)


def test_reset_demo(client, owner):
    client.post("/api/procurement/draft-restock", json={}, headers=owner)
    r = client.post("/api/business/reset-demo", headers=owner).json()
    h = {"Authorization": "Bearer " + r["token"]}
    assert client.get("/api/approvals", headers=h).json()["approvals"] == []
    assert len(client.get("/api/inventory/low-stock", headers=h).json()["products"]) == 3


def test_frontend_served(client):
    assert "BusinessPilot" in client.get("/").text
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/api/nope").status_code == 404


_ = io
