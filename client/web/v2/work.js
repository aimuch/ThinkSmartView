// 工作台和智能中枢的蓝牙灯（设计画布第 38 块）。数据都经小麦网关（/kiosk/integrations）：
// 智能桌面 = Mac 上的 DeskHub App（切显示器输入源、AI 用量、提醒事项），机柜 = Prometheus 和 rack-screen，仓库 = repo-manager，
// 蓝牙灯 = 智能中枢。网关上没接入、或这台设备关着对应插件的，这里什么都不显示。
// 在 home.js 之后、dash.js 之前加载；用 app.js 的 $、esc、toast、handlers、ui，home.js 的 window.miApi。
(() => {

const ig = { data: null, open: false, homeOpen: false, light: null, busy: {}, sig: {}, undo: {} };
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
  hub: '<circle cx="12" cy="12" r="2.5"/><path d="M5 5l4.5 4.5M19 5l-4.5 4.5M5 19l4.5-4.5M19 19l-4.5-4.5"/>',
  ir: '<rect x="7" y="2" width="10" height="20" rx="3"/><path d="M10 7h4M12 11v.01M10 15h.01M14 15h.01M10 18h.01M14 18h.01"/>',
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
// 工作台的页（设计画布第 71、75 块）：智能桌面、智能中枢、机柜、仓库同步，每页占满；只显示这台设备接入了的、
// 设置里没藏起来的，按设置里的顺序
const WK = [['desk', '智能桌面', 'desk', 'wkDesk', 'desk_hub'], ['hub', '智能中枢', 'hub', 'wkHub', 'smart_hub'],
            ['rack', '机柜', 'rack', 'wkRack', 'rack_status'], ['repo', '仓库同步', 'repo', 'wkRepo', 'repo_status']];
const W = () => ui.work || { order: WK.map(w => w[0]), hidden: [], first: '', rack: 'screen', hub: 'tiles', split: {}, idle_close: true, keep: 10, urls: {} };
const wkOrdered = () => W().order.map(k => WK.find(w => w[0] === k)).filter(Boolean);
const wkTabs = () => {
  const on = wkOrdered().filter(w => A()[w[4]]), shown = on.filter(w => !W().hidden.includes(w[0]));
  return shown.length ? shown : on.length ? on.slice(0, 1) : WK.filter(w => w[0] !== 'hub');
};
function lastTab() { try { return localStorage.getItem('wkTab') || ''; } catch (e) { return ''; } }
function pickTab(...wants) {
  const keys = wkTabs().map(w => w[0]);
  return wants.find(k => k && keys.includes(k)) || keys[0];
}
function openWork(on, want) {   // 也给「打开页面」用（wf.js，设计画布第 53 块）；want：直接到哪页
  ig.open = on;
  $('work').classList.toggle('on', on);
  if (!DEMO) postJson('/api/integrations/visible', { visible: on || ig.homeOpen });
  if (on) { ig.tab = pickTab(want, W().first, lastTab()); ig.touched = Date.now(); renderWork(); refresh(); }
  else { renderScreen(); showCfg(false); }
}
const secHead = (k, title, small = '') => `<div class="wk-h"><span class="wk-ic">${svg(k, 22)}</span><b>${title}</b>${small ? `<small>${esc(small)}</small>` : ''}</div>`;
const note = text => `<p class="wk-note">${esc(text)}</p>`;
const NOT_YET = name => `还没接入：在小麦网关后台「外部服务 › 家里的系统」里打开「${name}」并点「设置」。`;

function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  return s < 15 ? '刚刚' : s < 60 ? `${Math.round(s)} 秒前` : s < 3600 ? `${Math.round(s / 60)} 分钟前` : `上次 ${hm(t)}`;
}
function meter(left) {
  return `<div class="wk-meter${left <= 25 ? ' low' : ''}"><i style="transform:scaleX(${Math.max(0, Math.min(100, left)) / 100})"></i></div>`;
}
function tokens(n) { return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${(n / 1e4).toFixed(1)} 万` : String(n); }
function resetIn(s) { return !s ? '' : s < 3600 ? `${Math.max(1, Math.round(s / 60))} 分钟后` : s < 86400 ? `${Math.floor(s / 3600)}:${String(Math.floor(s % 3600 / 60)).padStart(2, '0')} 后` : `${Math.round(s / 86400)} 天后`; }

// 智能桌面（设计画布第 77 块）：最大的一块是「显示器现在接的是…」；输入源带图标；AI 用量两个圆环；待办按过期 / 今天 / 以后 / 没定日子分组
const SRC_ICON = {
  mac: '<path d="M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9H4z"/><path d="M2 18h20"/>',
  kvm: '<path d="M7 7h11l-3-3M17 17H6l3 3"/>',
  usb: '<path d="M12 3v14M12 3l-2 3h4zM8 9v3l4 2M16 7v4l-4 2"/><circle cx="12" cy="19" r="2"/>',
  pc: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
};
function srcKind(s) {
  const n = `${s.name} ${s.port || ''}`.toLowerCase();
  return /mac|book|imac/.test(n) ? 'mac' : /kvm|切换/.test(n) ? 'kvm' : /usb|type-?c|雷电|thunder/.test(n) ? 'usb' : 'pc';
}
const srcSvg = (k, s = 22) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${SRC_ICON[k]}</svg>`;
function ringHtml(left, name, reset, i) {
  const v = Math.max(0, Math.min(100, left)), low = v <= 25;
  return `<div class="dk-ring"><div class="dk-ring-c${low ? ' low' : i ? ' two' : ''}" style="--v:${v * 3.6}deg"><div><b>${v}%</b><small>剩下</small></div></div><span>${esc(name)}</span>${reset ? `<small>${esc(reset)}</small>` : ''}</div>`;
}
const TODO_GROUPS = [['o', '过期', 'bad'], ['t', '今天', 'warn'], ['later', '以后', ''], ['n', '没定日子', '']];
function todoGroup(t) { return t.kind === 'o' ? 'o' : t.kind === 't' ? 't' : t.kind === 'n' || !t.when ? 'n' : 'later'; }
function deskHtml() {
  if (!A().desk_hub) return secHead('desk', '智能桌面') + note(NOT_YET('智能桌面（DeskHub）'));
  const d = ig.data.desk_hub || {};
  if (!d.seen) return secHead('desk', '智能桌面') + note('还没有 Mac 连上来：在 Mac 上的 DeskHub App（0.4.5 起）里打开 设置 › 控制终端，点「添加一个控制终端」，填网关地址和后台里的同一串口令。');
  const srcs = d.sources || [], cur = srcs.find(s => s.id === d.current);
  const left = [];
  left.push(`<div class="dk-hero${d.online ? '' : ' off'}"><div class="dk-mon">${cur ? srcSvg(srcKind(cur), 30) : ''}</div>` +
    `<div class="dk-now"><small>显示器现在接的是</small><b>${esc(cur ? cur.name : '不知道')}</b><span>${esc([cur && cur.port, d.online ? '读回确认 · 点下面换一个' : `DeskHub 没连网关（上次 ${hm(d.seen)}），现在点了不会生效`].filter(Boolean).join(' · '))}</span></div>` +
    `<em class="dk-live${d.online ? '' : ' off'}"><i></i>${esc(d.name || 'DeskHub')} · ${d.online ? ago(d.seen) : '离线'}</em></div>`);
  left.push(srcs.length ? `<div class="dk-srcs">${srcs.map(s => `<button class="dk-src${s.id === d.current ? ' on' : ''}${ig.busy[s.id] ? ' busy' : ''}" data-src="${esc(s.id)}">` +
      `<span class="dk-si">${srcSvg(srcKind(s))}</span><b>${esc(s.name)}</b><small>${s.id === d.current ? '现在' : esc(s.port || '')}</small></button>`).join('')}</div>`
    : note('Mac 上还没有试切过的输入源：在 DeskHub 的菜单里先试切一次。'));
  const u = d.usage;
  if (u) left.push(`<div class="dk-ai wk-card"><div class="wk-row"><b><span class="ai-ink">AI 用量</span>${u.provider ? ' · ' + esc([u.provider, u.plan].filter(Boolean).join(' ')) : ''}</b><small>Magpie 读的</small></div>` +
      `<div class="dk-rings">${(u.windows || []).slice(0, 2).map((w, i) => ringHtml(w.left, w.name, w.reset_s ? resetIn(w.reset_s) + '重置' : '', i)).join('')}` +
      `<div class="dk-stats"><div><b>${u.calls}</b><small>今天的请求</small></div><div><b>${tokens(u.tokens)}</b><small>今天的 token</small></div></div></div></div>`);
  else if (d.usage_off) left.push(note('Mac 上读不到 Magpie 的用量（Magpie 没开？）'));
  let right;
  if (d.todos_off) right = note(d.todos_off === 'access' ? 'DeskHub 没有读提醒事项的权限：在 Mac 的 系统设置 › 隐私与安全性 › 提醒事项 里打开。' : 'DeskHub 里关掉了提醒事项。');
  else {
    const todos = (d.todos || []).filter(x => !ig.undo[x.id]), total = d.todos_total || todos.length;
    const head2 = `<div class="wk-row"><b>电脑上的待办</b><small>${todos.length < total ? `${todos.length} / ${total}` : `${todos.length} 条`}${d.allow_todo ? ' · 点圆圈完成' : ''}</small></div>`;
    const undo = Object.values(ig.undo).map(x => `<div class="dk-undo"><span>「${esc(x.title)}」做完了</span><button data-undo="${esc(x.id)}">撤销</button></div>`).join('');
    right = head2 + undo + (todos.length ? TODO_GROUPS.map(([g, name, cls]) => {
      const list = todos.filter(x => todoGroup(x) === g);
      return list.length ? `<div class="dk-grp ${cls}"><span>${name}</span><span>${list.length}</span></div>` + list.map(x => `<div class="wk-todo${cls === 'bad' ? ' bad' : ''}">` +
        `<button class="wk-check${ig.busy[x.id] ? ' busy' : ''}" data-todo="${esc(x.id)}" ${d.allow_todo ? '' : 'disabled'} aria-label="完成"></button>` +
        `<span>${esc(x.title)}</span>${x.when ? `<em class="k-${esc(x.kind)}">${esc(x.when.replace('（过期）', ''))}</em>` : ''}</div>`).join('') : '';
    }).join('') : note('没有未完成的待办'));
  }
  return split('desk', left.join(''), right, 'wk-card dk-todos');
}

// 左右两栏，中间的竖条能拖（设计画布第 75 块）：左栏占多少存在这台设备的工作台设置里
function split(key, left, right, rightClass = 'wk-card') {
  const l = (W().split || {})[key] ?? (key === 'repo' ? 0.58 : 0.52);
  return `<div class="wk-split" data-split="${key}" style="--l:${l}"><div class="wk-col">${left}</div>` +
    `<div class="wk-grip" data-grip="${key}" title="拖动调宽，双击恢复"><i></i></div><div class="wk-col ${rightClass === 'wk-plain' ? '' : rightClass || 'wk-card'}">${right}</div></div>`;
}

function rackHtml() {
  if (!A().rack_status) return secHead('rack', '机柜') + note(NOT_YET('机柜'));
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

// 仓库同步（设计画布第 77 块）：顶上一根分段条看全部仓库；正在跑的任务单独一张大卡；其他任务一行一个；要处理的按原因
const VIEW_SEG = [['synced', '已同步', 'var(--ok)'], ['behind', '落后', 'var(--accent2)'], ['dirty', '有本地修改', 'var(--warn)'], ['failed', '失败', 'var(--bad)'],
                  ['protected', '被拦下', '#c08cff'], ['gone', '上游没了', 'var(--ink3)'], ['skipped', '跳过', 'var(--line)']];
function dur(s) {
  if (s == null) return '';
  return s < 3600 ? `${Math.max(1, Math.round(s / 60))} 分钟` : s < 86400 ? `${Math.floor(s / 3600)} 小时${Math.round(s % 3600 / 60) ? ` ${Math.round(s % 3600 / 60)} 分` : ''}` : `${Math.floor(s / 86400)} 天 ${Math.round(s % 86400 / 3600)} 小时`;
}
function taskBtn(x, op, text, cls = '') {
  return `<button class="tk-btn ${cls}" data-task="${esc(x.id)}" data-op="${op}"${ig.busy['task:' + x.id] ? ' disabled' : ''}>${text}</button>`;
}
function runCard(x) {   // 在跑（或暂停）的那次：两行字 + 一根进度条（设计画布第 77 块，紧凑版）
  const pct = x.total ? Math.round(x.done / x.total * 100) : 0, c = x.counts || {}, paused = x.state === 'paused';
  const since = x.started ? (Date.now() - Date.parse(x.started)) / 1000 : null;
  const meta = [x.host, x.trigger === 'manual' ? '手动开始' : x.trigger ? '定时开始' : '', since ? `已跑 ${dur(since)}` : ''].filter(Boolean).join(' · ');
  const eta = paused ? '' : x.eta_s ? `还要约 ${dur(x.eta_s)}` : '剩余时间算一会儿';
  return `<div class="rp-run${paused ? ' paused' : ''}">` +
    `<div class="rp-run-h"><em class="tk-pill ${paused ? '' : 'run'}">${paused ? '暂停了' : '同步中'}</em><b>${esc(x.name)}</b><small>${esc(meta)}</small>` +
    `<span class="rp-acts">${paused ? taskBtn(x, 'resume', '继续', 'go') : taskBtn(x, 'pause', '暂停')}${taskBtn(x, 'stop', '停止', 'stop')}</span></div>` +
    `<div class="rp-run-p"><b>${pct}%</b><span>${x.done} / ${x.total}</span><span>新克隆 ${c.cloned || 0}</span><span>更新 ${c.updated || 0}</span>` +
    `<span class="${c.failed ? 'bad' : ''}">失败 ${c.failed || 0}</span>${eta ? `<em>${eta}</em>` : ''}</div>` +
    `<div class="tk-bar"><i style="width:${pct}%"></i></div></div>`;
}
function taskRow(x) {
  const dot = x.state !== 'idle' ? 'var(--accent2)' : x.last === 'error' || x.failed ? 'var(--bad)' : x.last === 'stopped' ? 'var(--ink3)' : x.last ? 'var(--ok)' : 'var(--ink3)';
  const last = x.state === 'running' ? '同步中' : x.state === 'paused' ? '暂停了' : x.last === 'error' ? '上次出错' : x.failed ? `上次 ${x.failed} 个失败` : x.last === 'stopped' ? '上次停了' : x.last ? '上次成功' : '还没跑过';
  const acts = x.state === 'idle' ? taskBtn(x, 'run', '立即同步', 'go') : '';
  return `<div class="rp-row"><i style="background:${dot}"></i><div><b>${esc(x.name)}</b><small>${esc([x.host, x.schedule, last].join(' · '))}</small></div>${acts}` +
    `<button class="switch sm" role="switch" data-task="${esc(x.id)}" data-op="schedule" aria-checked="${x.enabled}" aria-label="定时"${ig.busy['task:' + x.id] ? ' disabled' : ''}><i></i></button></div>`;
}
function repoHtml() {
  if (!A().repo_status) return secHead('repo', '仓库同步') + note(NOT_YET('仓库同步'));
  const r = ig.data.repo_status || {};
  if (r.error) return secHead('repo', '仓库同步') + note(`读不到 repo-manager：${r.error}`);
  const v = r.views || { synced: r.synced, failed: r.failed }, sum = VIEW_SEG.reduce((a, [k]) => a + (v[k] || 0), 0) || 1;
  const health = `<div class="rp-health wk-card"><div class="rp-total"><b>${r.total}</b><small>个仓库</small><span></span>` +
    (r.suspect7 ? `<em class="rp-warn">⚠ 最近 7 天疑似删库 ${r.suspect7} 次</em>` : '') + `</div>` +
    `<div class="rp-bar">${VIEW_SEG.filter(([k]) => v[k]).map(([k, , c]) => `<i style="flex:${v[k]};background:${c}"></i>`).join('')}</div>` +
    `<div class="rp-leg">${VIEW_SEG.filter(([k]) => v[k] && k !== 'skipped').map(([k, n, c]) => `<span><u style="background:${c}"></u>${n} <b>${v[k]}</b></span>`).join('')}</div></div>`;
  const tasks = r.tasks || [], active = tasks.filter(x => x.state !== 'idle'), rest = tasks.filter(x => x.state === 'idle');
  const left = (r.tasks == null ? note('读不到同步任务（repo-manager 太旧？）') : '') + active.map(runCard).join('') +
    (rest.length ? `<div class="wk-card rp-rest"><div class="wk-row"><b>${active.length ? '其他同步任务' : '同步任务'} · ${rest.length}</b><small>开关 = 定时</small></div>${rest.map(taskRow).join('')}</div>` : '');
  const reasons = r.reasons || [], open = ig.reason;
  const right = `<div class="wk-card"><div class="wk-row"><b>要处理的 · ${r.attention_total || 0}</b><small>按原因 · 点开看</small></div>` +
    (reasons.length ? reasons.map(x => `<button class="rp-why${open === x.kind ? ' open' : ''}" data-reason="${esc(x.kind)}"><span>${esc(x.label)}</span><em class="${x.kind === 'local_changes' || x.kind === 'protected' ? 'w' : ''}">${x.count}</em></button>` +
      (open === x.kind && x.names.length ? `<div class="rp-names">${x.names.map(n => `<span>${esc(n)}</span>`).join('')}${x.count > x.names.length ? `<small>还有 ${x.count - x.names.length} 个，在 repo-manager 网页里看</small>` : ''}</div>` : '')).join('')
      : note('没有要处理的仓库')) + `</div>` +
    ((r.host_list || []).length ? `<div class="wk-card"><div class="wk-row"><b>服务器</b><small>${r.hosts && r.hosts.down ? `${r.hosts.down} 台连不上` : '都在线'}</small></div>` +
      r.host_list.map(h => `<div class="rp-host"><i class="${r.hosts && r.hosts.down ? 'warn' : ''}"></i><span>${esc(h.name)}</span><small>${h.tasks} 个任务</small></div>`).join('') + '</div>' : '');
  return health + split('repo', left, right, 'wk-plain');
}
async function taskDo(id, op, on) {
  ig.busy['task:' + id] = true; renderWork();
  const r = await postJson('/api/integrations/repo', { op, id, on });
  delete ig.busy['task:' + id];
  if (r.repo_status && ig.data) ig.data.repo_status = r.repo_status;
  if (!r.ok) toast(r.error || '没做成', 'error');
  else toast({ run: '开始同步了', pause: '暂停了', resume: '继续同步', stop: '停下了', schedule: on ? '定时开了' : '定时关了' }[op] || '好了');
  renderWork();
}
function hubPageHtml() {
  if (!A().smart_hub) return secHead('hub', '智能中枢') + note(NOT_YET('智能中枢'));
  return secHead('hub', '智能中枢', ig.data.smart_hub && !ig.data.smart_hub.error ? hubSummary(hubDevices()) : '') + `<div class="wk-hub">${hubHtml().replace(/<p class="h-sum">.*?<\/p>/, '')}</div>`;
}

function renderTabs() {
  const tabs = wkTabs(), r = ig.data && ig.data.rack_status, p = ig.data && ig.data.repo_status;
  const badge = { rack: r && !r.error && r.problems.length ? `<em class="w">${r.problems.length}</em>` : '',
                  repo: p && !p.error && p.failed ? `<em>${p.failed}</em>` : '', desk: '', hub: '' };
  $('wkTabs').hidden = tabs.length < 2;
  set('wkTabs', tabs.map(([k, name, ic]) => `<button role="tab" data-wtab="${k}" class="${k === ig.tab ? 'on' : ''}" aria-selected="${k === ig.tab}">${svg(ic, 20)}<span>${name}</span>${badge[k]}</button>`).join(''));
}
// 机柜那页放 rack-screen 的画面（设置里选了、网关说能放：填了登录密码、登得上）；智能中枢那页可以放它自己的网页
function screenUrl() {
  if (!ig.open || !ig.data || DEMO) return '';
  const u = W().urls || (ui.home_tabs || {}).urls || {};
  if (ig.tab === 'rack' && W().rack === 'screen' && A().rack_status && (ig.data.rack_status || {}).screen) return u.rack || '';
  if (ig.tab === 'hub' && W().hub === 'web' && A().smart_hub) return u.hub || '';
  return '';
}
// 机柜、智能中枢的网页框不删，只藏起来：以前切到别的页、关掉工作台就把它删了，回来要重建、整页重新加载
// （经设备代理 → 网关 → 登录 rack-screen，好几秒黑框）。藏多久在工作台设置里选（设计画布第 79 块，work.keep 分钟：
// 0 不保留，-1 一直保留），过了才放掉，免得它在后台一直轮询
const KEEP = [[0, '不保留'], [5, '5 分钟'], [10, '10 分钟'], [30, '30 分钟'], [-1, '一直保留']];
const keepMs = () => { const k = W().keep; return k === -1 ? Infinity : (typeof k === 'number' ? k : 10) * 60000; };
const frames = {};   // 网址 -> { el, ready, hiddenAt }
function dropFrame(u) { const f = frames[u]; f.el.src = 'about:blank'; f.el.remove(); delete frames[u]; }
function renderScreen() {
  const box = $('wkScreen'), url = screenUrl();
  box.hidden = !url;
  for (const [u, f] of Object.entries(frames)) {
    const on = u === url;
    f.el.hidden = !on;
    f.hiddenAt = on ? 0 : (f.hiddenAt || Date.now());
    if (!on && keepMs() === 0) dropFrame(u);   // 不保留：切走就放掉
  }
  if (!url) return;
  if (!frames[url]) {
    const n = document.createElement('iframe');
    n.title = ig.tab === 'hub' ? '智能中枢' : '机柜';
    const f = frames[url] = { el: n, ready: false, hiddenAt: 0 };
    n.addEventListener('load', () => {
      f.ready = true;
      n.classList.add('ready');   // 画好了再露出来：之前是「正在打开…」，不是一块黑
      if (!n.hidden) $('wkScreenMsg').textContent = '';
    });
    n.src = url;
    box.appendChild(n);
  }
  $('wkScreenMsg').textContent = frames[url].ready ? '' : '正在打开…';
}
setInterval(() => {
  for (const [u, f] of Object.entries(frames)) {
    if (f.hiddenAt && Date.now() - f.hiddenAt > keepMs()) dropFrame(u);
  }
}, 60000);
function renderWork() {
  if (!ig.open || ig.dragging) return;
  if (!ig.data) { set('wkDesk', note('正在连小麦网关…')); return; }
  ig.tab = pickTab(ig.tab);
  renderTabs();
  const screen = !!screenUrl();
  for (const [k, , , id] of WK) $(id).hidden = k !== ig.tab || screen;
  if (ig.tab === 'desk') set('wkDesk', deskHtml());
  if (ig.tab === 'hub' && !screen) set('wkHub', hubPageHtml());
  if (ig.tab === 'rack' && !screen) set('wkRack', rackHtml() + (A().rack_status && W().rack === 'screen' && !(ig.data.rack_status || {}).screen
    ? note('在网关后台「外部服务 › 家里的系统 › 机柜 › 设置」里填 rack-screen 的登录密码，这一页就换成和机柜屏一样的画面。') : ''));
  if (ig.tab === 'repo') set('wkRepo', repoHtml());
  renderScreen();
  if (!$('wkCfg').hidden) renderCfg();
  $('wkSub').textContent = ig.data.at ? `经小麦网关 · ${hm(ig.data.at)} 更新` : '经小麦网关';
}
$('wkTabs').addEventListener('click', e => {
  const b = e.target.closest('[data-wtab]');
  if (!b || b.dataset.wtab === ig.tab) return;
  ig.tab = b.dataset.wtab;
  try { localStorage.setItem('wkTab', ig.tab); } catch (err) { /* 只是记一下上次看的 */ }
  renderWork();
});

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
  const rs = e.target.closest('[data-reason]');
  if (rs) { ig.reason = ig.reason === rs.dataset.reason ? null : rs.dataset.reason; return renderWork(); }
  const tk = e.target.closest('[data-task]');
  if (tk && !tk.disabled) {
    const x = ((ig.data.repo_status || {}).tasks || []).find(v => v.id === tk.dataset.task);
    if (!x) return;
    const op = tk.dataset.op;
    if (op === 'schedule') return taskDo(x.id, op, tk.getAttribute('aria-checked') !== 'true');
    if (op === 'stop') return window.miApi.askConfirm(`停止「${x.name}」这次同步？已经同步完的仓库不受影响。`, () => taskDo(x.id, op));
    return taskDo(x.id, op);
  }
  if (e.target.closest('.wk-hub')) {
    if (hubClick(e)) return;
    const p = e.target.closest('[data-hpower]');
    const px = p ? findDev(p.dataset.hpower) : null;
    if (px) return toggle(px);
    const h = e.target.closest('[data-hub]');
    if (h) return openLight(h.dataset.hub);
    return;
  }
  const s = e.target.closest('[data-src]');
  if (s) {
    const d = ig.data.desk_hub;
    if (s.dataset.src === d.current) return toast('显示器已经在这个输入源了');
    return deskDo('switch', s.dataset.src);
  }
  const u = e.target.closest('[data-undo]');
  if (u) { const x = ig.undo[u.dataset.undo]; if (x) { clearTimeout(x.timer); delete ig.undo[x.id]; renderWork(); } return; }
  const t = e.target.closest('[data-todo]');
  if (t && !t.disabled) {   // 划掉后 3 秒内能撤销，过了才真去 Mac 上完成
    const x = ((ig.data.desk_hub || {}).todos || []).find(v => v.id === t.dataset.todo);
    if (!x) return;
    ig.undo[x.id] = { id: x.id, title: x.title, timer: setTimeout(() => { delete ig.undo[x.id]; deskDo('todo_done', x.id); }, 3000) };
    return renderWork();
  }
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
// 智能中枢的设备块（设计画布第 72 块）：按灯、可调电源、红外分组；连不上说网关翻好的中文原因，不显示 MAC 和英文报错
const POWER_SVG = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 3.5v8M7 6.3a7.5 7.5 0 1 0 10 0"/></svg>';
const NODE_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="2.5"/><path d="M5 5l4.5 4.5M19 5l-4.5 4.5M5 19l4.5-4.5M19 19l-4.5-4.5"/></svg>';
const f2 = v => (v === null || v === undefined ? '—' : v.toFixed(2));
function hubTile(x) {
  const on = x.on === true, off = x.online === false;
  const power = x.kind === 'light' || (x.kind === 'other' && (x.actions.includes('toggle') || x.actions.includes('on')));
  const via = x.via && !off ? `<em class="h-via">${NODE_SVG}${esc(x.via)}</em>` : '';
  let body = '';
  if (x.kind === 'psu' && !off && on) {
    body = `<div class="h-read"><b>${f2(x.v_out)}<small>V</small></b><b>${f2(x.i_out)}<small>A</small></b></div>` +
      `<span>${x.p_out !== null && x.p_out !== undefined ? x.p_out.toFixed(1) + ' W · ' : ''}${x.mode === 'cc' ? '稳流' : '稳压'} · 输出开</span>`;
  } else if (x.kind === 'ir' && !off) {
    body = `<span>红外</span><div class="h-keys">${(x.buttons || []).slice(0, 3).map(b => `<button data-hpress="${esc(x.id)}" data-btn="${esc(b.id)}"${ig.busy[x.id] ? ' disabled' : ''}>${esc(b.name)}</button>`).join('')}</div>`;
  } else {
    body = `<span>${esc(x.summary || '')}</span>` + (off && (x.tried || []).length ? `<span class="h-tried">${x.tried.length} 个节点试过 · 点开看</span>` : '');
  }
  const bar = x.kind === 'light' && on && x.brightness !== null && x.brightness !== undefined ? `<div class="h-bar"><i style="width:${Math.round(x.brightness)}%"></i></div>` : '';
  return `<div class="h-tile hub k-${esc(x.kind)}${on && !off ? ' on' : ''}${off ? ' offline' : ''}${via ? ' has-via' : ''}" data-hub="${esc(x.id)}">
    <div class="h-ic">${svg(x.kind === 'psu' ? 'psu' : x.kind === 'ir' ? 'ir' : x.group ? 'group' : 'light', 30)}</div>` +
    (power ? `<button class="h-power${on ? ' on' : ''}" data-hpower="${esc(x.id)}" aria-label="开关">${POWER_SVG}</button>` : '') +
    `<b>${esc(x.name)}</b>${body}${bar}${via}</div>`;
}
const HUB_GROUPS = [['light', '灯', 'light'], ['psu', '可调电源', 'psu'], ['ir', '红外', 'ir'], ['other', '其他', 'light']];
function hubSummary(devs) {
  const n = k => devs.filter(x => x.kind === k), lights = n('light').filter(x => !x.group), psus = n('psu'), irs = n('ir');
  const down = psus.filter(x => x.online === false).length;
  return [lights.length ? `${lights.length} 盏灯` + (lights.some(x => x.on) ? `（${lights.filter(x => x.on).length} 盏开着）` : '') : '',
    psus.length ? `${psus.length} 台电源` + (down ? (down === psus.length ? '（都连不上）' : `（${down} 台连不上）`) : '') : '',
    irs.length ? `${irs.length} 个红外设备` : ''].filter(Boolean).join(' · ');
}
function hubGrid(devs) {
  return HUB_GROUPS.map(([k, name, ic]) => {
    const list = devs.filter(x => (x.kind === k) || (k === 'other' && !['light', 'psu', 'ir'].includes(x.kind)));
    return list.length ? `<div class="h-grp"><div class="h-gh">${svg(ic, 16)}${name}</div><div class="h-tiles">${list.map(hubTile).join('')}</div></div>` : '';
  }).join('');
}
function hubHtml() {
  const s = (ig.data && ig.data.smart_hub) || {}, devs = s.devices || [];
  return s.error ? `<p class="h-msg bad">${esc('读不到智能中枢：' + s.error)}</p>`
    : devs.length ? `<p class="h-sum">${esc(hubSummary(devs))}</p>${hubGrid(devs)}` : '<p class="h-msg">智能中枢里还没有设备</p>';
}
// home.js 的 renderHome 调：返回这台设备有没有接入智能中枢；shown：家居页现在在「智能中枢」那一页（设计画布第 45 块）
window.hubAvailable = () => !!A().smart_hub;
window.rackAvailable = () => !!A().rack_status;   // 家居的「机柜」分页（home.js，设计画布第 62 块）
window.hubRender = shown => {
  const box = $('hHub');
  if (!A().smart_hub) { box.hidden = true; return false; }
  const html = hubHtml();
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
function hubClick(e) {
  const k = e.target.closest('[data-hpress]');
  const kx = k ? findDev(k.dataset.hpress) : null;
  if (kx) { hubAct(kx.id, 'press', k.dataset.btn); return true; }
  return false;
}
window.hubClick = hubClick;
$('hHub').addEventListener('click', e => {
  if (hubClick(e)) return;
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
  $('ldSum').textContent = (x.summary || '') + (x.via && x.online !== false ? ` · 经 ${x.via} 节点` : '');
  const box = $('ldControls');
  if (!first && box.contains(document.activeElement) && document.activeElement.type === 'range') return;
  const parts = [], has = a => x.actions.includes(a);
  if (x.online === false) {   // 连不上（设计画布第 72 块）：一句中文原因，再列出哪些节点试过、各自为什么不行
    if ((x.tried || []).length) parts.push(`<div class="h-triedlist">${x.tried.map(n => `<div><b>${esc(n.node)}</b><span>${esc(n.why)}</span></div>`).join('')}</div>`);
    parts.push('<p class="opt-note">电源要开着，并且离某个节点（ESP32 或打开了「当智能中枢的蓝牙节点」的屏）不太远。</p>');
    box.innerHTML = parts.join('');
    return;
  }
  if (x.kind === 'ir') {
    parts.push(`<div class="h-keys big">${(x.buttons || []).map(b => `<button data-hpress="${esc(x.id)}" data-btn="${esc(b.id)}">${esc(b.name)}</button>`).join('')}</div>`);
    parts.push(`<p class="opt-note">点一个键，经${x.via ? ` ${esc(x.via)} ` : '能发红外的'}节点发出去。</p>`);
    box.innerHTML = parts.join('');
    return;
  }
  if (x.kind === 'psu') {
    parts.push(`<div class="opt"><div class="opt-text"><b>输出</b><span>开关输出要确认</span></div>
      <button class="switch" data-lop="psu" role="switch" aria-checked="${x.on ? 'true' : 'false'}"><i></i></button></div>`);
    const r = [['设定', x.v_set, 'V'], ['设定', x.i_set, 'A'], ['输出', x.v_out, 'V'], ['输出', x.i_out, 'A'], ['功率', x.p_out, 'W']]
      .filter(([, v]) => v !== null && v !== undefined).map(([k, v, u]) => `<span>${k}：${v.toFixed(u === 'W' ? 1 : 2)} ${u}</span>`);
    if (x.model) r.push(`<span>型号：${esc(x.model)}</span>`);

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
  if (window.hubClick && window.hubClick(e)) return;
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

/* ---------------- 工作台设置（设计画布第 75 块）：右上角齿轮 ---------------- */
async function sendWork(changes) {
  if (DEMO) { ui.work = Object.assign({}, W(), changes, changes.split ? { split: Object.assign({}, W().split, changes.split) } : {}); renderWork(); return; }
  const r = await postJson('/api/work', changes);
  if (r && r.ok) handlers.work(r.work); else toast((r && r.error) || '没保存上', 'error');
}
function showCfg(on) {
  $('wkCfg').hidden = !on;
  $('wkCfgBtn').classList.toggle('on', !!on);
  if (on) renderCfg();
}
function renderCfg() {
  const w = W(), avail = wkOrdered().filter(x => A()[x[4]]);
  const chip = (key, val, text) => `<button data-wset="${key}" data-v="${val}" class="${w[key] === val ? 'on' : ''}">${text}</button>`;
  const rows = avail.map(([k, name, ic], i) => `<div class="wc-row"><span class="wc-ic">${svg(ic, 20)}</span><b>${name}</b>` +
    `<button class="wc-mv" data-move="${k}" data-d="-1"${i === 0 ? ' disabled' : ''} aria-label="往前">↑</button>` +
    `<button class="wc-mv" data-move="${k}" data-d="1"${i === avail.length - 1 ? ' disabled' : ''} aria-label="往后">↓</button>` +
    `<button class="switch sm" role="switch" data-show="${k}" aria-checked="${!w.hidden.includes(k)}" aria-label="显示${name}"><i></i></button></div>`).join('');
  set('wkCfg', `<div class="wc-head"><b>工作台设置</b><button class="wc-x" data-wclose="1" aria-label="关闭">✕</button></div>` +
    `<div class="wc-sub">显示哪几页、顺序</div>${rows || '<p class="wc-note">这台设备还没接入任何一个</p>'}` +
    `<div class="wc-sub">打开工作台先看</div><div class="wc-chips">${chip('first', '', '上次看的')}${avail.map(([k, n]) => chip('first', k, n)).join('')}</div>` +
    (A().rack_status ? `<div class="wc-sub">机柜那页放</div><div class="wc-chips">${chip('rack', 'screen', '机柜屏的画面')}${chip('rack', 'list', '简要列表')}</div>` : '') +
    (A().smart_hub ? `<div class="wc-sub">智能中枢那页放</div><div class="wc-chips">${chip('hub', 'tiles', '设备块')}${chip('hub', 'web', '它自己的网页')}</div>` : '') +
    // 有网页要保留时才有这一行（机柜放机柜屏的画面、或智能中枢放它自己的网页）
    ((A().rack_status && w.rack === 'screen') || (A().smart_hub && w.hub === 'web')
      ? `<div class="wc-sub">切走后保留网页</div><div class="wc-chips">${KEEP.map(([m, t]) => `<button data-keep="${m}" class="${(w.keep ?? 10) === m ? 'on' : ''}">${t}</button>`).join('')}</div>` +
        '<p class="wc-note">保留着，回来不用重新加载；保留越久，它在后台刷新数据越久。</p>' : '') +
    `<div class="wc-row"><b>左右栏宽度</b><button class="wc-btn" data-wreset="1"${Object.keys(w.split || {}).length ? '' : ' disabled'}>恢复默认</button></div>` +
    `<div class="wc-row"><div><b>没人碰 3 分钟回首页</b><small>关掉就一直开着（叫它时也不收起）</small></div><button class="switch sm" role="switch" data-idle="1" aria-checked="${w.idle_close}"><i></i></button></div>`);
}
$('wkCfgBtn').addEventListener('click', () => showCfg($('wkCfg').hidden));
$('wkCfg').addEventListener('click', e => {
  const w = W(), t = e.target;
  if (t.closest('[data-wclose]')) return showCfg(false);
  const mv = t.closest('[data-move]');
  if (mv) {
    const order = w.order.slice(), avail = wkOrdered().filter(x => A()[x[4]]).map(x => x[0]);
    const i = avail.indexOf(mv.dataset.move), j = i + Number(mv.dataset.d);
    if (j < 0 || j >= avail.length) return;
    const a = order.indexOf(avail[i]), b = order.indexOf(avail[j]);
    [order[a], order[b]] = [order[b], order[a]];
    return sendWork({ order });
  }
  const sh = t.closest('[data-show]');
  if (sh) {
    const k = sh.dataset.show, hidden = w.hidden.includes(k) ? w.hidden.filter(x => x !== k) : [...w.hidden, k];
    if (wkOrdered().filter(x => A()[x[4]] && !hidden.includes(x[0])).length === 0) return toast('至少留一页', 'info');
    return sendWork({ hidden });
  }
  const c = t.closest('[data-wset]');
  if (c) return sendWork({ [c.dataset.wset]: c.dataset.v });
  const kp = t.closest('[data-keep]');
  if (kp) return sendWork({ keep: Number(kp.dataset.keep) });
  if (t.closest('[data-wreset]')) return sendWork({ split: null });
  if (t.closest('[data-idle]')) return sendWork({ idle_close: !w.idle_close });
});
handlers.work = v => { ui.work = v; renderWork(); if (!$('wkCfg').hidden) renderCfg(); };

// 拖中间的竖条调两栏宽度：拖的时候不重画，松手存
$('wkCols').addEventListener('pointerdown', e => {
  const g = e.target.closest('[data-grip]');
  if (!g) return;
  const box = g.closest('.wk-split'), key = g.dataset.grip;
  e.preventDefault();
  g.setPointerCapture(e.pointerId);
  ig.dragging = true;
  let l = Number(getComputedStyle(box).getPropertyValue('--l')) || 0.5;
  const move = ev => {
    const r = box.getBoundingClientRect();
    l = Math.min(0.7, Math.max(0.3, (ev.clientX - r.left) / r.width));
    box.style.setProperty('--l', l.toFixed(3));
  };
  const up = () => {
    g.removeEventListener('pointermove', move);
    ig.dragging = false;
    sendWork({ split: { [key]: Number(l.toFixed(3)) } });
  };
  g.addEventListener('pointermove', move);
  g.addEventListener('pointerup', up, { once: true });
  g.addEventListener('pointercancel', up, { once: true });
});
$('wkCols').addEventListener('dblclick', e => { const g = e.target.closest('[data-grip]'); if (g) sendWork({ split: { [g.dataset.grip]: null } }); });

// 没人碰 3 分钟回首页（设置里能关）
document.addEventListener('pointerdown', () => { ig.touched = Date.now(); }, true);
setInterval(() => { if (ig.open && W().idle_close && Date.now() - (ig.touched || 0) > 180000) openWork(false); }, 15000);

/* ---------------- 仪表盘卡片（dash.js 调） ---------------- */
window.igApi = {
  available: A,
  workCard(head) {
    const cells = hasWork() ? workCells() : [];
    if (!cells.length) return head('▭', '工作台') + '<button class="d-empty" data-work="1">在小麦网关后台的插件里接入机柜、仓库同步或 DeskHub，这里就有内容</button>';
    const r = ig.data.rack_status, prob = r && !r.error && r.problems.length ? `<div class="dm d-bad">● ${esc(r.problems[0])}</div>` : '';
    return head('▭', '工作台', '›') + `<button class="d-nums d-work" data-work="1">${cells.map(c => `<div><b class="${c.bad ? 'd-bad' : ''}">${esc(c.b).replace(/([\u4e00-\u9fff]+)/g, '<i>$1</i>')}</b><small>${esc(c.s.split(' · ')[0])}</small></div>`).join('')}</button>` + prob;
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
  hubTiles: hubHtml,
  hubSummary: () => hubSummary(hubDevices()),
};

/* ---------------- 设置 › 智能体 › 接入的服务（设计画布第 40 块） ---------------- */
const SVC = [['desk_hub', '智能桌面（DeskHub）', '连接口令'], ['rack_status', '机柜', ''], ['repo_status', '仓库同步', ''], ['smart_hub', '蓝牙灯（智能中枢）', '访问令牌']];
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
  }).join('') + hubNodeRow());
  $('svcNote').textContent = P ? '关掉后，顶栏的工作台按钮、首页和仪表盘上的那一块、语音命令都一起没有。只影响这台设备；别的插件在网关后台的「设备」页改。'
    : '连不上小麦网关，过一会儿再试。';
}
// 当智能中枢的蓝牙节点（设计画布第 73 块）：这台的蓝牙替智能中枢连灯和电源；接了智能中枢才显示
function hubNodeRow() {
  const P = ig.data && ig.data.plugins, s = P && P.smart_hub;
  if (!s || !s.on) return '';
  const n = ui.hub_node || { on: false, state: 'off' };
  const sub = !n.on ? '用这台的蓝牙替智能中枢连灯和电源，和 ESP32 节点一样出现在它的「节点管理」里'
    : n.state === 'online' ? `已连上智能中枢 · 节点 ${esc(n.node)}${n.conns ? ` · 现在连着 ${n.conns} 个设备` : ''}`
    : n.state === 'connecting' ? '正在连智能中枢…'
    : `<em class="svc-warn">${esc(n.error || '还没连上')}</em>`;
  return `<div class="opt rot-auto svc-row"><div class="opt-text"><b>当智能中枢的蓝牙节点</b><span>${sub}</span></div>`
    + `<button class="switch" role="switch" data-hubnode="1" aria-checked="${!!n.on}" aria-label="当智能中枢的蓝牙节点"${ig.busy.hubnode ? ' disabled' : ''}><i></i></button></div>`;
}
handlers.hub_node = v => { ui.hub_node = v; renderSvc(); };
handlers.identify = ({ ms, by }) => {   // 智能中枢在「节点管理」里点了「识别」：整块屏闪几秒
  document.body.classList.remove('identify'); void document.body.offsetWidth; document.body.classList.add('identify');
  toast(`${by || '智能中枢'}在找这台设备`, 'info');
  setTimeout(() => document.body.classList.remove('identify'), ms || 5000);
};
async function setHubNode(on) {
  ig.busy.hubnode = true; renderSvc();
  const r = await postJson('/api/hub-node', { on });
  ig.busy.hubnode = false;
  if (r.ok) ui.hub_node = r.hub_node; else toast(r.error || '没改成', 'error');
  renderSvc();
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
  const hn = e.target.closest('[data-hubnode]');
  if (hn && !hn.disabled) return setHubNode(hn.getAttribute('aria-checked') !== 'true');
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
  if (s.hub_node) ui.hub_node = s.hub_node;
  if (s.work) ui.work = s.work;
  renderAll();
};
handlers.integrations = d => { ig.data = d; renderAll(); };
// 叫醒时工作台和灯的详情也收起（和家居页一样）
const baseAutoClose = window.miAutoClose;
window.miAutoClose = () => { if (baseAutoClose) baseAutoClose(); if (ig.open && W().idle_close) openWork(false); closeLight(); };
window.openWork = openWork;
})();
