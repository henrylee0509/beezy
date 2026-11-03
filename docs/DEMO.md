# 3-minute demo script

> The finished, edited video is produced by the pipeline in [`demo/`](../demo/README.md), with a timed voice-over script in [`demo/VOICEOVER.md`](../demo/VOICEOVER.md). The table below is for presenting live instead.

**Before recording:** set `MODEL_API_KEY` in `.env`, start the app, sign in as the owner, go to Settings → *Reset demo data*. Optionally set `META_REASONING_EFFORT=low` for snappier turns. Keep `sample_docs/FreshBake_Pastry_Supply_Agreement_DEMO.pdf` handy.

| Time | Show | Say (suggested) |
|---|---|---|
| 0:00–0:20 | Login screen → Overview | "Mia runs a cafe. Inventory, payroll, suppliers, contracts and cash live in five different places. BusinessPilot gives her a team of AI departments built on Meta's Muse Spark." |
| 0:20–0:40 | Overview: Muse Spark briefing (point at "figures verified"), alerts, stock-level chart | "Every morning the Business Intelligence agent writes this briefing — and every figure is checked against the database." |
| 0:40–1:30 | Click **Run a health check** → Assistant. Let the operations floor light up | "The Executive Assistant decides which departments it needs and consults them in parallel. Each is its own Muse Spark agent with real tools — you're watching actual tool calls." Point at the plan event and the verifier line. |
| 1:30–2:00 | Ask (or say via the mic): *"Restock everything below 20%, but make sure it arrives before we run out."* | "Harbour is cheapest for coffee beans, but it can't deliver before we run out in four days, so the Procurement agent picks Highland." Approve the small order inline; show the coffee-beans order needs the owner because it's over the manager limit. |
| 2:00–2:20 | *"Prepare payroll for this month."* → open Payroll page | "Pay is calculated in code, not by the model. The HR agent flagged that Sam has no hours and Nur has no bank details — before payday, not after." |
| 2:20–2:40 | Contracts → upload the FreshBake PDF | "The Contract agent reads the PDF, extracts the term, payment terms and the 45-day notice clause with page numbers, and schedules reminders." |
| 2:40–3:00 | Activity log → AI requests tab, then Approvals | "Every agent action and every Muse Spark call is logged. Agents prepare the work — the owner stays in control of the money." |

## Good prompts

- Can I afford to restock inventory and pay my employees next month?
- What are the three biggest problems I should address this week?
- Which contracts need a decision in the next 30 days, and what are the payment terms with Highland Roasters? Cite pages.
- Find the cheapest supplier for our low-stock products, but make sure it arrives before we run out.
- Record an expense of 180 for espresso machine repair today. *(action: records a transaction)*

## Live automation moment

Inventory → Oat Milk → **Adjust** → Sale of 50. Stock crosses 20% and the *Automatic Restock Preparation* rule drafts a purchase order immediately (toast + Approvals badge).
