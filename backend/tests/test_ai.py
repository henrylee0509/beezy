"""AI layer: tool contracts & validation, permission filtering, verifier, and the real Muse Spark code path
exercised end-to-end against a protocol-level fake of the Meta Model API."""
import time

import pytest

from backend.app import config, db
from backend.app.ai import features, meta_client, orchestrator
from backend.app.ai.tools import TOOL_MAP, ToolError, execute, tools_for, validate_args
from backend.tests import fake_meta
from backend.tests.conftest import FakeCtx, login


@pytest.fixture
def real_mode():
    srv, url = fake_meta.start()
    old = (config.META_BASE_URL, config.META_API_KEY, config.AI_MODE_SETTING)
    config.META_BASE_URL, config.META_API_KEY, config.AI_MODE_SETTING = url, "test-key", "real"
    fake_meta.REQUESTS.clear()
    yield url
    config.META_BASE_URL, config.META_API_KEY, config.AI_MODE_SETTING = old
    srv.shutdown()


def test_tool_argument_validation():
    schema = TOOL_MAP["compare_supplier_quotes"].parameters
    assert validate_args(schema, {"product": "Coffee Beans", "quantity": "40", "junk": 1}) == {"product": "Coffee Beans", "quantity": 40.0}
    with pytest.raises(ToolError):
        validate_args(schema, {})
    with pytest.raises(ToolError):
        validate_args(schema, {"product": "x", "quantity": 0})
    with pytest.raises(ToolError):
        validate_args(TOOL_MAP["get_purchase_orders"].parameters, {"status": "SHIPPED"})


def test_tools_filtered_by_role(conn, biz):
    staff = FakeCtx(biz, "STAFF")
    assert tools_for("finance", staff) == []
    assert not any(t.name == "estimate_payroll" for t in tools_for("hr", FakeCtx(biz, "MANAGER")))
    with pytest.raises(ToolError):
        execute(TOOL_MAP["draft_restock_purchase_orders"], staff, {})


def test_action_tool_creates_drafts_not_submissions(conn, biz):
    res = execute(TOOL_MAP["draft_restock_purchase_orders"], FakeCtx(biz), {"products": ["Coffee Beans"]})
    assert len(res["created"]) == 1 and res["created"][0]["status"] == "AWAITING_APPROVAL"


def test_affordability_is_deterministic(conn, biz):
    a = execute(TOOL_MAP["evaluate_affordability"], FakeCtx(biz), {"days": 30})
    total = a["payroll_due"]["value"] + a["supplier_bills_due"]["value"] + a["recurring_expenses_due"]["value"] + \
        a["recommended_restock_cost"]["value"] + a["open_purchase_orders_due"]["value"]
    assert a["total_committed_outflows"] == pytest.approx(total)
    assert a["projected_balance_after_commitments"]["value"] == pytest.approx(
        a["current_cash_balance"]["value"] + a["expected_customer_receipts_not_overdue"]["value"] - total, abs=0.02)


def test_verifier_flags_invented_numbers():
    outputs = [{"result": {"cash": 35240.55, "pct": 12.0, "text": "projected SGD 12,103.63"}}]
    ok = orchestrator.verify("Cash is SGD 35,240.55 and stock is at 12% with SGD 12,104 projected.", outputs)
    assert ok["status"] == "verified" and ok["figures_checked"] == 3
    bad = orchestrator.verify("Cash is SGD 35,240.55 but profit is SGD 99,999.99.", outputs)
    assert bad["status"] == "partial" and "99,999.99" in bad["unverified"][0]


def test_parse_json_handles_fences():
    assert meta_client.parse_json('```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}


def _wait(client, h, r):
    for _ in range(200):
        e = client.get(f"/api/assistant/sessions/{r['session_id']}/events?run_id={r['run_id']}", headers=h).json()
        if e["run"] and e["run"]["status"] != "running":
            return e["events"]
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_real_mode_multi_agent_orchestration(client, real_mode):
    h = login(client)
    r = client.post("/api/assistant/chat", json={"message": "Health check please and restock low items"}, headers=h).json()
    events = _wait(client, h, r)
    plan = next(e for e in events if e["kind"] == "plan")
    assert len(plan["data"]["plan"]) >= 3                      # parallel delegation
    started = {e["agent"] for e in events if e["kind"] == "agent_started"}
    assert {"Inventory Agent", "Finance Agent", "Procurement Agent"} <= started
    msg = client.get(f"/api/assistant/sessions/{r['session_id']}", headers=h).json()["messages"][-1]
    assert msg["meta"]["mode"] == "real" and msg["meta"]["model_calls"] >= 5
    assert any(a["type"] == "PURCHASE_ORDER" and a["status"] == "AWAITING_APPROVAL" for a in msg["meta"]["actions"])
    assert "99,999.99" in msg["meta"]["verification"]["unverified"][0]   # fake model's invented figure is caught
    orch_req = next(q for q in fake_meta.REQUESTS if any(t["function"]["name"].startswith("consult_") for t in q.get("tools", [])))
    assert orch_req["model"] == config.META_MODEL and orch_req["parallel_tool_calls"] is True
    # tool results were sent back with matching tool_call_id after the assistant message
    follow = [q for q in fake_meta.REQUESTS if any(m["role"] == "tool" for m in q["messages"])]
    assert follow and all(m.get("tool_call_id") for q in follow for m in q["messages"] if m["role"] == "tool")
    with db.connect() as conn:
        assert db.scalar(conn, "SELECT COUNT(*) FROM ai_requests WHERE mode='real' AND status='ok'") >= 5


def test_real_mode_falls_back_when_api_down(client, real_mode):
    config.META_BASE_URL = "http://127.0.0.1:9/v1"
    config.META_MAX_RETRIES, old = 1, config.META_MAX_RETRIES
    try:
        h = login(client)
        r = client.post("/api/assistant/chat", json={"message": "Which products are running low?"}, headers=h).json()
        _wait(client, h, r)
        msg = client.get(f"/api/assistant/sessions/{r['session_id']}", headers=h).json()["messages"][-1]
        assert msg["meta"]["mode"] == "fallback" and msg["meta"]["fallback_reason"]
    finally:
        config.META_MAX_RETRIES = old


def test_contract_extraction_with_muse_spark(conn, biz, real_mode):
    meta = features.extract_contract(biz, ["Some contract text"], "pastry.pdf")
    assert meta["extraction_source"].startswith("Muse Spark") and meta["notice_days"] == 45


def test_voice_transcription(client, real_mode):
    h = login(client)
    wav = b"RIFF" + b"\x00" * 40 + b"data" + b"\x00" * 2000
    r = client.post("/api/assistant/voice", files={"audio": ("s.wav", wav, "audio/wav")}, headers=h)
    assert r.status_code == 200 and r.json()["text"] == "Which products are running low?"


def test_briefing_uses_model_and_verifies(client, real_mode):
    h = login(client)
    b = client.get("/api/dashboard/briefing?refresh=1", headers=h).json()
    assert b["source"].startswith("Muse Spark") and "verification" in b
