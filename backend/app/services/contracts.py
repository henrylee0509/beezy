"""Contracts & documents: text extraction, metadata, expiry tracking and reminders."""
from __future__ import annotations

import io
import json
import re
from datetime import timedelta

from .. import db
from ..web import bad_request, not_found
from .common import actor_type_for, audit, iso, now_iso, parse_date, settings, today

CATEGORIES = ["Supplier Agreement", "Rental Agreement", "Employment Contract", "Service Agreement", "NDA",
              "Customer Contract", "Purchase Agreement", "Licence / Permit", "Insurance", "Other"]

DISCLAIMER = ("Summaries highlight clauses for convenience and are not legal advice. "
              "Check the source document or a qualified professional before acting.")


def extract_text(filename: str, data: bytes) -> list[str]:
    """Return a list of page texts. PDF pages are real pages; DOCX/TXT are split into ~3,000-char sections."""
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return [(p.extract_text() or "").strip() for p in reader.pages]
    if name.endswith(".docx"):
        import docx
        d = docx.Document(io.BytesIO(data))
        text = "\n".join(p.text for p in d.paragraphs)
    elif name.endswith((".txt", ".md")):
        text = data.decode("utf-8", errors="replace")
    else:
        raise bad_request("Supported formats: PDF, DOCX, TXT")
    chunks, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) > 3000 and cur:
            chunks.append(cur.strip())
            cur = ""
        cur += para + "\n"
    if cur.strip():
        chunks.append(cur.strip())
    return chunks or [""]


_DATE_PATTERNS = [
    (r"(\d{4}-\d{2}-\d{2})", lambda m: m.group(1)),
    (r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
     lambda m: f"{m.group(3)}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"),
    (r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
     lambda m: f"{m.group(3)}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"),
]
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                        "september", "october", "november", "december"], 1)}


def _find_date_near(text: str, keywords: list[str]) -> tuple[str | None, int | None]:
    for kw in keywords:
        for km in re.finditer(kw, text, re.I):
            window = text[km.start(): km.start() + 160]
            for pat, fmt in _DATE_PATTERNS:
                m = re.search(pat, window, re.I)
                if m:
                    return fmt(m), km.start()
    return None, None


def heuristic_extract(pages: list[str], filename: str) -> dict:
    """Rule-based fallback extractor (used in mock mode or if the AI call fails). Cites pages it used."""
    full = "\n".join(pages)

    def page_of(pos):
        if pos is None:
            return None
        acc = 0
        for i, p in enumerate(pages, 1):
            acc += len(p) + 1
            if pos < acc:
                return i
        return len(pages)

    low = full.lower()
    cat = "Other"
    for c, kws in [("Supplier Agreement", ["supply agreement", "supplier agreement", "supply of goods"]),
                   ("Rental Agreement", ["lease", "tenancy", "rental"]),
                   ("Employment Contract", ["employment", "employee shall"]),
                   ("Service Agreement", ["service agreement", "maintenance services", "services agreement"]),
                   ("NDA", ["non-disclosure", "confidentiality agreement"]),
                   ("Insurance", ["insurance policy", "insured"])]:
        if any(k in low for k in kws):
            cat = c
            break
    start, sp = _find_date_near(full, [r"commenc", r"effective", r"start date", r"dated"])
    end, ep = _find_date_near(full, [r"expir", r"terminat\w+ on", r"end date", r"until", r"ending on"])
    party = None
    m = re.search(r"between\s+(.+?)\s+(?:\(|and)\s", full, re.I | re.S)
    m2 = re.search(r"\band\s+([A-Z][\w&.,' -]{2,60}?)\s*\(", full)
    if m2:
        party = m2.group(1).strip(" ,")
    elif m:
        party = m.group(1).strip()[:80]
    auto = bool(re.search(r"automatic(ally)?\s+renew", full, re.I))
    notice = re.search(r"(\d{1,3})\s*(?:\(\w+\)\s*)?days'?\s*(?:prior\s+)?(?:written\s+)?notice", full, re.I)
    pay = (re.search(r"payment terms? (?:are|is|of|:)[^.\n]{3,140}", full, re.I)
           or re.search(r"net\s*\d+[^.\n]{0,80}", full, re.I)
           or re.search(r"payment terms?[^.\n]{3,140}", full, re.I))
    val = re.search(r"(?:S?\$|USD|SGD)\s?([\d,]+(?:\.\d{2})?)\s*(per|a|each)?\s*(month|year|quarter|annum)?", full, re.I)
    term = re.search(r"(terminat[^.]{10,220}\.)", full, re.I)
    clauses = []
    if auto:
        p = re.search(r"automatic(ally)?\s+renew", full, re.I)
        clauses.append({"type": "Auto-renewal", "text": full[p.start():p.start() + 200].split("\n")[0], "page": page_of(p.start())})
    if notice:
        clauses.append({"type": "Notice period", "text": notice.group(0), "page": page_of(notice.start())})
    if pay:
        clauses.append({"type": "Payment terms", "text": pay.group(0).strip(), "page": page_of(pay.start())})
    if term:
        clauses.append({"type": "Termination", "text": term.group(1).strip()[:220], "page": page_of(term.start())})
    value = float(val.group(1).replace(",", "")) if val else None
    return {
        "title": re.sub(r"\.(pdf|docx|txt)$", "", filename, flags=re.I).replace("_", " ").title(),
        "category": cat, "counterparty": party, "start_date": start, "end_date": end,
        "start_date_page": page_of(sp), "end_date_page": page_of(ep),
        "value": value, "value_period": (val.group(3).lower() if val and val.group(3) else None),
        "payment_terms": pay.group(0).strip()[:200] if pay else None, "auto_renew": auto,
        "notice_days": int(notice.group(1)) if notice else None,
        "termination_clause": term.group(1).strip()[:300] if term else None,
        "key_clauses": clauses,
        "summary": f"{cat} with {party or 'an unidentified party'}"
                   + (f", running {start} to {end}" if start and end else "")
                   + (". Renews automatically unless notice is given" if auto else "") + ".",
        "extraction_source": "heuristic",
    }


def validate_extraction(meta: dict) -> dict:
    """Never trust model output blindly: coerce types and drop invalid dates."""
    clean = dict(meta)
    for k in ("start_date", "end_date"):
        d = parse_date(clean.get(k))
        clean[k] = iso(d) if d else None
    if clean.get("start_date") and clean.get("end_date") and clean["end_date"] < clean["start_date"]:
        clean["end_date"] = None
    if clean.get("category") not in CATEGORIES:
        clean["category"] = "Other"
    try:
        clean["value"] = float(clean["value"]) if clean.get("value") not in (None, "") else None
    except (TypeError, ValueError):
        clean["value"] = None
    try:
        clean["notice_days"] = int(clean["notice_days"]) if clean.get("notice_days") not in (None, "") else None
    except (TypeError, ValueError):
        clean["notice_days"] = None
    clean["auto_renew"] = bool(clean.get("auto_renew"))
    clauses = clean.get("key_clauses") or []
    clean["key_clauses"] = [c for c in clauses if isinstance(c, dict) and c.get("text")][:12]
    return clean


def store_document(conn, biz: int, filename: str, mime: str, path: str, pages: list[str], actor: str) -> int:
    return db.insert(conn, "documents", {"business_id": biz, "filename": filename, "mime": mime, "storage_path": path,
                                          "text": "\n\n".join(pages), "pages": json.dumps(pages), "uploaded_by": actor})


def create_contract(conn, biz: int, meta: dict, document_id: int | None, actor: str) -> dict:
    meta = validate_extraction(meta)
    cid = db.insert(conn, "contracts", {
        "business_id": biz, "document_id": document_id, "title": meta.get("title") or "Untitled contract",
        "category": meta["category"], "counterparty": meta.get("counterparty"), "start_date": meta.get("start_date"),
        "end_date": meta.get("end_date"), "value": meta.get("value"), "value_period": meta.get("value_period"),
        "payment_terms": meta.get("payment_terms"), "auto_renew": int(meta["auto_renew"]),
        "notice_days": meta.get("notice_days"), "termination_clause": meta.get("termination_clause"),
        "summary": meta.get("summary"), "key_clauses": json.dumps(meta["key_clauses"]),
        "extraction_source": meta.get("extraction_source", "manual")})
    sync_reminders(conn, biz, cid)
    audit(conn, biz, actor_type_for(actor), actor, "contract.create", "contract", cid,
          {"title": meta.get("title"), "source": meta.get("extraction_source")})
    return get_contract(conn, biz, cid)


def _decorate(c: dict) -> dict:
    t = today()
    end = parse_date(c.get("end_date"))
    c["days_to_expiry"] = (end - t).days if end else None
    if c.get("auto_renew") and end and c.get("notice_days"):
        nd = end - timedelta(days=c["notice_days"])
        c["notice_deadline"] = iso(nd)
        c["days_to_notice_deadline"] = (nd - t).days
    else:
        c["notice_deadline"] = None
        c["days_to_notice_deadline"] = None
    if end and end < t:
        c["renewal_status"] = "Auto-renewed / check" if c.get("auto_renew") else "Expired"
    elif c["days_to_expiry"] is not None and c["days_to_expiry"] <= 60:
        c["renewal_status"] = "Auto-renews soon" if c.get("auto_renew") else "Expiring soon"
    else:
        c["renewal_status"] = "Active"
    return c


def list_contracts(conn, biz: int) -> list[dict]:
    rows = db.all_(conn, """SELECT c.*, d.filename FROM contracts c LEFT JOIN documents d ON d.id=c.document_id
                            WHERE c.business_id=? ORDER BY CASE WHEN c.end_date IS NULL THEN 1 ELSE 0 END, c.end_date""", (biz,))
    pend = pending_actions(conn, biz)
    out = []
    for r in rows:
        c = _decorate(r)
        c["suggestions"] = suggestions(c, pend)
        out.append(c)
    return out


def get_contract(conn, biz: int, cid: int) -> dict:
    c = db.one(conn, """SELECT c.*, d.filename FROM contracts c LEFT JOIN documents d ON d.id=c.document_id
                        WHERE c.id=? AND c.business_id=?""", (cid, biz))
    if not c:
        raise not_found("Contract")
    c["reminders"] = db.all_(conn, "SELECT * FROM contract_reminders WHERE contract_id=? AND business_id=? ORDER BY remind_on", (cid, biz))
    c["disclaimer"] = DISCLAIMER
    c = _decorate(c)
    c["suggestions"] = suggestions(c, pending_actions(conn, biz))
    return c


def find_contract(conn, biz: int, ref) -> dict:
    if isinstance(ref, (int, float)) or (isinstance(ref, str) and ref.isdigit()):
        return get_contract(conn, biz, int(ref))
    r = db.one(conn, "SELECT id FROM contracts WHERE business_id=? AND (lower(title) LIKE ? OR lower(counterparty) LIKE ?)",
               (biz, f"%{str(ref).lower()}%", f"%{str(ref).lower()}%"))
    if not r:
        raise not_found(f"Contract '{ref}'")
    return get_contract(conn, biz, r["id"])


def document_pages(conn, biz: int, doc_id: int) -> dict:
    d = db.get_owned(conn, "documents", doc_id, biz)
    if not d:
        raise not_found("Document")
    return {"id": d["id"], "filename": d["filename"], "pages": d["pages"], "uploaded_at": d["uploaded_at"]}


def expiring(conn, biz: int, days: int = 30) -> list[dict]:
    return [c for c in list_contracts(conn, biz)
            if c["days_to_expiry"] is not None and 0 <= c["days_to_expiry"] <= days
            or (c["days_to_notice_deadline"] is not None and 0 <= c["days_to_notice_deadline"] <= days)]


def search(conn, biz: int, keyword: str) -> list[dict]:
    kw = f"%{keyword.lower()}%"
    rows = db.all_(conn, """SELECT c.id, c.title, c.counterparty, c.category, d.pages FROM contracts c
                            LEFT JOIN documents d ON d.id=c.document_id
                            WHERE c.business_id=? AND (lower(c.title) LIKE ? OR lower(c.counterparty) LIKE ?
                                  OR lower(c.summary) LIKE ? OR lower(COALESCE(d.text,'')) LIKE ?)""",
                   (biz, kw, kw, kw, kw))
    out = []
    for r in rows:
        hits = []
        for i, p in enumerate(r.pop("pages") or [], 1):
            pos = p.lower().find(keyword.lower())
            if pos >= 0:
                hits.append({"page": i, "excerpt": p[max(0, pos - 80): pos + 160].replace("\n", " ")})
        out.append({**r, "matches": hits[:5]})
    return out


def sync_reminders(conn, biz: int, cid: int | None = None) -> int:
    """Create reminders N days before expiry (configurable) and before auto-renewal notice deadlines."""
    s = settings(conn, biz)
    offsets = sorted({int(x) for x in str(s["contract_reminder_days"]).split(",") if x.strip().isdigit()}, reverse=True)
    rows = [get_contract(conn, biz, cid)] if cid else list_contracts(conn, biz)
    n = 0
    for c in rows:
        end = parse_date(c.get("end_date"))
        if not end:
            continue
        for off in offsets:
            d = end - timedelta(days=off)
            label = "Expires today" if off == 0 else f"{off} days before expiry"
            n += conn.execute("""INSERT OR IGNORE INTO contract_reminders(business_id, contract_id, remind_on, label)
                                 VALUES (?,?,?,?)""", (biz, c["id"], iso(d), label)).rowcount
        if c.get("notice_deadline"):
            nd = parse_date(c["notice_deadline"])
            for off, label in ((14, "Notice deadline in 14 days"), (0, "Last day to give non-renewal notice")):
                n += conn.execute("""INSERT OR IGNORE INTO contract_reminders(business_id, contract_id, remind_on, label)
                                     VALUES (?,?,?,?)""", (biz, c["id"], iso(nd - timedelta(days=off)), label)).rowcount
    return n


def upcoming_reminders(conn, biz: int, days: int = 30) -> list[dict]:
    return db.all_(conn, """SELECT r.*, c.title, c.counterparty, c.end_date FROM contract_reminders r
                            JOIN contracts c ON c.id=r.contract_id
                            WHERE r.business_id=? AND r.status='PENDING' AND r.remind_on<=? ORDER BY r.remind_on""",
                   (biz, iso(today() + timedelta(days=days))))


def due_reminders(conn, biz: int) -> list[dict]:
    return db.all_(conn, """SELECT r.*, c.title, c.counterparty, c.end_date FROM contract_reminders r
                            JOIN contracts c ON c.id=r.contract_id
                            WHERE r.business_id=? AND r.status='PENDING' AND r.remind_on<=? ORDER BY r.remind_on""",
                   (biz, iso(today())))


def update_contract(conn, biz: int, cid: int, data: dict, actor: str) -> dict:
    get_contract(conn, biz, cid)
    if "key_clauses" in data:
        data["key_clauses"] = json.dumps(data["key_clauses"])
    db.update(conn, "contracts", cid, data, biz)
    conn.execute("DELETE FROM contract_reminders WHERE contract_id=? AND status='PENDING'", (cid,))
    sync_reminders(conn, biz, cid)
    audit(conn, biz, actor_type_for(actor), actor, "contract.update", "contract", cid, {k: v for k, v in data.items() if k != "key_clauses"})
    return get_contract(conn, biz, cid)


# ---------------------------------------------------------------- decisions: extend, or end at expiry
ACTION_LABELS = {"EXTEND": "Extend", "END_AT_EXPIRY": "End at expiry"}


def _add_months(d, months: int):
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    import calendar
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def _nice(d) -> str:
    d = parse_date(d) if isinstance(d, str) else d
    return f"{d.day} {d.strftime('%b %Y')}" if d else ""


def term_months(c: dict) -> int:
    """Length of the current term in months (rounded), used as the default extension; 12 if unknown."""
    s, e = parse_date(c.get("start_date")), parse_date(c.get("end_date"))
    if not s or not e or e <= s:
        return 12
    return max(1, min(60, round((e - s).days / 30.44)))


def pending_actions(conn, biz: int) -> dict[int, dict]:
    rows = db.all_(conn, "SELECT * FROM contract_actions WHERE business_id=? AND status='PENDING'", (biz,))
    return {r["contract_id"]: r for r in rows}


def suggestions(c: dict, pending: dict | None = None) -> list[dict]:
    """Smart options for a contract that needs a decision soon (ends or has a notice deadline within 120 days)."""
    end = parse_date(c.get("end_date"))
    if not end or c.get("status") in ("ENDED",):
        return []
    days = c.get("days_to_expiry")
    nd = c.get("days_to_notice_deadline")
    if (days is None or days > 120) and (nd is None or nd > 120):
        return []
    months = term_months(c)
    new_end = _nice(_add_months(end, months))
    ending_note = (f"Notice goes before {_nice(c['notice_deadline'])}, so it does not auto-renew."
                   if c.get("auto_renew") and c.get("notice_deadline") else f"It finishes on {_nice(c['end_date'])} with no renewal.")
    opts = [
        {"action": "EXTEND", "months": months, "label": f"Extend {months} months",
         "detail": f"Same terms, new end date {new_end}.", "needs_approval": True},
        {"action": "END_AT_EXPIRY", "label": "End when it expires", "detail": ending_note, "needs_approval": True},
        {"action": "RENEGOTIATE", "label": "Renegotiate first",
         "detail": "Beezy prepares talking points from the contract and your spending.", "needs_approval": False},
    ]
    # Suggest extending the agreements that keep the business running; no suggestion for people or other decisions.
    rec = "EXTEND" if c.get("category") in ("Supplier Agreement", "Rental Agreement", "Service Agreement") else None
    for o in opts:
        o["recommended"] = o["action"] == rec
    if pending and c["id"] in pending:
        p = pending[c["id"]]
        for o in opts:
            o["pending"] = o["action"] == p["action"]
            o["approval_id"] = p.get("approval_id") if o["pending"] else None
    return opts


def propose_action(conn, biz: int, cid: int, action: str, actor: str, months: int | None = None, note: str = "") -> dict:
    from . import approvals
    if action not in ACTION_LABELS:
        raise bad_request("action must be EXTEND or END_AT_EXPIRY")
    c = get_contract(conn, biz, cid)
    existing = pending_actions(conn, biz).get(cid)
    if existing:
        if existing["action"] == action:
            return {"action_id": existing["id"], "approval_id": existing["approval_id"], "status": "PENDING", "existing": True}
        db.update(conn, "contract_actions", existing["id"], {"status": "REPLACED"}, biz)
        if existing.get("approval_id"):
            db.update(conn, "approval_requests", existing["approval_id"], {"status": "REJECTED", "reason": "Replaced by a newer decision",
                                                                           "decided_by": actor, "decided_at": now_iso()}, biz)
    end = parse_date(c.get("end_date"))
    if action == "EXTEND":
        months = int(months or term_months(c))
        if not 1 <= months <= 60:
            raise bad_request("months must be between 1 and 60")
        if not end:
            raise bad_request("This contract has no end date to extend")
        new_end = iso(_add_months(end, months))
        title = f"Extend {c['title']} by {months} months"
        value = (f" Value {c['value']:,.2f}" + (f" {c['value_period'].lower()}" if c.get("value_period") else "") + ".") if c.get("value") else ""
        summary = f"{c.get('counterparty') or 'Counterparty'}: new end date {_nice(new_end)} (from {_nice(c['end_date'])}), same terms.{value}"
    else:
        new_end = None
        title = f"Let {c['title']} end on {_nice(c['end_date'])}"
        summary = (f"Turn off auto-renewal and give notice to {c.get('counterparty') or 'the counterparty'}"
                   + (f" before {_nice(c['notice_deadline'])}." if c.get("notice_deadline") else "."))
    if note:
        summary += f" Note: {note[:300]}"
    act_id = db.insert(conn, "contract_actions", {"business_id": biz, "contract_id": cid, "action": action, "months": months,
                                                  "new_end_date": new_end, "note": note[:500] or None, "requested_by": actor})
    aid = approvals.create(conn, biz, "CONTRACT_ACTION", act_id, title, summary, None, actor, required_role="OWNER")
    db.update(conn, "contract_actions", act_id, {"approval_id": aid}, biz)
    audit(conn, biz, actor_type_for(actor), actor, "contract.decision.request", "contract", cid,
          {"action": action, "months": months, "approval_id": aid})
    return {"action_id": act_id, "approval_id": aid, "status": "PENDING", "title": title, "summary": summary}


def apply_action(conn, biz: int, act_id: int, actor: str, approve: bool) -> dict:
    act = db.get_owned(conn, "contract_actions", act_id, biz)
    if not act:
        raise not_found("Contract decision")
    if not approve:
        db.update(conn, "contract_actions", act_id, {"status": "REJECTED", "decided_by": actor}, biz)
        return {"contract_id": act["contract_id"], "status": "REJECTED"}
    if act["action"] == "EXTEND":
        update_contract(conn, biz, act["contract_id"], {"end_date": act["new_end_date"], "status": "ACTIVE"}, actor)
        msg = f"Contract extended to {act['new_end_date']}."
    else:
        update_contract(conn, biz, act["contract_id"], {"auto_renew": 0, "status": "ENDING"}, actor)
        msg = "Auto-renewal turned off; the contract ends at expiry. Send the notice to the counterparty (sending is simulated)."
    db.update(conn, "contract_actions", act_id, {"status": "APPROVED", "decided_by": actor}, biz)
    return {"contract_id": act["contract_id"], "status": "APPROVED", "message": msg}
