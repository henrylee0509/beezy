"""Single-purpose Muse Spark features: contract extraction, business briefing, recommendation
explanations and report narratives. Each validates model output and falls back to a clearly
labelled deterministic result when the model is unavailable."""
from __future__ import annotations

import json

from .. import config, db
from ..services import contracts, dashboard, insights
from ..services.common import now_iso
from . import meta_client
from .orchestrator import complete_json, complete_text, verify

CONTRACT_SYSTEM = """You are the Contract Agent. Extract structured metadata from the contract text. Pages are marked [PAGE n].
Return ONLY a JSON object with keys:
title (short descriptive), category (one of: """ + ", ".join(contracts.CATEGORIES) + """),
counterparty (the party other than the business), start_date (YYYY-MM-DD or null), end_date (YYYY-MM-DD or null),
value (number or null), value_period ("month"|"quarter"|"year"|"one-off"|null), payment_terms (string or null),
auto_renew (boolean), notice_days (integer or null), termination_clause (string or null),
summary (2-3 plain-English sentences), key_clauses (array of {type, text (short paraphrase or quote under 25 words), page (integer)}).
Only use information present in the text; use null when absent. This is not legal advice."""


def extract_contract(biz: int, pages: list[str], filename: str) -> dict:
    if config.ai_mode() != "real":
        meta = contracts.heuristic_extract(pages, filename)
        meta["extraction_source"] = "heuristic (mock mode)"
        return meta
    text = "\n\n".join(f"[PAGE {i}]\n{p[:6000]}" for i, p in enumerate(pages[:30], 1))
    try:
        meta = complete_json(CONTRACT_SYSTEM, f"Filename: {filename}\n\n{text}", biz=biz, agent="Contract Agent")
        meta["extraction_source"] = f"Muse Spark ({config.META_MODEL})"
        return meta
    except (meta_client.MetaAPIError, ValueError, json.JSONDecodeError) as e:
        meta = contracts.heuristic_extract(pages, filename)
        meta["extraction_source"] = f"heuristic fallback ({str(e)[:80]})"
        return meta


BRIEFING_SYSTEM = """You are BusinessPilot's Business Intelligence Agent writing "Your Business at a Glance" for a small business owner.
Use ONLY the figures in the JSON facts; do not compute new totals or percentages. Write 4-6 short sentences (plain text, no headings):
sales performance, stock situation, upcoming payroll and bills, cash outlook, and end with "Recommended priority: ..." naming the 2-3 most important actions.
Label estimates as estimates. Use the currency code given."""


def briefing(conn, biz: int, force: bool = False) -> dict:
    cached = db.one(conn, "SELECT * FROM briefings WHERE business_id=?", (biz,))
    facts = dashboard.briefing_facts(conn, biz)
    facts_key = json.dumps(facts, sort_keys=True, default=str)
    if cached and not force and json.dumps(cached["facts"], sort_keys=True, default=str) == facts_key:
        return cached
    source = "mock"
    content = None
    if config.ai_mode() == "real":
        try:
            content = complete_text(BRIEFING_SYSTEM, json.dumps(facts, default=str), biz=biz, agent="Business Intelligence Agent")
            source = f"Muse Spark ({config.META_MODEL})"
        except meta_client.MetaAPIError as e:
            source = f"template fallback ({str(e)[:60]})"
    if not content:
        content = _template_briefing(facts)
        source = source if source != "mock" else "template (mock mode)"
    ver = verify(content, [{"result": facts}])
    row = {"business_id": biz, "content": content, "facts": json.dumps(facts, default=str), "source": source,
           "verification": json.dumps(ver), "generated_at": now_iso()}
    conn.execute("DELETE FROM briefings WHERE business_id=?", (biz,))
    db.insert(conn, "briefings", row)
    return db.one(conn, "SELECT * FROM briefings WHERE business_id=?", (biz,))


def _template_briefing(f: dict) -> str:
    c = f["currency"]
    m = f["month_to_date"]
    ch = m["revenue_change_pct_vs_same_days_last_month"]
    s = [f"Over the last 30 days the business recorded {c} {m['revenue']:,.2f} in revenue"
         + (f" ({ch:+.1f}% vs the previous 30 days)" if ch is not None else "")
         + f" and {c} {m['expenses']:,.2f} in expenses."]
    if f["low_stock"]:
        s.append(f"{len(f['low_stock'])} product(s) are below the stock threshold: "
                 + ", ".join(f"{x['product']} ({x['stock_pct']}%)" for x in f["low_stock"])
                 + (f"; restocking is estimated at {c} {f['restock_estimated_cost']:,.2f}." if f["restock_estimated_cost"]
                    else "; replenishment orders are already in progress."))
    s.append(f"Payroll on {f['payroll']['date']} is estimated at {c} {f['payroll']['estimated_total_cost']:,.2f} including employer costs"
             + (f", with {len(f['payroll']['issues'])} data issue(s) to fix first." if f["payroll"]["issues"] else "."))
    if f["receivables"]["overdue_count"]:
        s.append(f"{f['receivables']['overdue_count']} customer invoice(s) totalling {c} {f['receivables']['overdue_total']:,.2f} are overdue.")
    s.append(f"Cash is {c} {f['cash_balance']:,.2f} today; after committed obligations the 30-day projection is "
             f"{c} {f['cash_forecast_30_days']['committed_end_balance']:,.2f} before new sales (estimate).")
    if f["top_recommendations"]:
        s.append("Recommended priority: " + "; ".join(r["title"] for r in f["top_recommendations"][:3]) + ".")
    return " ".join(s)


EXPLAIN_SYSTEM = """You are BusinessPilot's Business Intelligence Agent. Explain this recommendation to a busy small business owner in 2-4 sentences:
why it matters, what to do first, and any trade-off. Use only figures present in the JSON. Not financial, legal or tax advice."""


def explain_recommendation(conn, biz: int, rec_id: int) -> dict:
    r = db.get_owned(conn, "recommendations", rec_id, biz)
    if not r:
        from ..web import not_found
        raise not_found("Recommendation")
    payload = {k: r[k] for k in ("title", "category", "description", "supporting_data", "impact", "suggested_action", "priority")}
    if config.ai_mode() == "real":
        try:
            text = complete_text(EXPLAIN_SYSTEM, json.dumps(payload, default=str), biz=biz, agent="Business Intelligence Agent")
            source = f"Muse Spark ({config.META_MODEL})"
        except meta_client.MetaAPIError as e:
            text, source = f"{r['description']} {r['suggested_action']}", f"fallback ({str(e)[:60]})"
    else:
        text, source = f"{r['description']} Suggested next step: {r['suggested_action']} ({r['impact']})", "template (mock mode)"
    ver = verify(text, [{"result": payload}])
    db.update(conn, "recommendations", rec_id, {"ai_explanation": json.dumps({"text": text, "source": source, "verification": ver})}, biz)
    return {"text": text, "source": source, "verification": ver}


NARRATIVE_SYSTEM = """You are BusinessPilot's Business Intelligence Agent writing a short management summary (one paragraph of 3-5 sentences
followed by up to 3 bullet points of actions) for the report data provided. Use only the figures in the JSON."""


def report_narrative(biz: int, title: str, data: dict) -> tuple[str, str]:
    if config.ai_mode() == "real":
        try:
            return complete_text(NARRATIVE_SYSTEM, json.dumps({"title": title, **data}, default=str)[:20000], biz=biz,
                                 agent="Business Intelligence Agent"), f"Muse Spark ({config.META_MODEL})"
        except meta_client.MetaAPIError as e:
            src = f"template fallback ({str(e)[:60]})"
    else:
        src = "template (mock mode)"
    k = data.get("kpis") or {}
    parts = [f"{key.replace('_', ' ').capitalize()}: {v:,.2f}" if isinstance(v, float) else f"{key.replace('_', ' ').capitalize()}: {v}"
             for key, v in k.items()]
    text = f"{title}. " + "; ".join(parts) + "."
    if data.get("recommendations"):
        text += "\n" + "\n".join(f"- {r['title']}: {r['action']}" for r in data["recommendations"][:3])
    return text, src


def refresh_insights(conn, biz: int) -> dict:
    return insights.refresh(conn, biz)
