// 仪表盘（设计画布第 35 块）：首页布局选「仪表盘」时，顶上换成页签（首页、家居、日程、对话），
// 「对话」就是原来的首页（app.js 的 main）。数据都是页面已有的：ui.*（app.js）和 window.miApi（home.js 的米家）。
// 每秒看一次要不要重画，内容没变不动 DOM（和 home.js 的磁贴一样），设备上很省。对话一开始（叫醒、在想、在说）
// 自动切到「对话」，说完 8 秒回到原来那一页。
(() => {
const D = { tab: 'home', talk: false, idleAt: 0, room: '全屋', cost: null, costAt: 0, sig: {} };
try { D.tab = localStorage.getItem('dashTab') || 'home'; } catch (e) { /* private storage */ }
if (!['home', 'smart', 'plan', 'chat'].includes(D.tab)) D.tab = 'home';

const on = () => !!(ui.looks && ui.looks.layout === 'dashboard');
const mi = () => (window.miApi && window.miApi.view()) || null;
const n0 = v => (v === null || v === undefined ? '?' : Math.round(v));
const set = (id, html) => { if (D.sig[id] !== html) { D.sig[id] = html; $(id).innerHTML = html; } };
const head = (ic, title, small = '') => `<div class="dc-h"><span class="dc-ic">${ic}</span><b>${title}</b>${small ? `<small>${small}</small>` : ''}</div>`;
const WD = ['日', '一', '二', '三', '四', '五', '六'];

function setTab(t) {
  D.tab = t;
  try { localStorage.setItem('dashTab', t); } catch (e) { /* private storage */ }
  apply();
  render(true);
}
function apply() {
  const dash = on();
  document.body.classList.toggle('dash-on', dash);
  const shown = D.talk ? 'chat' : D.tab;
  document.body.dataset.dash = dash ? shown : '';
  document.querySelectorAll('#dashTabs [data-dt]').forEach(b => b.classList.toggle('on', b.dataset.dt === shown));
  document.querySelectorAll('.dash-pane').forEach(p => p.classList.toggle('on', p.dataset.dp === shown));
  if (window.miApi) window.miApi.visible(dash && shown === 'smart' && !document.hidden);
}

/* ---- 首页 ---- */
function weather() {
  const w = ui.today && ui.today.weather, now = new Date(), lunar = ui.today && ui.today.lunar;
  const t = w && w.today || {};
  return `<div class="dw-top"><div><div class="dw-time" id="dTime">${pad(now.getHours())}:${pad(now.getMinutes())}</div>` +
    `<div class="dm">${now.getMonth() + 1}月${now.getDate()}日 周${WD[now.getDay()]}${lunar && lunar.text ? ' · ' + esc(lunar.text) : ''}</div></div>` +
    (w ? `<div class="dw-now"><span class="dw-ic">${esc(w.icon || '')}</span><b>${n0(w.temp)}°</b><span class="dm">${esc(w.place || '')} · ${esc(w.text || '')}</span></div>` : '') + '</div>' +
    (w ? `<div class="dm dw-row"><span>${n0(t.min)}° / ${n0(t.max)}°</span>${t.rain !== null && t.rain !== undefined ? `<span>降水 ${n0(t.rain)}%</span>` : ''}${w.air ? `<span class="d-ok">空气${esc(w.air)}</span>` : ''}</div>`
      : `<div class="dm">${esc((ui.today && ui.today.weather_error) || '天气还没取到')}</div>`);
}
function todoItems(max) {
  const l = ui.lists && ui.lists.lists;
  if (!l) return [];
  const out = [];
  for (const name of Object.keys(l).sort((a, b) => (a === '购物清单' ? -1 : b === '购物清单' ? 1 : 0)))
    for (const x of l[name]) if (!x.done) out.push({ list: name, text: x.text });
  return out.slice(0, max);
}
function todo() {
  const items = todoItems(3);
  return head('✓', '待办', ui.lists ? Object.keys(ui.lists.lists).join(' · ') : '') +
    (items.length ? items.map(x => `<button class="d-todo" data-check="${esc(x.list)}" data-text="${esc(x.text)}"><i></i>${esc(x.text)}</button>`).join('')
      : '<div class="dm">清单是空的。说「把牛奶加到购物清单」</div>');
}
function alarmRows(max) {
  const a = ui.alarms;
  return a && a.items ? a.items.slice(0, max).map(v =>
    `<div class="d-ev"><b>${v.kind === 'timer' ? '倒计时' : dayText(v.at) + ' ' + hhmm(v.at)}</b>${esc(v.label || ({ alarm: '闹钟', reminder: '提醒', timer: '', broadcast: '天气播报' }[v.kind] || ''))}</div>`).join('') : '';
}
function alarms() {
  const rows = alarmRows(4);
  return head('⏰', '闹钟和提醒') + (rows || '<div class="dm">没有闹钟。说「明早 7 点叫我」</div>');
}
function brief() {
  const h = new Date().getHours();
  const hi = h < 5 ? '夜深了' : h < 9 ? '早上好' : h < 12 ? '上午好' : h < 14 ? '中午好' : h < 18 ? '下午好' : h < 23 ? '晚上好' : '夜深了';
  const w = ui.today && ui.today.weather, t = w && w.today || {};
  const said = [];
  if (w) said.push(`今天${esc(w.text || '')} ${n0(t.min)}~${n0(t.max)}°${t.rain >= 50 ? '，可能下雨，出门带伞' : ''}。`);
  const next = ui.alarms && ui.alarms.items.find(v => v.kind !== 'timer');
  if (next) said.push(`${dayText(next.at)} ${hhmm(next.at)} ${esc(next.label || (next.kind === 'alarm' ? '有闹钟' : '有提醒'))}；`);
  const todos = todoItems(99).length;
  said.push(todos ? `清单里还有 ${todos} 样。` : '清单都办完了。');
  const tip = !w ? '问我「明天下雨吗」' : t.rain >= 50 ? '下雨概率高，带把伞' : (t.max - t.min >= 8) ? `早晚温差 ${n0(t.max - t.min)}°，加件外套`
    : w.air && /差|污染/.test(w.air) ? '空气不太好，少开窗' : '天气不错，适合出去走走';
  const today = (ui.alarms && ui.alarms.items || []).filter(v => v.kind !== 'timer' && new Date(v.at * 1000).toDateString() === new Date().toDateString());
  return `<div class="db-top"><span class="d-orb big"></span><div><div class="db-hi">${hi}！</div><div class="dm db-say">${said.join('')}</div></div></div>` +
    `<div class="db-boxes"><div><b>今天要做</b><span class="dm">${today.length ? today.slice(0, 2).map(v => `· ${hhmm(v.at)} ${esc(v.label || '')}`).join('<br>') : '· 没有提醒'}</span></div>` +
    `<div><b>生活建议</b><span class="dm">${tip}</span></div>` +
    `<div><b>清单</b><span class="dm">${todos ? todoItems(2).map(x => '· ' + esc(x.text)).join('<br>') : '· 都办完了'}</span></div></div>`;
}
function devTile(c) {
  const api = window.miApi, s = c.state || {};
  const sw = c.controls && c.controls.power && c.online ? `<span class="d-sw${api.isOn(c) ? ' on' : ''}"></span>` : '';
  const bars = [];
  if (s.brightness !== undefined && api.isOn(c)) bars.push(`<div class="d-bar"><i style="transform:scaleX(${(s.brightness || 0) / 100})"></i></div>`);
  // 第一行图标和名字（名字要看得全），第二行状态和开关
  return `<button class="d-dev${api.isOn(c) ? ' on' : ''}${c.online ? '' : ' off'}" data-dev="${esc(c.id)}"><div class="dd-r"><span class="dd-ic">${api.icon(c.kind, 20)}</span>` +
    `<b>${esc(c.name)}</b></div><div class="dd-r"><span class="dm${api.isOn(c) ? ' d-ok' : ''}">${esc(c.online ? c.summary : '离线')}</span>${sw}</div>${bars.join('')}</button>`;
}
function devices() {
  const v = mi();
  if (!v || !v.account || !window.miApi) return head('⌂', '常用设备') + '<button class="d-empty" data-open="home">在「家居」里用米家 App 扫码登录，常用设备就会出现在这里</button>';
  const all = v.rooms.flatMap(r => r.cards);
  const favs = (v.favorites && v.favorites.length ? v.favorites : all).slice(0, 6);
  return head('⌂', '常用设备', `米家 · ${all.filter(c => c.online).length} 个在线`) +
    (favs.length ? `<div class="d-devs">${favs.map(devTile).join('')}</div>` : '<div class="dm">米家里还没有设备</div>');
}
function scenes() {
  const x = (ui.scenes && ui.scenes.scenes) || [];
  return head('✦', '场景') + (x.length ? `<div class="d-scenes">${x.slice(0, 4).map(sc => `<button data-scene="${esc(sc.name)}"><b>${esc(sc.name)}</b><span class="dm">${esc(sc.steps)}</span></button>`).join('')}</div>`
    : '<div class="dm">说「以后我说『我出门了』，就把灯和空调都关了」，就会多一个场景</div>');
}
function media() {
  const m = ui.media || {}, now = m.playing;
  if (now) {
    const left = now.ends ? Math.max(0, Math.round((now.ends - Date.now() / 1000) / 60)) : null;
    return head('♪', '电台和助眠', '正在放') + `<div class="dm-row"><span class="dm-cover"></span><div><b>${esc(now.name)}</b><div class="dm">${now.kind === 'radio' ? '电台直播' : left !== null ? `还有 ${left} 分钟停` : '一直放'}</div></div>` +
      '<button class="da-mic small" data-media="stop" aria-label="停">❚❚</button></div>';
  }
  return head('♪', '电台和助眠') + `<div class="d-chips">${(m.stations || []).slice(0, 3).map(s => `<button data-media="${esc(s.id)}">${esc(s.name)}</button>`).join('')}</div>`;
}
function nums() {
  const v = mi(), plugs = v && v.account ? v.rooms.flatMap(r => r.cards).filter(c => c.state && typeof c.state.power_w === 'number') : [];
  const watts = plugs.reduce((a, c) => a + c.state.power_w, 0);
  const k = ui.kids;
  const cells = [];
  if (D.cost !== null) cells.push(`<div><b>${esc(D.cost)}</b><span class="dm">大模型费用</span></div>`);
  if (k && k.enabled) cells.push(`<div><b>${k.used_minutes}<small> 分</small></b><span class="dm">孩子聊了</span></div>`);
  if (plugs.length) cells.push(`<div><b>${Math.round(watts)}<small> W</small></b><span class="dm">插座正在用电</span></div>`);
  return head('◷', '今天') + (cells.length ? `<div class="d-nums">${cells.join('')}</div>` : '<div class="dm">还没有数字</div>');
}

/* ---- 家居 ---- */
function smart() {
  const v = mi();
  if (!v || !v.account) {
    set('dHomeRooms', head('⌂', '我的家') + '<button class="d-empty" data-open="home">还没登录米家：点这里扫码</button>');
    set('dKinds', ''); set('dRooms', ''); set('dScene2', scenes());
    return;
  }
  const all = v.rooms.flatMap(r => r.cards), home = (v.homes || []).find(h => h.id === v.home_id);
  const rooms = ['全屋', ...v.rooms.map(r => r.name)];
  if (!rooms.includes(D.room)) D.room = '全屋';
  set('dHomeRooms', `<div class="dc-h"><b class="dh-name">${esc(home ? home.name : '我的家')}</b></div><span class="dm d-ok">● ${all.length} 个设备 · ${all.filter(c => c.online).length} 个在线</span>` +
    rooms.map(r => `<button class="d-room${r === D.room ? ' on' : ''}" data-room="${esc(r)}">${esc(r)}</button>`).join(''));
  set('dScene2', scenes());
  const kinds = [['light', '灯', c => `${all.filter(x => x.kind === 'light' && window.miApi.isOn(x)).length} 个开着`],
    ['plug', '插座', () => { const w = all.filter(x => x.state && typeof x.state.power_w === 'number').reduce((a, x) => a + x.state.power_w, 0); return w ? `正在用 ${Math.round(w)} W` : '没在用电'; }],
    ['sensor', '传感器', () => { const s = all.find(x => x.state && typeof x.state.temp === 'number'); return s ? `${s.state.temp}°${typeof s.state.humidity === 'number' ? ' · ' + s.state.humidity + '%' : ''}` : ''; }],
    ['cover', '窗帘', () => `${all.filter(x => x.kind === 'cover' && window.miApi.isOn(x)).length} 个开着`],
    ['climate', '空调暖气', () => `${all.filter(x => x.kind === 'climate' && window.miApi.isOn(x)).length} 个开着`]]
    .map(([k, name, sub]) => [k, name, all.filter(x => x.kind === k).length, sub]).filter(x => x[2]).slice(0, 4);
  set('dKinds', kinds.map(([k, name, count, sub]) => `<div class="dc d-kind"><span class="dd-ic">${window.miApi.icon(k, 22)}</span><b>${name} ${count}</b><span class="dm">${sub()}</span></div>`).join(''));
  const shown = D.room === '全屋' ? v.rooms : v.rooms.filter(r => r.name === D.room);
  set('dRooms', shown.map(r => `<div class="dc d-roomcard"><div class="dc-h"><b>${esc(r.name)}</b><small>${r.cards.length} 个设备</small></div>` +
    `<div class="d-devs wide">${r.cards.map(devTile).join('')}</div></div>`).join('') || '<div class="dc dm">这个房间没有设备</div>');
}

/* ---- 日程 ---- */
function plan() {
  const a = (ui.alarms && ui.alarms.items) || [], byDay = {};
  for (const v of a) {
    const d = new Date(v.at * 1000), key = d.toDateString();
    (byDay[key] = byDay[key] || { d, items: [] }).items.push(v);
  }
  set('dPlanAlarms', head('⏰', '闹钟和提醒', '说一句就加') + (Object.values(byDay).slice(0, 4).map(({ d, items }) =>
    `<div class="d-day"><div class="dd-date"><span class="dm">${d.getMonth() + 1}月</span><b>${d.getDate()}</b><span class="dm">周${WD[d.getDay()]}</span></div><div>` +
    items.map(v => `<div class="d-ev"><b>${hhmm(v.at)}</b>${esc(v.label || { alarm: '闹钟', reminder: '提醒', timer: '倒计时', broadcast: '天气播报' }[v.kind] || '')}${v.repeat ? ` <span class="dm">${esc(v.repeat)}</span>` : ''}</div>`).join('') +
    '</div></div>').join('') || '<div class="dm">没有闹钟和提醒。说「明早 7 点叫我」「10 分钟后提醒我晾衣服」</div>'));
  const l = ui.lists && ui.lists.lists;
  set('dPlanLists', head('✓', '清单') + (l ? Object.keys(l).map(name => `<div class="dp-list"><b>${esc(name)}</b>` +
    (l[name].length ? l[name].slice(0, 8).map(x => `<button class="d-todo${x.done ? ' done' : ''}" data-check="${esc(name)}" data-text="${esc(x.text)}" data-done="${x.done ? 1 : 0}"><i></i>${esc(x.text)}</button>`).join('') : '<div class="dm">空的</div>') + '</div>').join('')
    : '<div class="dm">清单还没取到</div>'));
  const t = (ui.tasks && ui.tasks.tasks) || [], notes = (ui.notes && ui.notes.notes) || [];
  set('dPlanTasks', head('↻', '定时任务') + (t.length ? t.map(x => `<div class="d-ev"><b>${esc(x.time)}</b>${esc(x.prompt)}</div>`).join('') : '<div class="dm">说「每天早上七点告诉我要不要带伞」</div>') +
    head('✉', '留言') + (notes.length ? notes.slice(0, 4).map(n => `<div class="d-ev">${n.from ? esc(n.from) + ' → ' : '→ '}${esc(n.to)}：${esc(n.text)}</div>`).join('') : '<div class="dm">说「给爸爸留言：……」</div>'));
}

/* ---- 底部一条：随时能说话 ---- */
const ASK = ['明天下雨吗？', '放雨声半小时', '我有哪些闹钟', '讲个冷笑话'];
function ask() {
  const st = ui.status && ui.status.state;
  $('dAskSub').textContent = st === 'listening' ? '在听…' : st === 'thinking' ? '在想…' : st === 'speaking' ? '在说…'
    : `我在呢，叫「${(ui.wake && ui.wake.primary) || '小麦小麦'}」或者点话筒`;
  set('dAskChips', ASK.map(q => `<button data-ask="${esc(q)}">${esc(q)}</button>`).join(''));
}

function render(force) {
  if (force) D.sig = {};
  apply();
  if (!on()) return;
  ask();
  if (D.tab === 'home' || force) {
    set('dWx', weather()); set('dTodo', todo()); set('dAlarm', alarms()); set('dBrief', brief());
    set('dDev', devices()); set('dScene', scenes()); set('dMedia', media()); set('dNums', nums());
  }
  if (D.tab === 'smart' || force) smart();
  if (D.tab === 'plan' || force) plan();
}

async function loadCost() {
  if (DEMO || !on() || Date.now() - D.costAt < 10 * 60e3) return;
  D.costAt = Date.now();
  try {
    const d = await (await fetch('/api/billing')).json();
    if (d && d.ok !== false) {
      const sym = c => (d.currencies || {})[c] || c + ' ';
      const p = Object.entries(d.today || {}).filter(([, v]) => v > 0).map(([c, v]) => sym(c) + (v < 1 ? v.toFixed(2) : v.toFixed(1)));
      D.cost = p.length ? p.join(' + ') : '¥0';
    }
  } catch (e) { /* the hub is away: no number */ }
}

// 对话一开始切到「对话」页，回到待机 8 秒后切回来
function watchTalk() {
  if (!on()) return;
  const st = ui.status && ui.status.state;
  const busy = st && !['idle', 'sleep', 'offline'].includes(st) || !!(ui.photo);
  if (busy) { D.idleAt = 0; if (!D.talk && D.tab !== 'chat') { D.talk = true; apply(); } }
  else if (D.talk) {
    if (!D.idleAt) D.idleAt = Date.now();
    else if (Date.now() - D.idleAt > 8000) { D.talk = false; D.idleAt = 0; apply(); render(true); }
  }
}

$('dashTabs').addEventListener('click', e => { const b = e.target.closest('[data-dt]'); if (b) { D.talk = false; setTab(b.dataset.dt); } });
$('dash').addEventListener('click', e => {
  const t = e.target;
  const dev = t.closest('[data-dev]');
  if (dev && window.miApi) {
    const c = window.miApi.findCard(dev.dataset.dev);
    if (c && c.controls.power && c.online) window.miApi.control(c.id, c.kind === 'cover' ? (window.miApi.isOn(c) ? 'close' : 'open') : 'toggle');
    else if (c) window.miApi.openDetail(c.id);
    return;
  }
  const sc = t.closest('[data-scene]');
  if (sc) return post('/api/text', { text: sc.dataset.scene });
  const ck = t.closest('[data-check]');
  if (ck) return dailyPost('lists', { op: 'check', name: ck.dataset.check, items: [ck.dataset.text], done: ck.dataset.done !== '1' });
  const md = t.closest('[data-media]');
  if (md) return dailyPost('media', md.dataset.media === 'stop' ? { stop: true } : { kind: 'radio', name: md.dataset.media });
  const rm = t.closest('[data-room]');
  if (rm) { D.room = rm.dataset.room; return render(true); }
  const op = t.closest('[data-open]');
  if (op && window.miApi) return window.miApi.openHome(true);
  const q = t.closest('[data-ask]');
  if (q) return post('/api/text', { text: q.dataset.ask });
  if (t.closest('#dMic')) return post('/api/wake');
});
if (window.miApi) window.miApi.longPress($('dash'), '[data-dev]', el => window.miApi.openDetail(el.dataset.dev));
document.addEventListener('visibilitychange', apply);

let n = 0;
setInterval(() => {
  watchTalk();
  if (!on()) { if (document.body.classList.contains('dash-on')) apply(); return; }
  const now = new Date(), tm = $('dTime');
  if (tm) tm.textContent = pad(now.getHours()) + ':' + pad(now.getMinutes());
  if (++n % 2 === 0) render(false);
  if (n % 30 === 0) loadCost();
}, 1000);
window.dashRender = () => { loadCost(); render(true); };
})();
