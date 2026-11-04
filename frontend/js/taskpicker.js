// "I want to…" picker: two drop-down lists that build a request, so nobody has to know what to type.
import { esc, icon } from './ui.js';

// Each task: what the owner wants, an optional second choice, and how that becomes a request for the agents.
export const TASKS = [
  { id: 'health', label: 'See how my business is doing', ask: 'Run check',
    detail: { label: 'Focus on', options: ['Everything', 'Cash and bills', 'Stock', 'Staff and payroll'] },
    prompt: (d) => d === 'Everything'
      ? 'Give me a health check of my business. Check my inventory, upcoming expenses, payroll and cash flow. Tell me what I should prioritise.'
      : `Give me a health check focused on ${d.toLowerCase()}. Tell me what needs my attention first.` },
  { id: 'restock', label: 'Restock items that are running low', ask: 'Prepare orders',
    detail: { label: 'Which items', options: ['Everything below 20%', 'Only items running out this week'] },
    prompt: (d) => d.startsWith('Only')
      ? 'Restock only the items that will run out within 7 days, and make sure the orders arrive before they run out.'
      : 'Restock everything below 20% and make sure it arrives before we run out.' },
  { id: 'afford', label: 'Check if I can afford what is coming up', ask: 'Check',
    detail: { label: 'Over the next', options: ['2 weeks', '30 days', '60 days'] },
    prompt: (d) => `Can I afford to restock and pay everyone over the next ${d}? Include payroll, supplier bills and recurring costs.` },
  { id: 'payroll', label: 'Get payroll ready', ask: 'Prepare payroll',
    prompt: () => "Prepare this month's payroll and tell me about any problems I need to fix before payday." },
  { id: 'contracts', label: 'Check contracts that need a decision', ask: 'Check contracts',
    detail: { label: 'Due within', options: ['30 days', '60 days', '90 days'] },
    prompt: (d) => `Which contracts expire or need a renewal decision in the next ${d}? Tell me the notice deadlines.` },
  { id: 'owed', label: 'Chase customers who owe me money', ask: 'Find who owes',
    prompt: () => 'Which customer invoices are overdue, how much is owed in total, and who should I follow up with first?' },
  { id: 'costs', label: 'Find ways to cut costs', ask: 'Find savings',
    prompt: () => 'Which expenses have gone up the most recently, and where could I save money without hurting the business?' },
];

export function pickerHTML(opts = {}) {
  const t = TASKS[0];
  return `<form class="bz-pick${opts.compact ? ' compact' : ''}" data-picker>
    <label class="f"><span>I want to</span>
      <select name="task">${TASKS.map(x => `<option value="${x.id}">${esc(x.label)}</option>`).join('')}</select></label>
    <label class="f" data-detail-wrap><span>${esc(t.detail.label)}</span>
      <select name="detail">${t.detail.options.map(o => `<option>${esc(o)}</option>`).join('')}</select></label>
    <button class="btn primary" type="submit">${icon('sparkle')}<span data-ask>${esc(t.ask)}</span></button>
  </form>`;
}

// Wires every picker inside root. onAsk(promptText) runs when the owner presses the button.
export function bindPicker(root, onAsk) {
  root.querySelectorAll('[data-picker]').forEach((form) => {
    const taskSel = form.querySelector('[name=task]');
    const wrap = form.querySelector('[data-detail-wrap]');
    const detailSel = form.querySelector('[name=detail]');
    const sync = () => {
      const t = TASKS.find(x => x.id === taskSel.value);
      form.querySelector('[data-ask]').textContent = t.ask;
      wrap.hidden = !t.detail;
      if (t.detail) {
        wrap.querySelector('span').textContent = t.detail.label;
        detailSel.innerHTML = t.detail.options.map(o => `<option>${esc(o)}</option>`).join('');
      }
    };
    taskSel.addEventListener('change', sync);
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const t = TASKS.find(x => x.id === taskSel.value);
      onAsk(t.prompt(t.detail ? detailSel.value : null));
    });
  });
}
