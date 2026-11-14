// 工作台和智能中枢的蓝牙灯（设计画布第 38 块）。数据都经小麦网关（/kiosk/integrations）：
// 桌面 = Mac 上的 DeskHub App（切显示器输入源、AI 用量、提醒事项），机柜 = Prometheus 和 rack-screen，仓库 = repo-manager，
// 蓝牙灯 = 智能中枢。网关上没接入、或这台设备关着对应插件的，这里什么都不显示。
// 在 home.js 之后、dash.js 之前加载；用 app.js 的 $、esc、toast、handlers、ui，home.js 的 window.miApi。
(() => {

const ig = { data: null, open: false, homeOpen: false, light: null, busy: {}, sig: {} };
const A = () => (ig.data && ig.data.available) || {};
const hasWork = () => !!(A().rack_status || A().repo_status || A().desk_hub);
const set = (id, html) => { const el = $(id); if (el && ig.sig[id] !== html) { ig.sig[id] = html; el.innerHTML = html; } };
const hm = t => { const d = new Date(t * 1000); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
const ICON = {
  desk: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  rack: '<rect x="4" y="3" width="16" height="7" rx="1.5"/><rect x="4" y="14" width="16" height="7" rx="1.5"/><path d="M8 6.5h.01M8 17.5h.01"/>',
  repo: '<path d="M7 7h10M7 7l3-3M7 7l3 3M17 17H7M17 17l-3-3M17 17l-3 3"/>',
  light: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
  group: '<path d="M12 3l2.2 5.6L20 9l-4.5 3.8L17 19l-5-3.2L7 19l1.5-6.2L4 9l5.8-.4z"/>',
  psu: '<path d="M13 2L5 14h6l-1 8 8-12h-6z"/>',
};
const svg = (k, s = 24) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON[k] || ICON.light}</svg>`;

async function postJson(path, body) {
  try {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    return await r.json();
  } catch (e) {
    return { ok: false, error: '客户端没有响应' };
  }
}
async function refresh() {
  try {
    const r = await (await fetch('/api/integrations?fresh=1')).json();
    if (r.integrations) { ig.data = r.integrations; renderAll(); }
    else if (r.error) toast(r.error, 'error');
  } catch (e) { /* 事件流会带来新的 */ }
}

/* ---------------- 工作台 ---------------- */
function openWork(on) {   // 也给「打开页面」用（wf.js，设计画布第 53 块）
  ig.open = on;
  $('work').classList.toggle('on', on);
  if (!DEMO) postJson('/api/integrations/visible', { visible: on || ig.homeOpen });
  if (on) { renderWork(); refresh(); }
}
const secHead = (k, title, small = '') => `<div class="wk-h"><span class="wk-ic">${svg(k, 22)}</span><b>${title}</b>${small ? `<small>${esc(small)}</small>` : ''}</div>`;
const note = text => `<p class="wk-note">${esc(text)}</p>`;
const NOT_YET = name => `还没接入：在小麦网关后台「意图识别 › 插件」里打开「${name}」并点「设置」。`;

function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  return s < 15 ? '刚刚' : s < 60 ? `${Math.round(s)} 秒前` : s < 3600 ? `${Math.round(s / 60)} 分钟前` : `上次 ${hm(t)}`;
}
function meter(left) {
  return `<div class="wk-meter${left <= 25 ? ' low' : ''}"><i style="transform:scaleX(${Math.max(0, Math.min(100, left)) / 100})"></i></div>`;
}
function tokens(n) { return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${(n / 1e4).toFixed(1)} 万` : String(n); }
function resetIn(s) { return !s ? '' : s < 3600 ? `${Math.max(1, Math.round(s / 60))} 分钟后` : s < 86400 ? `${Math.floor(s / 3600)}:${String(Math.floor(s % 3600 / 60)).padStart(2, '0')} 后` : `${Math.round(s / 86400)} 天后`; }

function deskHtml() {
  if (!A().desk_hub) return secHead('desk', '桌面') + note(NOT_YET('桌面（DeskHub）'));
  const d = ig.data.desk_hub || {};
  if (!d.seen) return secHead('desk', '桌面') + note('还没有 Mac 连上来：在 Mac 上的 DeskHub App 里打开 设置 › 小麦网关，填网关地址和后台里的同一串口令。');
  const parts = [secHead('desk', '桌面', `${d.name} · ${d.online ? ago(d.seen) : '上次 ' + hm(d.seen)}`)];
  if (!d.online) parts.push(note(`Mac 上的 DeskHub 没连网关（上次 ${hm(d.seen)}）。下面是上次的内容，现在点了不会生效。`));
  const srcs = d.sources || [];
  parts.push(srcs.length ? `<div class="wk-srcs">${srcs.map(s => `<button class="wk-src${s.id === d.current ? ' on' : ''}${ig.busy[s.id] ? ' busy' : ''}" data-src="${esc(s.id)}">` +
      `<b>${esc(s.name)}</b><small>${s.id === d.current ? '当前' : esc(s.port || '')}</small></button>`).join('')}</div>`
    : note('Mac 上还没有试切过的输入源：在 DeskHub 的菜单里先试切一次。'));
  const u = d.usage;
  if (u) {
    parts.push(`<div class="wk-row"><b><span class="ai-ink">AI 用量</span>${u.provider ? ' · ' + esc([u.provider, u.plan].filter(Boolean).join(' ')) : ''}</b><small>今天 ${u.calls} 次 · ${tokens(u.tokens)} token</small></div>`);
    parts.push(u.windows.map(w => `<div class="wk-win"><span>${esc(w.name)}</span>${meter(w.left)}<small>剩 ${w.left}%${w.reset_s ? ' · ' + resetIn(w.reset_s) : ''}</small></div>`).join(''));
  } else if (d.usage_off) parts.push(note('Mac 上读不到 Magpie 的用量（Magpie 没开？）'));
  if (d.todos_off) parts.push(note(d.todos_off === 'access' ? 'DeskHub 没有读提醒事项的权限：在 Mac 的 系统设置 › 隐私与安全性 › 提醒事项 里打开。' : 'DeskHub 里关掉了提醒事项。'));
  else {
    const todos = d.todos || [];
    parts.push(`<div class="wk-row"><b>电脑上的待办</b><small>${todos.length < d.todos_total ? `${todos.length} / ${d.todos_total}` : todos.length ? `${todos.length} 条` : ''}</small></div>`);
    parts.push(todos.length ? `<div class="wk-todos">${todos.slice(0, 12).map(t => `<div class="wk-todo">` +
        `<button class="wk-check${ig.busy[t.id] ? ' busy' : ''}" data-todo="${esc(t.id)}" ${d.allow_todo ? '' : 'disabled'} aria-label="完成"></button>` +
        `<span>${esc(t.title)}</span>${t.when ? `<em class="k-${esc(t.kind)}">${esc(t.when)}</em>` : ''}</div>`).join('')}</div>` : note('没有未完成的待办'));
  }
  return parts.join('');
}

function rackHtml() {
  if (!A().rack_status) return secHead('rack', '机柜') + note(NOT_YET('机柜状态'));
  const r = ig.data.rack_status || {};
  if (r.error) return secHead('rack', '机柜') + note(`读不到 Prometheus：${r.error}`);
  const hosts = r.hosts || [];
  const parts = [secHead('rack', '机柜', `${r.up}/${r.total} 台在线${r.problems && r.problems.length ? ` · ${r.problems.length} 个告警` : ''}`)];
  const bar = (v, hot) => v === null || v === undefined ? '<span class="wk-na">—</span>' : `<div class="wk-mini${hot ? ' hot' : ''}"><i style="transform:scaleX(${Math.min(100, v) / 100})"></i></div>`;
  parts.push(`<div class="wk-hosts"><div class="wk-host wk-cols-h"><i></i><span>主机</span><span>CPU</span><span>内存</span><span>温度</span></div>` + hosts.map(h => h.up
    ? `<div class="wk-host"><i class="wk-dot ${h.hot ? 'warn' : 'ok'}"></i><b>${esc(h.name)}</b>${bar(h.cpu, h.cpu >= 90)}${bar(h.mem, h.mem >= 90)}<span class="${(h.temp || 0) >= 70 ? 'wk-warn' : ''}">${h.temp === null ? '—' : h.temp + '℃'}</span></div>`
    : `<div class="wk-host down"><i class="wk-dot bad"></i><b>${esc(h.name)}</b><span class="wk-span">掉线${h.down_minutes ? ` ${h.down_minutes} 分钟` : ''}</span></div>`).join('') + '</div>');
  const chips = (r.probes || []).map(p => `<span class="wk-chip${p.up ? '' : ' bad'}">${esc(p.name)}${p.up ? (p.ms !== null ? ` · ${p.ms} ms` : '') : ' · 不通'}</span>`)
    .concat((r.monitors || []).map(m => `<span class="wk-chip${m.state === 'down' ? ' bad' : ''}">${esc(m.name)}${m.state === 'down' ? ' · 失败' : m.ms ? ` · ${m.ms} ms` : ''}</span>`));
  if (chips.length) parts.push(`<div class="wk-chips">${chips.join('')}</div>`);
  if (r.monitor_error) parts.push(note(r.monitor_error));
  return parts.join('');
}

function repoHtml() {
  if (!A().repo_status) return secHead('repo', '仓库同步') + note(NOT_YET('仓库同步'));
  const r = ig.data.repo_status || {};
  if (r.error) return secHead('repo', '仓库同步') + note(`读不到 repo-manager：${r.error}`);
  const ev = r.last_event;
  const parts = [secHead('repo', '仓库同步', ev && ev.at ? ev.at.slice(5, 16).replace('T', ' ') : '')];
  parts.push(`<div class="wk-nums"><div><b>${r.total}</b><small>仓库</small></div><div class="ok"><b>${r.synced}</b><small>已同步</small></div>` +
    `<div class="${r.failed ? 'bad' : ''}"><b>${r.failed}</b><small>失败</small></div><div class="${r.suspect7 ? 'bad' : ''}"><b>${r.suspect7}</b><small>疑似删库</small></div></div>`);
  const w = r.watches || {};
  if (w.running) parts.push(note(`${w.running} 个同步任务正在跑`));
  if ((r.attention || []).length) parts.push(`<div class="wk-att">${r.attention.map(a => `<div><b>${esc(a.name)}</b><small>${esc([a.why, a.host].filter(Boolean).join(' · '))}</small></div>`).join('')}` +
    (r.attention_total > r.attention.length ? `<small class="wk-more">还有 ${r.attention_total - r.attention.length} 个需要处理，在 repo-manager 网页里看</small>` : '') + '</div>');
  if (ev && ev.msg) parts.push(`<p class="wk-note">${esc(ev.msg)}</p>`);
  return parts.join('');
}

function renderWork() {
  if (!ig.open) return;
  if (!ig.data) { set('wkDesk', note('正在连小麦网关…')); return; }
  set('wkDesk', deskHtml());
  set('wkRack', rackHtml());
  set('wkRepo', repoHtml());
  $('wkSub').textContent = ig.data.at ? `经小麦网关 · ${hm(ig.data.at)} 更新` : '经小麦网关';
}

async function deskDo(op, id, label) {
  if (ig.busy[id]) return;
  const d = ig.data && ig.data.desk_hub;
  if (!d || !d.online) return toast('Mac 上的 DeskHub 没连上网关', 'error');
  ig.busy[id] = true; renderWork();
  const r = await postJson('/api/integrations/desk', { op, id });
  delete ig.busy[id];
  if (r.desk && ig.data) ig.data.desk_hub = r.desk;
  renderAll();
  if (r.ok) toast(r.text || label); else toast(r.text || r.error || '没做成', 'error');
}
$('wkCols').addEventListener('click', e => {
  const s = e.target.closest('[data-src]');
  if (s) {
    const d = ig.data.desk_hub;
    if (s.dataset.src === d.current) return toast('显示器已经在这个输入源了');
    return deskDo('switch', s.dataset.src);
  }
  const t = e.target.closest('[data-todo]');
  if (t && !t.disabled) return deskDo('todo_done', t.dataset.todo);
});
$('btnWork').addEventListener('click', () => openWork(true));
$('wkClose').addEventListener('click', () => openWork(false));
$('wkRefresh').addEventListener('click', () => { refresh(); toast('正在刷新'); });

/* ---------------- 首页：工作台摘要、蓝牙灯 ---------------- */
function workCells() {
  const a = A(), cells = [];
  const r = ig.data && ig.data.rack_status;
  if (a.rack_status && r && !r.error) cells.push({ b: `${r.up}/${r.total} 在线`, s: r.problems.length ? `机柜 · ${r.problems[0]}` : '机柜 · 没有告警', bad: r.up < r.total || r.problems.length });
  const d = ig.data && ig.data.desk_hub;
  if (a.desk_hub && d && d.usage && d.usage.windows.length) {
    const w = d.usage.windows[0];
    cells.push({ b: `剩 ${w.left}%`, s: `AI 用量 · ${w.name}`, bad: w.left <= 25 });
  } else if (a.desk_hub && d && d.seen) {
    const due = (d.todos || []).filter(t => t.kind === 'o' || t.kind === 't').length;
    cells.push({ b: `${due} 条`, s: '电脑待办 · 今天', bad: false });
  }
  const p = ig.data && ig.data.repo_status;
  if (a.repo_status && p && !p.error) cells.push({ b: `${p.failed} 失败`, s: `仓库 · ${p.total} 个`, bad: p.failed > 0 || p.suspect7 > 0 });
  return cells;
}
const lights = () => ((A().smart_hub && ig.data.smart_hub && ig.data.smart_hub.devices) || []).filter(x => x.kind === 'light');
function renderBlocks() {
  const cells = hasWork() ? workCells() : [];
  const w = $('homeWork');
  w.hidden = !cells.length;
  set('homeWork', cells.length ? `<div class="hw-h"><b>工作台</b><small>点开看全部 ›</small></div><div class="hw-cells">${cells.map(c =>
    `<div class="${c.bad ? 'bad' : ''}"><b>${esc(c.b)}</b><small>${esc(c.s)}</small></div>`).join('')}</div>` : '');
  const ls = lights();
  $('homeLights').hidden = !ls.length;
  set('homeLights', ls.map(l => `<button class="hl-chip${l.on ? ' on' : ''}${ig.busy[l.id] ? ' busy' : ''}" data-light="${esc(l.id)}">${svg(l.group ? 'group' : 'light', 20)}` +
    `<b>${esc(l.name)}</b>${l.on && l.brightness !== null && l.brightness !== undefined ? `<small>${Math.round(l.brightness)}%</small>` : ''}</button>`).join(''));
  if (window.fitHome) window.fitHome();
}
$('homeWork').addEventListener('click', () => openWork(true));

/* ---------------- 家居页里的智能中枢 ---------------- */
const hubDevices = () => (ig.data && ig.data.smart_hub && ig.data.smart_hub.devices) || [];
const findDev = id => hubDevices().find(x => x.id === id);
function hubTile(x) {
  const on = x.on === true, power = x.kind !== 'psu' && (x.actions.includes('toggle') || x.actions.includes('on'));
  return `<div class="h-tile hub${on ? ' on' : ''}${x.kind === 'psu' && x.online === false ? ' offline' : ''}" data-hub="${esc(x.id)}">
    <div class="h-ic">${svg(x.kind === 'psu' ? 'psu' : x.group ? 'group' : 'light', 30)}</div>` +
    (power ? `<button class="h-power${on ? ' on' : ''}" data-hpower="${esc(x.id)}" aria-label="开关"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 3.5v8M7 6.3a7.5 7.5 0 1 0 10 0"/></svg></button>` : '') +
    `<b>${esc(x.name)}</b><span>${esc(x.summary || '')}${x.via ? ` · 经 ${esc(x.via)}` : ''}</span></div>`;
}
// home.js 的 renderHome 调：返回这台设备有没有接入智能中枢；shown：家居页现在在「智能中枢」那一页（设计画布第 45 块）
window.hubAvailable = () => !!A().smart_hub;
window.hubRender = shown => {
  const box = $('hHub');
  if (!A().smart_hub) { box.hidden = true; return false; }
  const s = ig.data.smart_hub || {};
  const devs = s.devices || [];
  const html = s.error ? `<p class="h-msg bad">${esc('读不到智能中枢：' + s.error)}</p>`
    : devs.length ? `<div class="h-tiles">${devs.map(hubTile).join('')}</div>` : '<p class="h-msg">智能中枢里还没有设备</p>';
  box.hidden = !shown;
  if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
  return true;
};
window.hubOpen = on => {
  ig.homeOpen = on;
  if (!on) closeLight();
  if (!DEMO) postJson('/api/integrations/visible', { visible: on || ig.open });
  if (on && A().smart_hub) refresh();
};
async function hubAct(id, action, value) {
  ig.busy[id] = true; renderBlocks();
  const r = await postJson('/api/integrations/smarthub', { id, action, value });
  delete ig.busy[id];
  if (!r.ok) toast(r.error || '智能中枢没做成', 'error');
  renderAll();
  return r;
}
function toggle(x) {
  if (x.actions.includes('toggle')) return hubAct(x.id, 'toggle');
  return hubAct(x.id, x.on ? 'off' : 'on');
}
$('hHub').addEventListener('click', e => {
  const p = e.target.closest('[data-hpower]');
  const x = p ? findDev(p.dataset.hpower) : null;
  if (x) return toggle(x);
  const t = e.target.closest('[data-hub]');
  if (t) openLight(t.dataset.hub);
});
$('homeLights').addEventListener('click', e => {
  const b = e.target.closest('[data-light]');
  const x = b && findDev(b.dataset.light);
  if (x) toggle(x);
});
if (window.miApi) window.miApi.longPress($('homeLights'), '[data-light]', el => openLight(el.dataset.light));
$('hRefresh').addEventListener('click', () => { if (A().smart_hub) refresh(); });

/* ---- 灯和电源的详情 ---- */
function openLight(id) { ig.light = id; $('lDetail').classList.add('on'); renderLight(true); }
function closeLight() { ig.light = null; $('lDetail').classList.remove('on'); }
function renderLight(first) {
  if (!ig.light) return;
  const x = findDev(ig.light);
  if (!x) return closeLight();
  $('ldName').textContent = x.name;
  $('ldSum').textContent = (x.summary || '') + (x.via ? ` · 经 ${x.via} 节点` : '');
  const box = $('ldControls');
  if (!first && box.contains(document.activeElement) && document.activeElement.type === 'range') return;
  const parts = [], has = a => x.actions.includes(a);
  if (x.kind === 'psu') {
    parts.push(`<div class="opt"><div class="opt-text"><b>输出</b><span>开关输出要确认</span></div>
      <button class="switch" data-lop="psu" role="switch" aria-checked="${x.on ? 'true' : 'false'}"><i></i></button></div>`);
    const r = [['设定', x.v_set, 'V'], ['设定', x.i_set, 'A'], ['输出', x.v_out, 'V'], ['输出', x.i_out, 'A'], ['功率', x.p_out, 'W']]
      .filter(([, v]) => v !== null && v !== undefined).map(([k, v, u]) => `<span>${k}：${v.toFixed(u === 'W' ? 1 : 2)} ${u}</span>`);
    if (x.model) r.push(`<span>型号：${esc(x.model)}</span>`);
    if (x.error) r.push(`<span>${esc(x.error)}</span>`);
    parts.push(`<div class="h-readings">${r.join('')}</div><p class="opt-note">电压、电流在智能中枢的网页上调。</p>`);
  } else {
    if (has('toggle') || has('on')) parts.push(`<div class="opt"><div class="opt-text"><b>开关</b></div>
      <button class="switch" data-lop="toggle" role="switch" aria-checked="${x.on ? 'true' : 'false'}"><i></i></button></div>`);
    const fill = (v, lo, hi) => `${Math.round(((v ?? lo) - lo) / (hi - lo) * 100)}%`;
    const range = (op, label, val, lo, hi, step, unit) => `<div class="bright"><div class="bright-head"><b>${label}</b><span class="bright-num" data-num="${op}">${val ?? '—'}${unit}</span></div>
      <div class="bright-row"><input type="range" data-lop="${op}" min="${lo}" max="${hi}" step="${step}" value="${val ?? lo}" class="range${op === 'cct' ? ' cct-range' : ''}" style="--fill:${fill(val, lo, hi)}"></div></div>`;
    if (has('brightness')) parts.push(range('brightness', '亮度', x.brightness === null || x.brightness === undefined ? null : Math.round(x.brightness), 1, 100, 1, '%'));
    if (has('cct')) {
      const lo = x.cct_min || 2700, hi = x.cct_max || 6500;
      parts.push(range('cct', `色温 ${lo}–${hi}K`, x.kelvin === null || x.kelvin === undefined ? null : Math.round(x.kelvin), lo, hi, 100, 'K'));
      const presets = [['暖光', 3000], ['自然', 4300], ['冷光', 5600]].filter(([, k]) => k >= lo && k <= hi);
      parts.push(`<div class="minutes">${presets.map(([n, k]) => `<button data-lop="cct" data-v="${k}" class="${x.kelvin === k ? 'on' : ''}">${n} ${k}K</button>`).join('')}</div>`);
    }
    parts.push('<p class="opt-note">拖动松手才发给灯（蓝牙每条约 0.3 秒）。</p>');
  }
  box.innerHTML = parts.join('');
}
$('ldClose').addEventListener('click', closeLight);
$('lDetail').addEventListener('click', e => { if (e.target === $('lDetail')) closeLight(); });
$('ldControls').addEventListener('click', e => {
  const b = e.target.closest('[data-lop]');
  const x = ig.light && findDev(ig.light);
  if (!b || !x || b.type === 'range') return;
  if (b.dataset.lop === 'toggle') return toggle(x);
  if (b.dataset.lop === 'psu') {
    const to = !x.on;
    return window.miApi.askConfirm(`${to ? '打开' : '关掉'}「${x.name}」的输出？`, () => hubAct(x.id, to ? 'on' : 'off'));
  }
  if (b.dataset.v) hubAct(x.id, b.dataset.lop, Number(b.dataset.v));
});
$('ldControls').addEventListener('input', e => {
  const r = e.target.closest('input[type=range]');
  const num = r && $('ldControls').querySelector(`[data-num="${r.dataset.lop}"]`);
  if (num) num.textContent = r.value + (r.dataset.lop === 'cct' ? 'K' : '%');
  if (r) r.style.setProperty('--fill', `${Math.round((r.value - r.min) / (r.max - r.min) * 100)}%`);
});
$('ldControls').addEventListener('change', e => {
  const r = e.target.closest('input[type=range]');
  const x = ig.light && findDev(ig.light);
  if (r && x) hubAct(x.id, r.dataset.lop, Number(r.value));
});

/* ---------------- 仪表盘卡片（dash.js 调） ---------------- */
window.igApi = {
  available: A,
  workCard(head) {
    const cells = hasWork() ? workCells() : [];
    if (!cells.length) return head('▭', '工作台') + '<button class="d-empty" data-work="1">在小麦网关后台的插件里接入机柜、仓库同步或 DeskHub，这里就有内容</button>';
    const r = ig.data.rack_status, prob = r && !r.error && r.problems.length ? `<div class="dm d-bad">● ${esc(r.problems[0])}</div>` : '';
    return head('▭', '工作台', '›') + `<button class="d-nums d-work" data-work="1">${cells.map(c => `<div><b class="${c.bad ? 'd-bad' : ''}">${esc(c.b)}</b><small>${esc(c.s.split(' · ')[0])}</small></div>`).join('')}</button>` + prob;
  },
  lightsCard(head) {
    const ls = lights();
    if (!ls.length) return head('✦', '蓝牙灯') + `<div class="dm">${A().smart_hub ? '智能中枢里还没有灯' : '在小麦网关后台接入智能中枢，这里就能开关蓝牙灯'}</div>`;
    return head('✦', '蓝牙灯', `${ls.filter(l => l.on).length} 盏开着`) + `<div class="d-chips">${ls.map(l =>
      `<button data-light="${esc(l.id)}" class="${l.on ? 'on' : ''}">${esc(l.name)}${l.on && l.brightness ? ' ' + Math.round(l.brightness) + '%' : ''}</button>`).join('')}</div>`;
  },
  openWork: () => openWork(true),
  toggleLight: id => { const x = findDev(id); if (x) toggle(x); },
  openLight,
  // 仪表盘「家居」的智能中枢那一页（设计画布第 45 块）：和家居页同样的设备块
  hubTiles() {
    const s = (ig.data && ig.data.smart_hub) || {}, devs = s.devices || [];
    return s.error ? `<p class="h-msg bad">${esc('读不到智能中枢：' + s.error)}</p>`
      : devs.length ? `<div class="h-tiles">${devs.map(hubTile).join('')}</div>` : '<p class="h-msg">智能中枢里还没有设备</p>';
  },
};

/* ---------------- 设置 › 智能体 › 接入的服务（设计画布第 40 块） ---------------- */
const SVC = [['desk_hub', '桌面（DeskHub）', '连接口令'], ['rack_status', '机柜', ''], ['repo_status', '仓库同步', ''], ['smart_hub', '蓝牙灯（智能中枢）', '访问令牌']];
function renderSvc() {
  const P = ig.data && ig.data.plugins;
  set('svcList', SVC.map(([k, label, need]) => {
    const s = P && P[k], g = s && (s.global ? '开' : '关');
    const sub = !s ? '—'
      : !s.ready && need ? `<em class="svc-warn">网关后台还没填${need}</em>`
      : s.own == null ? `跟着网关（${g}）`
      : `这台单独${s.own ? '开着' : '关了'}<button class="link-btn" data-svc-reset="${k}">改回跟着网关（${g}）</button>`;
    return `<div class="opt rot-auto svc-row"><div class="opt-text"><b>${esc(label)}</b><span>${sub}</span></div>`
      + `<button class="switch${s && !s.ready && need ? ' dim' : ''}" role="switch" data-svc="${k}" aria-checked="${!!(s && s.on)}" aria-label="${esc(label)}"${s && !ig.busy['svc:' + k] ? '' : ' disabled'}><i></i></button></div>`;
  }).join(''));
  $('svcNote').textContent = P ? '关掉后，顶栏的工作台按钮、首页和仪表盘上的那一块、语音命令都一起没有。只影响这台设备；别的插件在网关后台的「设备」页改。'
    : '连不上小麦网关，过一会儿再试。';
}
async function setSvc(k, on) {
  if (ig.busy['svc:' + k]) return;
  ig.busy['svc:' + k] = true; renderSvc();
  const r = await postJson('/api/integrations/plugin', { name: k, on });
  ig.busy['svc:' + k] = false;
  if (r.ok && r.plugins && ig.data) ig.data.plugins = r.plugins;
  if (!r.ok) toast(r.error || '没改成', 'error');
  renderAll();
}
$('svcList').addEventListener('click', e => {
  const sw = e.target.closest('[data-svc]'), reset = e.target.closest('[data-svc-reset]');
  const P = ig.data && ig.data.plugins;
  if (sw && !sw.disabled) setSvc(sw.dataset.svc, sw.getAttribute('aria-checked') !== 'true');
  else if (reset && P && P[reset.dataset.svcReset]) setSvc(reset.dataset.svcReset, !!P[reset.dataset.svcReset].global);
});

/* ---------------- 事件 ---------------- */
function renderAll() {
  $('btnWork').hidden = !hasWork();
  $('alertRackRow').hidden = !A().rack_status;
  renderBlocks();
  renderWork();
  renderLight(false);
  renderSvc();
  if (window.miApi && window.miApi.isOpen()) window.miApi.rerender();   // 仪表盘每 2 秒自己重画，不用通知
}
const baseSnapshot = handlers.snapshot;
handlers.snapshot = s => {
  baseSnapshot(s);
  if (s.integrations) ig.data = s.integrations;
  renderAll();
};
handlers.integrations = d => { ig.data = d; renderAll(); };
// 叫醒时工作台和灯的详情也收起（和家居页一样）
const baseAutoClose = window.miAutoClose;
window.miAutoClose = () => { if (baseAutoClose) baseAutoClose(); if (ig.open) openWork(false); closeLight(); };
window.openWork = openWork;
})();
