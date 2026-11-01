-- BusinessPilot schema (SQLite). Every business-owned table carries business_id.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- Business & access ----------
CREATE TABLE IF NOT EXISTS businesses (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  business_type TEXT NOT NULL DEFAULT 'Retail',
  currency TEXT NOT NULL DEFAULT 'USD',
  jurisdiction TEXT NOT NULL DEFAULT 'GENERIC',
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS business_members (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('OWNER','MANAGER','HR','FINANCE','INVENTORY','STAFF')),
  employee_id INTEGER,
  UNIQUE (business_id, user_id)
);

CREATE TABLE IF NOT EXISTS business_settings (
  business_id INTEGER PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
  low_stock_pct REAL NOT NULL DEFAULT 20,
  urgent_stock_pct REAL NOT NULL DEFAULT 10,
  critical_stock_pct REAL NOT NULL DEFAULT 5,
  auto_po_enabled INTEGER NOT NULL DEFAULT 0,
  auto_approve_limit REAL NOT NULL DEFAULT 0,
  manager_spending_limit REAL NOT NULL DEFAULT 2000,
  payroll_day INTEGER NOT NULL DEFAULT 28,
  contract_reminder_days TEXT NOT NULL DEFAULT '60,30,7,0',
  invoice_overdue_days INTEGER NOT NULL DEFAULT 30,
  forecast_window_days INTEGER NOT NULL DEFAULT 28,
  safety_stock_days INTEGER NOT NULL DEFAULT 3,
  budgets TEXT NOT NULL DEFAULT '{}'
);

-- ---------- Inventory ----------
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  sku TEXT NOT NULL,
  name TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'General',
  unit TEXT NOT NULL DEFAULT 'unit',
  current_stock REAL NOT NULL DEFAULT 0 CHECK (current_stock >= 0),
  max_stock REAL NOT NULL CHECK (max_stock > 0),
  unit_cost REAL NOT NULL DEFAULT 0,
  unit_price REAL NOT NULL DEFAULT 0,
  preferred_supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
  active INTEGER NOT NULL DEFAULT 1,
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (business_id, sku)
);
CREATE INDEX IF NOT EXISTS ix_products_biz ON products(business_id);

CREATE TABLE IF NOT EXISTS inventory_movements (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('sale','restock','adjustment','waste')),
  quantity REAL NOT NULL,
  note TEXT,
  ref TEXT,
  occurred_on TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_moves_prod ON inventory_movements(business_id, product_id, occurred_on);

CREATE TABLE IF NOT EXISTS stock_alerts (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  level TEXT NOT NULL CHECK (level IN ('LOW','URGENT','CRITICAL','OUT')),
  stock_pct REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_biz ON stock_alerts(business_id, status);

-- ---------- Suppliers & procurement ----------
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  contact_name TEXT,
  email TEXT,
  phone TEXT,
  payment_terms TEXT DEFAULT 'Net 30',
  reliability REAL NOT NULL DEFAULT 4.0 CHECK (reliability BETWEEN 0 AND 5),
  is_preferred INTEGER NOT NULL DEFAULT 0,
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_suppliers_biz ON suppliers(business_id);

CREATE TABLE IF NOT EXISTS supplier_products (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  unit_price REAL NOT NULL CHECK (unit_price >= 0),
  min_order_qty REAL NOT NULL DEFAULT 1,
  lead_time_days INTEGER NOT NULL DEFAULT 3,
  previous_unit_price REAL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (supplier_id, product_id)
);

CREATE TABLE IF NOT EXISTS purchase_orders (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  po_number TEXT NOT NULL,
  supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN
    ('DRAFT','AWAITING_APPROVAL','APPROVED','SUBMITTED','PARTIALLY_RECEIVED','COMPLETED','CANCELLED')),
  total REAL NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'manual',
  notes TEXT,
  expected_date TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  approved_at TEXT, approved_by TEXT,
  submitted_at TEXT, received_at TEXT,
  UNIQUE (business_id, po_number)
);
CREATE INDEX IF NOT EXISTS ix_po_biz ON purchase_orders(business_id, status);

CREATE TABLE IF NOT EXISTS purchase_order_items (
  id INTEGER PRIMARY KEY,
  po_id INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity REAL NOT NULL CHECK (quantity > 0),
  unit_price REAL NOT NULL,
  line_total REAL NOT NULL,
  received_qty REAL NOT NULL DEFAULT 0
);

-- ---------- Finance ----------
CREATE TABLE IF NOT EXISTS financial_accounts (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  opening_balance REAL NOT NULL DEFAULT 0,
  opening_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  direction TEXT NOT NULL CHECK (direction IN ('INCOME','EXPENSE')),
  category TEXT NOT NULL,
  amount REAL NOT NULL CHECK (amount > 0),
  description TEXT,
  txn_date TEXT NOT NULL,
  payment_method TEXT DEFAULT 'Bank transfer',
  counterparty TEXT,
  status TEXT NOT NULL DEFAULT 'PAID' CHECK (status IN ('PAID','PENDING')),
  document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  ref TEXT,
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_txn_biz_date ON transactions(business_id, txn_date);

CREATE TABLE IF NOT EXISTS invoices (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('RECEIVABLE','PAYABLE')),
  number TEXT NOT NULL,
  counterparty TEXT NOT NULL,
  amount REAL NOT NULL CHECK (amount > 0),
  issue_date TEXT NOT NULL,
  due_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','PAID','VOID')),
  paid_date TEXT,
  ref TEXT,
  is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_inv_biz ON invoices(business_id, kind, status);

CREATE TABLE IF NOT EXISTS recurring_expenses (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  amount REAL NOT NULL,
  day_of_month INTEGER NOT NULL CHECK (day_of_month BETWEEN 1 AND 28),
  active INTEGER NOT NULL DEFAULT 1
);

-- ---------- HR & payroll ----------
CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  emp_code TEXT NOT NULL,
  name TEXT NOT NULL,
  job_title TEXT,
  department TEXT,
  employment_type TEXT NOT NULL CHECK (employment_type IN ('MONTHLY','HOURLY')),
  start_date TEXT,
  base_salary REAL NOT NULL DEFAULT 0,
  hourly_rate REAL NOT NULL DEFAULT 0,
  allowance REAL NOT NULL DEFAULT 0,
  standard_hours REAL NOT NULL DEFAULT 44,
  leave_entitlement REAL NOT NULL DEFAULT 14,
  payment_details_on_file INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','INACTIVE')),
  is_demo INTEGER NOT NULL DEFAULT 0,
  UNIQUE (business_id, emp_code)
);

CREATE TABLE IF NOT EXISTS attendance_records (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  period TEXT NOT NULL,
  hours_worked REAL NOT NULL DEFAULT 0,
  overtime_hours REAL NOT NULL DEFAULT 0,
  bonus REAL NOT NULL DEFAULT 0,
  UNIQUE (employee_id, period)
);

CREATE TABLE IF NOT EXISTS leave_records (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  days REAL NOT NULL,
  leave_type TEXT NOT NULL DEFAULT 'Annual',
  paid INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'APPROVED'
);

CREATE TABLE IF NOT EXISTS payroll_rules (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  jurisdiction TEXT NOT NULL DEFAULT 'GENERIC',
  kind TEXT NOT NULL CHECK (kind IN ('EMPLOYEE_PCT','EMPLOYER_PCT','EMPLOYEE_FIXED','EMPLOYER_FIXED')),
  rate REAL NOT NULL,
  wage_ceiling REAL,
  applies_to TEXT NOT NULL DEFAULT 'ALL',
  version TEXT NOT NULL DEFAULT 'v1',
  effective_from TEXT,
  verified INTEGER NOT NULL DEFAULT 0,
  source_note TEXT,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS payroll_runs (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  period TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','AWAITING_APPROVAL','APPROVED','PAID','CANCELLED')),
  total_gross REAL NOT NULL DEFAULT 0,
  total_deductions REAL NOT NULL DEFAULT 0,
  total_net REAL NOT NULL DEFAULT 0,
  total_employer_cost REAL NOT NULL DEFAULT 0,
  anomalies TEXT NOT NULL DEFAULT '[]',
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  approved_at TEXT, approved_by TEXT, paid_at TEXT
);

CREATE TABLE IF NOT EXISTS payroll_items (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
  employee_id INTEGER NOT NULL REFERENCES employees(id),
  basic REAL NOT NULL, allowances REAL NOT NULL, overtime REAL NOT NULL, bonus REAL NOT NULL,
  unpaid_leave_deduction REAL NOT NULL,
  gross REAL NOT NULL, deductions REAL NOT NULL, net REAL NOT NULL, employer_contrib REAL NOT NULL,
  breakdown TEXT NOT NULL DEFAULT '{}'
);

-- ---------- Contracts & documents ----------
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  mime TEXT,
  storage_path TEXT,
  text TEXT,
  pages TEXT NOT NULL DEFAULT '[]',
  uploaded_by TEXT,
  uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contracts (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'Other',
  counterparty TEXT,
  start_date TEXT,
  end_date TEXT,
  value REAL,
  value_period TEXT,
  payment_terms TEXT,
  auto_renew INTEGER NOT NULL DEFAULT 0,
  notice_days INTEGER,
  termination_clause TEXT,
  summary TEXT,
  key_clauses TEXT NOT NULL DEFAULT '[]',
  extraction_source TEXT DEFAULT 'manual',
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contract_reminders (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
  remind_on TEXT NOT NULL,
  label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SENT','DISMISSED')),
  UNIQUE (contract_id, remind_on, label)
);

-- ---------- AI, automation, governance ----------
CREATE TABLE IF NOT EXISTS agent_sessions (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  title TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_messages (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT,
  meta TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_events (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL,
  agent TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT,
  data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_events_session ON agent_events(session_id, id);

CREATE TABLE IF NOT EXISTS ai_requests (
  id INTEGER PRIMARY KEY,
  business_id INTEGER,
  run_id TEXT,
  agent TEXT,
  model TEXT,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  latency_ms INTEGER,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS automation_rules (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT,
  trigger_type TEXT NOT NULL,
  params TEXT NOT NULL DEFAULT '{}',
  actions TEXT NOT NULL DEFAULT '[]',
  approval_required INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS automation_runs (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  rule_id INTEGER REFERENCES automation_rules(id) ON DELETE SET NULL,
  rule_name TEXT,
  status TEXT NOT NULL,
  summary TEXT,
  details TEXT NOT NULL DEFAULT '[]',
  started_at TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_autoruns_biz ON automation_runs(business_id, id);

CREATE TABLE IF NOT EXISTS recommendations (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  fingerprint TEXT NOT NULL,
  title TEXT NOT NULL,
  category TEXT NOT NULL,
  description TEXT NOT NULL,
  supporting_data TEXT NOT NULL DEFAULT '{}',
  impact TEXT,
  priority TEXT NOT NULL CHECK (priority IN ('HIGH','MEDIUM','LOW')),
  suggested_action TEXT,
  estimated_benefit REAL,
  confidence TEXT NOT NULL DEFAULT 'HIGH',
  module TEXT NOT NULL,
  agent TEXT NOT NULL,
  ai_explanation TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','ACCEPTED','REJECTED','COMPLETED')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (business_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS approval_requests (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('PURCHASE_ORDER','PAYROLL','PAYROLL_PAYMENT','INVOICE_REMINDER')),
  ref_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  summary TEXT,
  amount REAL,
  requested_by TEXT NOT NULL,
  required_role TEXT NOT NULL DEFAULT 'OWNER',
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPROVED','REJECTED','CANCELLED')),
  decided_by TEXT,
  decided_at TEXT,
  reason TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_approvals_biz ON approval_requests(business_id, status);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('USER','AGENT','AUTOMATION','SYSTEM')),
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  entity TEXT,
  entity_id INTEGER,
  details TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_audit_biz ON audit_logs(business_id, id);

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY,
  business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  period TEXT,
  data TEXT NOT NULL DEFAULT '{}',
  narrative TEXT,
  narrative_source TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS briefings (
  business_id INTEGER PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  facts TEXT NOT NULL,
  source TEXT NOT NULL,
  verification TEXT NOT NULL DEFAULT '{}',
  generated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
