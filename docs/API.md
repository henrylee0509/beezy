# REST API

All endpoints are under `/api`, take and return JSON, and (except auth and health) need `Authorization: Bearer <token>`. Errors look like `{"error": {"code", "message", "details"}}` with 400/401/403/404/413/422/429.

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register` · `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` · `POST /auth/switch` |
| Business | `GET /business` · `PUT /business/settings` · `POST /business/reset-demo` |
| Dashboard | `GET /dashboard/overview` · `GET /dashboard/alerts` · `GET /dashboard/briefing[?refresh=1]` |
| Inventory | `GET /inventory` · `POST /inventory` · `PUT /inventory/{id}` · `DELETE /inventory/{id}` · `GET /inventory/low-stock` · `POST /inventory/{id}/adjust` · `GET /inventory/{id}/forecast` · `GET /inventory/{id}/history` · `GET /inventory/{id}/quotes` |
| Procurement | `GET /suppliers` · `POST /suppliers` · `GET /suppliers/{id}` · `POST /suppliers/{id}/products` · `GET /purchase-orders` · `POST /purchase-orders` · `GET /purchase-orders/{id}` · `POST /purchase-orders/{id}/approve` · `POST /purchase-orders/{id}/receive` · `POST /purchase-orders/{id}/cancel` · `GET /procurement/restock-plan` · `POST /procurement/draft-restock` |
| Finance | `GET /finance/summary?period=last30\|YYYY-MM` · `GET /finance/transactions` · `POST /finance/transactions` · `PUT /finance/transactions/{id}` · `POST /finance/transactions/{id}/receipt` · `GET /finance/cash-flow` · `GET /finance/forecast?days=` · `GET /finance/invoices` · `POST /finance/invoices` · `POST /finance/invoices/{id}/pay` · `GET /finance/meta` |
| HR & payroll | `GET /employees` · `POST /employees` · `GET/PUT /employees/{id}` · `POST /employees/{id}/attendance` · `POST /employees/{id}/leave` · `GET /payroll` · `POST /payroll/prepare` · `GET /payroll/{id}` · `POST /payroll/{id}/approve` · `POST /payroll/{id}/pay` · `GET /payroll/{id}/payslips` · `POST /payroll/rules` · `PUT /payroll/rules/{id}` · `POST /payroll/rules/template` |
| Contracts | `GET /contracts` · `POST /contracts/upload` (multipart `file`) · `GET /contracts/expiring?days=` · `GET /contracts/search?q=` · `GET/PUT /contracts/{id}` · `GET /contracts/{id}/summary` · `GET /documents/{id}` · `GET /documents/{id}/file` |
| Assistant | `POST /assistant/chat` `{message, session_id?}` → `{session_id, run_id}` · `GET /assistant/sessions/{id}/events?after=&run_id=` · `GET /assistant/sessions` · `GET /assistant/sessions/{id}` · `POST /assistant/voice` (multipart `audio`, WAV) · `GET /assistant/status` · `GET /ai/requests` |
| Insights | `GET /insights[?refresh=1]` · `POST /insights/refresh` · `PUT /insights/{id}` `{status}` · `POST /insights/{id}/explain` |
| Automations | `GET /automations` · `POST /automations` · `PUT /automations/{id}` · `POST /automations/{id}/toggle` · `POST /automations/{id}/run` · `POST /automations/run-all` · `GET /automations/history` |
| Approvals | `GET /approvals?status=PENDING\|ALL` · `POST /approvals/{id}/approve` · `POST /approvals/{id}/reject` |
| Reports & audit | `GET /reports` · `POST /reports/generate` `{kind, period?, ai_summary}` · `GET /reports/{id}` · `GET /reports/{id}/export?format=pdf\|csv` · `GET /audit` |
| Health | `GET /health` |

## Agent tools (what Muse Spark can call)

Inventory: `get_all_inventory`, `get_low_stock_products`, `get_inventory`, `get_stock_history`, `forecast_product_demand`, `get_supplier_for_product`
Procurement: `get_suppliers`, `compare_supplier_quotes`, `get_restock_plan`, `get_purchase_orders`, `get_supplier_performance`, **`draft_restock_purchase_orders`**, **`create_purchase_order`**
Finance: `get_financial_summary`, `get_transactions`, `get_accounts_receivable`, `get_accounts_payable`, `forecast_cash_balance`, `get_upcoming_obligations`, `get_spending_anomalies`, **`record_transaction`**
HR & Payroll: `get_employees`, `estimate_payroll`, `identify_payroll_anomalies`, `get_payroll_runs`, **`prepare_payroll`**
Contracts: `get_contracts`, `get_expiring_contracts`, `get_contract_details`, `search_contracts`
Business Intelligence: `get_business_health_snapshot`, `get_recommendations`, `evaluate_affordability`

**Bold** = action tools; they create drafts or records and never bypass the approval queue. Definitions: [`backend/app/ai/tools.py`](../backend/app/ai/tools.py).
