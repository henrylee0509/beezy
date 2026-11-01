"""A protocol-level fake of the Meta Model API (/v1/chat/completions, /v1/asr/transcribe) used by the
test-suite to exercise the real-mode orchestration code path end to end without network access.
It behaves like a tool-calling model: the orchestrator consults agents, agents call their first tools,
then everyone summarises using numbers taken from the tool results."""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REQUESTS: list[dict] = []


def _tool_call(i, name, args):
    return {"id": f"call_{i}_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _respond(body: dict) -> dict:
    msgs = body["messages"]
    tools = [t["function"]["name"] for t in body.get("tools", [])]
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    system = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
    msg: dict = {"role": "assistant", "content": None}
    if any(t.startswith("consult_") for t in tools):  # orchestrator
        if not tool_msgs:
            wanted = [t for t in ("consult_inventory", "consult_finance", "consult_bi", "consult_procurement") if t in tools]
            user = msgs[-1]["content"].lower()
            calls = [_tool_call(i, t, {"task": f"Handle: {user}"}) for i, t in enumerate(wanted[:3])]
            if "restock" in user and "consult_procurement" in tools:
                calls.append(_tool_call(9, "consult_procurement", {"task": "Draft purchase orders for all low-stock products"}))
            msg["tool_calls"] = calls
        else:
            nums = re.findall(r'"cash_balance":\{"value":([\d.]+)', " ".join(m["content"] for m in tool_msgs))
            nums += re.findall(r'"cash_balance":([\d.]+)', " ".join(m["content"] for m in tool_msgs))
            cash = float(nums[0]) if nums else None
            msg["content"] = ("**Health check complete.** " + (f"Cash balance is SGD {cash:,.2f} (recorded). " if cash else "")
                              + "Coffee Beans, Fresh Milk and Chocolate Syrup are below 20%. Invented figure: SGD 99,999.99.")
    elif tools:  # specialist agent
        if not tool_msgs:
            first = tools[0]
            want_action = "draft purchase orders" in msgs[-1]["content"].lower() and "draft_restock_purchase_orders" in tools
            if want_action:
                msg["tool_calls"] = [_tool_call(0, "draft_restock_purchase_orders", {})]
            else:
                schema = next(t for t in body["tools"] if t["function"]["name"] == first)["function"]["parameters"]
                args = {"product": "Coffee Beans"} if "product" in schema.get("required", []) else {}
                msg["tool_calls"] = [_tool_call(0, first, args)]
        else:
            msg["content"] = "Findings: " + tool_msgs[-1]["content"][:300]
    elif "Extract structured metadata" in system:
        msg["content"] = json.dumps({"title": "Pastry Supply Agreement", "category": "Supplier Agreement", "counterparty": "FreshBake (Demo)",
                                     "start_date": "2026-11-01", "end_date": "2027-10-31", "value": 1200, "value_period": "month",
                                     "payment_terms": "Net 30", "auto_renew": True, "notice_days": 45, "termination_clause": "30 days notice",
                                     "summary": "Supply of pastries.", "key_clauses": [{"type": "Auto-renewal", "text": "Renews yearly", "page": 1}]})
    else:
        msg["content"] = "Brief generated from facts."
    return {"id": "fake", "model": body.get("model"), "choices": [{"index": 0, "message": msg,
                                                                    "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        if self.headers.get("Authorization") != "Bearer test-key":
            self.send_response(401)
            self.end_headers()
            return
        if self.path.endswith("/chat/completions"):
            body = json.loads(raw)
            REQUESTS.append(body)
            out = json.dumps(_respond(body)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path.endswith("/asr/transcribe"):
            assert b"muse-voice-transcribe" in raw and b"RIFF" in raw
            out = b"Which products are running low?"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
        else:
            out = b"{}"
            self.send_response(404)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def start() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"
