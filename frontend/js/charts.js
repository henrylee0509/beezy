// Small dependency-free SVG charts. Tooltips use data-tip attributes handled globally in app.js.
import { esc, fmt } from './ui.js';

export const PALETTE = ['#e0a100', '#16140f', '#c2410c', '#7a5c1e', '#57534e', '#3f6212', '#b45309', '#a8a29e'];

function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function yAxis(min, max, W, H, pad, ticks = 4, f = fmt.compact) {
  let s = '';
  for (let i = 0; i <= ticks; i++) {
    const v = min + (max - min) * i / ticks;
    const y = pad.t + (H - pad.t - pad.b) * (1 - i / ticks);
    s += `<line class="grid-line" x1="${pad.l}" x2="${W - pad.r}" y1="${y}" y2="${y}"/><text x="${pad.l - 6}" y="${y + 3.5}" text-anchor="end">${f(v)}</text>`;
  }
  return s;
}

export function lineChart({ labels, series, height = 220, format = fmt.compact, tipFormat = (v) => fmt.money(v), zeroLine = false }) {
  const W = 640, H = height, pad = { l: 46, r: 12, t: 12, b: 26 };
  const all = series.flatMap(s => s.values).filter(v => v !== null && v !== undefined);
  let min = Math.min(0, ...all), max = niceMax(Math.max(...all, 1));
  if (min < 0) min = -niceMax(-min);
  const x = (i) => pad.l + (W - pad.l - pad.r) * (labels.length <= 1 ? 0.5 : i / (labels.length - 1));
  const y = (v) => pad.t + (H - pad.t - pad.b) * (1 - (v - min) / (max - min));
  let svg = yAxis(min, max, W, H, pad, 4, format);
  const step = Math.ceil(labels.length / 7);
  labels.forEach((l, i) => { if (i % step === 0 || i === labels.length - 1) svg += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(l)}</text>`; });
  if (zeroLine && min < 0) svg += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(0)}" y2="${y(0)}" stroke="var(--red)" stroke-dasharray="3 3"/>`;
  series.forEach((s, si) => {
    const color = s.color || PALETTE[si];
    const pts = s.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    if (s.area) svg += `<polygon points="${x(0)},${y(Math.max(min, 0))} ${pts} ${x(s.values.length - 1)},${y(Math.max(min, 0))}" fill="${color}" opacity=".08"/>`;
    svg += `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" ${s.dashed ? 'stroke-dasharray="5 4"' : ''} stroke-linejoin="round"/>`;
  });
  // hover columns
  labels.forEach((l, i) => {
    const tip = `${l}: ` + series.map(s => `${s.name} ${tipFormat(s.values[i])}`).join(' · ');
    const w = (W - pad.l - pad.r) / Math.max(1, labels.length - 1);
    svg += `<rect x="${x(i) - w / 2}" y="${pad.t}" width="${w}" height="${H - pad.t - pad.b}" fill="transparent" data-tip="${esc(tip)}"/>`;
  });
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map(s => s.name).join(', '))} chart">${svg}</svg>${legend(series)}`;
}

export function barChart({ labels, series, height = 220, format = fmt.compact, tipFormat = (v) => fmt.money(v) }) {
  const W = 640, H = height, pad = { l: 46, r: 12, t: 12, b: 26 };
  const max = niceMax(Math.max(...series.flatMap(s => s.values), 1));
  const inner = W - pad.l - pad.r;
  const group = inner / labels.length;
  const bw = Math.min(26, (group * 0.7) / series.length);
  let svg = yAxis(0, max, W, H, pad, 4, format);
  labels.forEach((l, i) => {
    const gx = pad.l + group * i + (group - bw * series.length) / 2;
    series.forEach((s, si) => {
      const v = s.values[i] || 0;
      const h = (H - pad.t - pad.b) * v / max;
      svg += `<rect x="${gx + si * bw}" y="${H - pad.b - h}" width="${bw - 2}" height="${Math.max(0, h)}" rx="3" fill="${s.color || PALETTE[si]}" ${s.faded && s.faded[i] ? 'opacity=".45"' : ''} data-tip="${esc(`${l} · ${s.name}: ${tipFormat(v)}`)}"/>`;
    });
    svg += `<text x="${pad.l + group * i + group / 2}" y="${H - 8}" text-anchor="middle">${esc(l)}</text>`;
  });
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="bar chart">${svg}</svg>${series.length > 1 ? legend(series) : ''}`;
}

export function hbars(items, { format = (v) => fmt.num(v), max } = {}) {
  const m = max || Math.max(...items.map(i => i.value), 1);
  return `<div class="stack" style="gap:9px">${items.map((it) => `
    <div data-tip="${esc(it.tip || `${it.label}: ${format(it.value)}`)}">
      <div class="row" style="justify-content:space-between;font-size:12.5px"><span>${esc(it.label)}</span><span class="num muted">${format(it.value)}</span></div>
      <div class="meter" style="height:8px"><span style="width:${Math.max(1.5, it.value / m * 100)}%;background:${it.color || PALETTE[0]}"></span>${it.mark ? `<i class="mark" style="left:${it.mark}%"></i>` : ''}</div>
    </div>`).join('')}</div>`;
}

export function donut(items, { size = 168, format = (v) => fmt.money(v) } = {}) {
  const total = items.reduce((a, b) => a + b.value, 0) || 1;
  const r = 60, c = 2 * Math.PI * r;
  let off = 0, arcs = '';
  items.forEach((it, i) => {
    const len = c * it.value / total;
    arcs += `<circle r="${r}" cx="80" cy="80" fill="none" stroke="${it.color || PALETTE[i % PALETTE.length]}" stroke-width="22"
      stroke-dasharray="${len} ${c - len}" stroke-dashoffset="${-off}" transform="rotate(-90 80 80)" data-tip="${esc(`${it.label}: ${format(it.value)} (${(it.value / total * 100).toFixed(1)}%)`)}"/>`;
    off += len;
  });
  return `<div class="row" style="gap:18px;flex-wrap:nowrap">
    <svg viewBox="0 0 160 160" width="${size}" height="${size}" style="flex:none" role="img" aria-label="breakdown">${arcs}
      <text x="80" y="76" text-anchor="middle" style="font:600 11px var(--sans);fill:var(--ink-3)">Total</text>
      <text x="80" y="94" text-anchor="middle" style="font:700 14px var(--sans);fill:var(--ink)">${fmt.compact(total)}</text></svg>
    <div class="stack" style="gap:5px;font-size:12.5px;min-width:0">${items.map((it, i) => `<div class="row" style="gap:7px;flex-wrap:nowrap">
      <i style="width:10px;height:10px;border-radius:3px;background:${it.color || PALETTE[i % PALETTE.length]};flex:none"></i>
      <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(it.label)}</span><span class="num muted" style="margin-left:auto;padding-left:8px">${format(it.value)}</span></div>`).join('')}</div>
  </div>`;
}

function legend(series) {
  return `<div class="legend">${series.map((s, i) => `<span><i style="background:${s.color || PALETTE[i]};${s.dashed ? 'opacity:.6' : ''}"></i>${esc(s.name)}</span>`).join('')}</div>`;
}
