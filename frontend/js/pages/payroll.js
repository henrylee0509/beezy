import { api } from '../api.js';
import { refreshCounts } from '../app.js';
import { can, confirmBox, errToast, esc, field, fmt, icon, modal, on, skeleton, state, statusBadge, toast } from '../ui.js';

export async function render(el, params) {
  let tab = params.get('tab') || (can('salary.read') ? 'payroll' : 'employees');
  el.innerHTML = `<div class="page-head"><div><h1>Staff &amp; pay</h1><div class="lead">Pay is calculated by deterministic rules in the backend, never by the AI. Payroll needs owner approval, and payments here are recorded, not sent to a bank.</div></div>
    <div class="actions">${can('salary.read') ? `<a class="btn" href="#/assistant?q=${encodeURIComponent('Check payroll for the next pay date: how much do I need, is any information missing, and can I afford it?')}">${icon('sparkle')} Ask about payroll</a>` : ''}
    ${can('employees.write') ? `<button class="btn" id="addEmp">${icon('plus')} Add employee</button>` : ''}</div></div>
    <div class="tabs">${[['payroll', 'Payroll', 'salary.read'], ['employees', 'Employees', 'employees.read'], ['rules', 'Payroll rules', 'salary.read']].filter(t => can(t[2]))
      .map(([k, l]) => `<button data-tab="${k}" class="${tab === k ? 'on' : ''}">${l}</button>`).join('')}</div><div id="pane">${skeleton(5)}</div>`;
  const pane = el.querySelector('#pane');
  on(el, 'click', '[data-tab]', (e, b) => { tab = b.dataset.tab; el.querySelectorAll('[data-tab]').forEach(x => x.classList.toggle('on', x === b)); show(); });

  let emps = [];
  async function employees() {
    const r = await api.get('/api/employees');
    emps = r.employees;
    pane.innerHTML = `${!r.salary_visible ? '<p class="small muted">Salary details are hidden for your role.</p>' : ''}<section class="panel"><div class="panel-body table-wrap"><table class="t"><thead><tr>
      <th>ID</th><th>Name</th><th>Job title</th><th>Type</th><th class="right">Basic salary / rate</th><th class="right">Allowance</th><th>Payment details</th><th>Status</th><th></th></tr></thead><tbody>
      ${emps.map(e => `<tr><td class="muted">${esc(e.emp_code)}</td><td class="strong"><a href="#" data-emp="${e.id}">${esc(e.name)}</a></td><td>${esc(e.job_title || '')}<div class="small muted">${esc(e.department || '')}</div></td>
        <td>${e.employment_type === 'MONTHLY' ? 'Monthly' : 'Hourly'}</td>
        <td class="right num">${e.salary_hidden ? '<span class="muted">Hidden</span>' : e.employment_type === 'MONTHLY' ? fmt.money(e.base_salary) : fmt.money(e.hourly_rate) + '/h'}</td>
        <td class="right num">${e.salary_hidden ? '' : fmt.money(e.allowance)}</td><td>${e.payment_details_on_file ? '<span class="badge green">On file</span>' : '<span class="badge red">Missing</span>'}</td>
        <td>${statusBadge(e.status)}</td><td class="actions">${can('employees.write') ? `<button class="btn sm" data-att="${e.id}">Hours</button> <button class="btn sm ghost" data-leave="${e.id}">Leave</button> <button class="btn sm ghost" data-editemp="${e.id}">Edit</button>` : ''}</td></tr>`).join('')}
      </tbody></table></div></section>`;
  }

  async function payroll() {
    const r = await api.get('/api/payroll');
    const e = r.estimate;
    const run = r.runs.find(x => x.period === r.period && x.status !== 'CANCELLED');
    const sev = { HIGH: 'red', MEDIUM: 'amber', INFO: 'blue' };
    pane.innerHTML = `<div class="kpis" style="margin-bottom:16px">
        <div class="kpi"><div class="k-label">Next pay date</div><div class="k-value">${fmt.date(r.next_pay_date)}</div><div class="small muted">Period ${esc(r.period)}</div></div>
        <div class="kpi"><div class="k-label">Gross pay</div><div class="k-value num">${fmt.money(e.total_gross)}</div></div>
        <div class="kpi"><div class="k-label">Employee deductions</div><div class="k-value num">${fmt.money(e.total_deductions)}</div></div>
        <div class="kpi"><div class="k-label">Net pay</div><div class="k-value num">${fmt.money(e.total_net)}</div></div>
        <div class="kpi"><div class="k-label">Total cost to business</div><div class="k-value num">${fmt.money(e.total_cost_to_business)}</div><div class="small muted">incl. employer ${fmt.money(e.total_employer_cost)}</div></div>
      </div>
      <div class="grid g-2-1">
        <section class="panel"><div class="panel-head"><h2>Payroll ${esc(r.period)}</h2>${run ? statusBadge(run.status) : '<span class="badge">Not prepared</span>'}
          <div class="tools">${can('payroll.prepare') && (!run || ['DRAFT', 'AWAITING_APPROVAL'].includes(run.status)) ? `<button class="btn primary sm" id="prep">${run ? 'Recalculate draft' : 'Prepare payroll'}</button>` : ''}
          ${run && run.status === 'AWAITING_APPROVAL' && can('payroll.approve') ? `<button class="btn sm teal" data-approve-run="${run.id}">Approve</button>` : ''}
          ${run && run.status === 'APPROVED' && can('payroll.pay') ? `<button class="btn sm" data-pay-run="${run.id}">Record payment</button>` : ''}
          ${run && ['APPROVED', 'PAID'].includes(run.status) ? `<button class="btn sm ghost" data-slips="${run.id}">Payslips</button>` : ''}</div></div>
          <div class="panel-body table-wrap"><table class="t"><thead><tr><th>Employee</th><th class="right">Basic</th><th class="right">Allow.</th><th class="right">Overtime</th><th class="right">Unpaid leave</th><th class="right">Gross</th><th class="right">Deductions</th><th class="right">Net</th><th class="right">Employer</th></tr></thead><tbody>
            ${e.items.map(i => `<tr title="${esc(i.steps.join('\n'))}"><td class="strong">${esc(i.name)}<div class="small muted">${i.employment_type === 'HOURLY' ? `${fmt.num(i.hours_worked)}h` : 'Monthly'}${i.overtime_hours ? ` + ${i.overtime_hours}h OT` : ''}</div></td>
              <td class="right num">${fmt.money(i.basic, { noCur: true })}</td><td class="right num">${fmt.money(i.allowances, { noCur: true })}</td><td class="right num">${fmt.money(i.overtime, { noCur: true })}</td>
              <td class="right num">${i.unpaid_leave_deduction ? '−' + fmt.money(i.unpaid_leave_deduction, { noCur: true }) : '—'}</td><td class="right num strong">${fmt.money(i.gross, { noCur: true })}</td>
              <td class="right num">${fmt.money(i.deductions, { noCur: true })}</td><td class="right num strong">${fmt.money(i.net, { noCur: true })}</td><td class="right num">${fmt.money(i.employer_contrib, { noCur: true })}</td></tr>`).join('')}
          </tbody></table><p class="small muted" style="margin-top:8px">Amounts in ${esc(state.currency)}. Hover a row to see how it was calculated.</p></div></section>
        <section class="panel"><div class="panel-head"><h2>Checks before payday</h2></div><div class="panel-body">
          ${e.anomalies.length ? e.anomalies.map(a => `<div class="alert-row"><span class="sev ${a.severity === 'HIGH' ? 'critical' : a.severity === 'MEDIUM' ? 'warning' : 'info'}"></span><div>
            <div class="a-title">${esc(a.employee || 'Payroll rules')} <span class="badge ${sev[a.severity]}">${a.severity.toLowerCase()}</span></div><div class="a-detail">${esc(a.issue)}</div></div></div>`).join('') : '<p class="muted">No issues found.</p>'}
        </div></section>
      </div>
      <section class="panel" style="margin-top:16px"><div class="panel-head"><h2>Payroll history</h2></div><div class="panel-body table-wrap"><table class="t"><thead><tr><th>Period</th><th>Status</th><th class="right">Gross</th><th class="right">Net</th><th class="right">Employer</th><th>Approved by</th><th></th></tr></thead><tbody>
        ${r.runs.map(x => `<tr><td class="strong">${esc(x.period)}</td><td>${statusBadge(x.status)}</td><td class="right num">${fmt.money(x.total_gross)}</td><td class="right num">${fmt.money(x.total_net)}</td><td class="right num">${fmt.money(x.total_employer_cost)}</td>
          <td class="small muted">${esc(x.approved_by || '')}</td><td class="actions">${['APPROVED', 'PAID'].includes(x.status) ? `<button class="btn sm ghost" data-slips="${x.id}">Payslips</button>` : ''}</td></tr>`).join('')}
      </tbody></table></div></section>`;
    pane.querySelector('#prep')?.addEventListener('click', async () => {
      try { const x = await api.post('/api/payroll/prepare', { period: r.period }); toast(x.note || `Payroll ${x.period} prepared — awaiting owner approval`, 'ok'); refreshCounts(); payroll(); } catch (err) { errToast(err); }
    });
  }

  on(pane, 'click', '[data-approve-run]', async (e, b) => {
    if (!await confirmBox('Approve payroll', 'Approve this payroll run? Payslips become available. No money is sent until you record the payment.', 'Approve')) return;
    try { await api.post(`/api/payroll/${b.dataset.approveRun}/approve`); toast('Payroll approved', 'ok'); refreshCounts(); payroll(); } catch (err) { errToast(err); }
  });
  on(pane, 'click', '[data-pay-run]', async (e, b) => {
    if (!await confirmBox('Record salary payment', 'Record that salaries have been paid? This books the expense in Finance. Beezy does not move money; pay through your bank as usual.', 'Record payment')) return;
    try { await api.post(`/api/payroll/${b.dataset.payRun}/pay`); toast('Payment recorded', 'ok'); payroll(); } catch (err) { errToast(err); }
  });
  on(pane, 'click', '[data-slips]', async (e, b) => {
    try {
      const r = await api.get(`/api/payroll/${b.dataset.slips}/payslips`);
      await modal({ title: `Payslips ${r.payslips[0]?.period || ''}`, wide: true, noFooter: true, body: `<div class="grid g2">${r.payslips.map(p => `<div class="panel flat" style="padding:14px">
        <div class="row"><b>${esc(p.employee)}</b><span class="muted small">${esc(p.emp_code)} · ${esc(p.job_title || '')}</span><span style="margin-left:auto">${statusBadge(p.status)}</span></div>
        <dl class="kv" style="margin-top:8px">${Object.entries(p.earnings).filter(([, v]) => v).map(([k, v]) => `<dt>${esc(k)}</dt><dd class="num">${fmt.money(v)}</dd>`).join('')}
        <dt><b>Gross</b></dt><dd class="num"><b>${fmt.money(p.gross)}</b></dd>${p.deductions.filter(d => d.kind.startsWith('EMPLOYEE')).map(d => `<dt>${esc(d.name)}</dt><dd class="num">−${fmt.money(d.amount)}</dd>`).join('')}
        <dt><b>Net pay</b></dt><dd class="num"><b>${fmt.money(p.net)}</b></dd><dt>Employer contributions</dt><dd class="num">${fmt.money(p.employer_contributions)}</dd></dl>
        <details style="margin-top:6px"><summary class="small muted">How this was calculated</summary><ul class="small" style="padding-left:18px">${p.steps.map(s => `<li>${esc(s)}</li>`).join('')}</ul></details></div>`).join('')}</div>` });
    } catch (err) { errToast(err); }
  });

  async function rules() {
    const r = await api.get('/api/payroll');
    pane.innerHTML = `<p class="muted" style="max-width:80ch">Statutory contributions are configured here as versioned rules, never hard-coded into prompts. A rule marked unverified is flagged on every payroll until someone confirms it against the official source.</p>
      ${can('payroll.approve') ? `<div class="row" style="margin:12px 0"><button class="btn" id="addRule">${icon('plus')} Add rule</button>${r.templates.map(t => `<button class="btn ghost" data-tpl="${t}">Add ${t.replace(/_/g, ' ').toLowerCase()} (unverified)</button>`).join('')}</div>` : ''}
      <section class="panel"><div class="panel-body table-wrap"><table class="t"><thead><tr><th>Rule</th><th>Jurisdiction</th><th>Type</th><th class="right">Rate</th><th class="right">Wage ceiling</th><th>Version</th><th>Verified</th><th>Active</th></tr></thead><tbody>
      ${r.all_rules.map(x => `<tr><td class="strong">${esc(x.name)}<div class="small muted">${esc(x.source_note || '')}</div></td><td>${esc(x.jurisdiction)}</td><td>${esc(fmt.status(x.kind))}</td>
        <td class="right num">${x.kind.endsWith('PCT') ? x.rate + '%' : fmt.money(x.rate)}</td><td class="right num">${x.wage_ceiling ? fmt.money(x.wage_ceiling) : '—'}</td><td>${esc(x.version)}</td>
        <td>${can('payroll.approve') ? `<button class="btn sm ${x.verified ? '' : 'danger'}" data-verify="${x.id}" data-v="${x.verified}">${x.verified ? 'Verified' : 'Unverified'}</button>` : (x.verified ? 'Yes' : 'No')}</td>
        <td>${can('payroll.approve') ? `<button class="btn sm" data-active="${x.id}" data-a="${x.active}">${x.active ? 'Active' : 'Inactive'}</button>` : (x.active ? 'Yes' : 'No')}</td></tr>`).join('')}
      </tbody></table></div></section>`;
    pane.querySelector('#addRule')?.addEventListener('click', async () => {
      const f = await modal({ title: 'Add payroll rule', body: `<div class="form-grid">${field('name', 'Name', { required: true, full: true })}${field('jurisdiction', 'Jurisdiction', { value: 'GENERIC' })}
        ${field('kind', 'Type', { options: [['EMPLOYEE_PCT', 'Employee % deduction'], ['EMPLOYER_PCT', 'Employer % contribution'], ['EMPLOYEE_FIXED', 'Employee fixed deduction'], ['EMPLOYER_FIXED', 'Employer fixed contribution']] })}
        ${field('rate', 'Rate (% or amount)', { type: 'number', step: '0.01', required: true })}${field('wage_ceiling', 'Wage ceiling (optional)', { type: 'number', step: '0.01' })}
        ${field('applies_to', 'Applies to', { options: ['ALL', 'MONTHLY', 'HOURLY'] })}${field('version', 'Version', { value: 'v1' })}${field('source_note', 'Source / note', { full: true })}
        ${field('verified', 'I have verified this against the official source', { type: 'checkbox', full: true })}</div>` });
      if (!f) return;
      try { await api.post('/api/payroll/rules', { ...f, rate: Number(f.rate), wage_ceiling: f.wage_ceiling ? Number(f.wage_ceiling) : null }); toast('Rule added', 'ok'); rules(); } catch (err) { errToast(err); }
    });
  }
  on(pane, 'click', '[data-tpl]', async (e, b) => { try { const x = await api.post('/api/payroll/rules/template', { template: b.dataset.tpl }); toast(x.note, 'ok'); rules(); } catch (err) { errToast(err); } });
  on(pane, 'click', '[data-verify]', async (e, b) => { try { await api.put(`/api/payroll/rules/${b.dataset.verify}`, { verified: b.dataset.v !== '1' }); rules(); } catch (err) { errToast(err); } });
  on(pane, 'click', '[data-active]', async (e, b) => { try { await api.put(`/api/payroll/rules/${b.dataset.active}`, { active: b.dataset.a !== '1' }); rules(); } catch (err) { errToast(err); } });

  const thisPeriod = new Date().toISOString().slice(0, 7);
  on(pane, 'click', '[data-att]', async (e, b) => {
    const emp = emps.find(x => x.id === Number(b.dataset.att));
    const f = await modal({ title: `Hours & bonus: ${emp.name}`, body: `<div class="form-grid">${field('period', 'Period (YYYY-MM)', { value: thisPeriod, required: true })}
      ${field('hours_worked', 'Hours worked (hourly staff)', { type: 'number', step: '0.5', value: 0 })}${field('overtime_hours', 'Overtime hours', { type: 'number', step: '0.5', value: 0 })}${field('bonus', 'Bonus', { type: 'number', step: '0.01', value: 0 })}</div>` });
    if (!f) return;
    try { await api.post(`/api/employees/${emp.id}/attendance`, { period: f.period, hours_worked: Number(f.hours_worked), overtime_hours: Number(f.overtime_hours), bonus: Number(f.bonus) }); toast('Saved', 'ok'); } catch (err) { errToast(err); }
  });
  on(pane, 'click', '[data-leave]', async (e, b) => {
    const emp = emps.find(x => x.id === Number(b.dataset.leave));
    const f = await modal({ title: `Record leave: ${emp.name}`, body: `<div class="form-grid">${field('start_date', 'From', { type: 'date', required: true })}${field('end_date', 'To', { type: 'date', required: true })}
      ${field('days', 'Working days', { type: 'number', step: '0.5', value: 1 })}${field('leave_type', 'Type', { options: ['Annual', 'Sick', 'Unpaid', 'Other'] })}${field('paid', 'Paid leave', { type: 'checkbox', value: true })}</div>` });
    if (!f) return;
    try { await api.post(`/api/employees/${emp.id}/leave`, { ...f, days: Number(f.days) }); toast('Leave recorded', 'ok'); } catch (err) { errToast(err); }
  });
  on(pane, 'click', '[data-editemp]', async (e, b) => {
    const emp = emps.find(x => x.id === Number(b.dataset.editemp));
    const f = await modal({ title: `Edit ${emp.name}`, body: `<div class="form-grid">${field('job_title', 'Job title', { value: emp.job_title || '' })}${field('department', 'Department', { value: emp.department || '' })}
      ${!emp.salary_hidden ? field(emp.employment_type === 'MONTHLY' ? 'base_salary' : 'hourly_rate', emp.employment_type === 'MONTHLY' ? 'Basic monthly salary' : 'Hourly rate', { type: 'number', step: '0.01', value: emp.employment_type === 'MONTHLY' ? emp.base_salary : emp.hourly_rate, hint: 'Salary changes require the owner' }) : ''}
      ${!emp.salary_hidden ? field('allowance', 'Allowance', { type: 'number', step: '0.01', value: emp.allowance }) : ''}
      ${field('status', 'Status', { options: ['ACTIVE', 'INACTIVE'], value: emp.status })}${field('payment_details_on_file', 'Bank / payment details on file', { type: 'checkbox', value: !!emp.payment_details_on_file, full: true })}</div>` });
    if (!f) return;
    for (const k of ['base_salary', 'hourly_rate', 'allowance']) if (k in f) f[k] = Number(f[k]);
    try { await api.put(`/api/employees/${emp.id}`, f); toast('Saved', 'ok'); employees(); } catch (err) { errToast(err); }
  });
  on(pane, 'click', '[data-emp]', async (e, a) => {
    e.preventDefault();
    try {
      const d = await api.get(`/api/employees/${a.dataset.emp}`);
      await modal({ title: d.name, noFooter: true, body: `<dl class="kv"><dt>Job title</dt><dd>${esc(d.job_title || '')}</dd><dt>Start date</dt><dd>${fmt.date(d.start_date)}</dd><dt>Leave entitlement</dt><dd>${d.leave_entitlement} days</dd></dl>
        <h3 style="margin:14px 0 6px">Hours by period</h3><table class="t"><tbody>${d.attendance.map(x => `<tr><td>${esc(x.period)}</td><td class="right num">${x.hours_worked}h</td><td class="right num">${x.overtime_hours}h OT</td><td class="right num">${x.bonus ? fmt.money(x.bonus) : ''}</td></tr>`).join('') || '<tr><td class="muted">No records</td></tr>'}</tbody></table>
        <h3 style="margin:14px 0 6px">Leave</h3><table class="t"><tbody>${d.leave.map(x => `<tr><td>${fmt.date(x.start_date)} – ${fmt.date(x.end_date)}</td><td>${esc(x.leave_type)}</td><td class="right num">${x.days} d</td><td>${x.paid ? 'Paid' : 'Unpaid'}</td></tr>`).join('') || '<tr><td class="muted">No leave recorded</td></tr>'}</tbody></table>` });
    } catch (err) { errToast(err); }
  });

  el.querySelector('#addEmp')?.addEventListener('click', async () => {
    const f = await modal({ title: 'Add employee', body: `<div class="form-grid">${field('emp_code', 'Employee ID', { required: true })}${field('name', 'Full name', { required: true })}
      ${field('job_title', 'Job title')}${field('department', 'Department')}${field('employment_type', 'Employment type', { options: [['MONTHLY', 'Monthly salary'], ['HOURLY', 'Hourly']] })}${field('start_date', 'Start date', { type: 'date' })}
      ${field('base_salary', 'Basic monthly salary', { type: 'number', step: '0.01', value: 0 })}${field('hourly_rate', 'Hourly rate', { type: 'number', step: '0.01', value: 0 })}
      ${field('allowance', 'Monthly allowance', { type: 'number', step: '0.01', value: 0 })}${field('standard_hours', 'Standard weekly hours', { type: 'number', value: 44 })}
      ${field('payment_details_on_file', 'Bank / payment details on file', { type: 'checkbox', value: true, full: true })}</div>` });
    if (!f) return;
    for (const k of ['base_salary', 'hourly_rate', 'allowance', 'standard_hours']) f[k] = Number(f[k] || 0);
    if (!f.start_date) delete f.start_date;
    try { await api.post('/api/employees', f); toast('Employee added', 'ok'); tab = 'employees'; show(); } catch (err) { errToast(err); }
  });

  async function show() {
    try { if (tab === 'employees') await employees(); else if (tab === 'rules') await rules(); else await payroll(); } catch (e) { pane.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  }
  show();
}
