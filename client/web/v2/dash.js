// 仪表盘（设计画布第 35 块）：首页布局选「仪表盘」时，顶上换成页签（首页、家居、日程、对话），
// 「对话」就是原来的首页（app.js 的 main）。数据都是页面已有的：ui.*（app.js）和 window.miApi（home.js 的米家）。
// 每秒看一次要不要重画，内容没变不动 DOM（和 home.js 的磁贴一样），设备上很省。对话一开始（叫醒、在想、在说）
// 自动切到「对话」，说完 8 秒回到原来那一页。
(() => {
// tab：现在看的页，页面打开时是「外观」里选的默认页（looks.dash_default），之后点哪页是哪页
const D = { tab: null, talk: false, idleAt: 0, room: '全屋', cost: null, costAt: 0, sig: {}, day: null, placed: '' };
const portrait = () => innerHeight > innerWidth;

const on = () => !!(ui.looks && ui.looks.layout === 'dashboard');
const mi = () => (window.miApi && window.miApi.view()) || null;
const n0 = v => (v === null || v === undefined ? '?' : Math.round(v));
const set = (id, html) => { if (D.sig[id] !== html) { D.sig[id] = html; $(id).innerHTML = html; } };
const head = (ic, title, small = '') => `<div class="dc-h"><span class="dc-ic">${ic}</span><b>${title}</b>${small ? `<small>${small}</small>` : ''}</div>`;
const WD = ['日', '一', '二', '三', '四', '五', '六'];

function setTab(t) {
  D.tab = t;
  apply();
  render(true);
}
function apply() {
  const dash = on();
  if (dash && !D.tab) D.tab = (ui.looks.dash_default in { home: 1, smart: 1, plan: 1, chat: 1 }) ? ui.looks.dash_default : 'home';
  document.body.classList.toggle('dash-on', dash);
  const shown = D.talk ? 'chat' : (D.tab || 'home');
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
  return `<div class="db-top"><span class="d-orb big"><img alt=""${window.dashAvSrc && window.dashAvSrc(false) ? ` src="${esc(window.dashAvSrc(false))}"` : ''}></span><div><div class="db-hi">${hi}！</div><div class="dm db-say">${said.join('')}</div></div></div>` +
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

/* ---- 首页的卡片：按「外观」里的顺序和开关摆（looks.dash_cards，设计画布第 36 块） ----
   横屏三列：早安小结、常用设备这两张宽的在中间，其余按顺序前一半在左、后一半在右；竖屏按顺序往下排，小卡片两张一行 */
const CARDS = { wx: ['dWx', weather], todo: ['dTodo', todo], alarm: ['dAlarm', alarms], brief: ['dBrief', brief],
  dev: ['dDev', devices], scene: ['dScene', scenes], media: ['dMedia', media], nums: ['dNums', nums],
  // 工作台、蓝牙灯（work.js，设计画布第 38 块）
  work: ['dWork', () => window.igApi ? window.igApi.workCard(head) : ''], lights: ['dLights', () => window.igApi ? window.igApi.lightsCard(head) : ''] };
const WIDE = { brief: 1, dev: 1 };
for (const [k, [id]] of Object.entries(CARDS)) {
  const el = document.createElement('div');
  el.className = 'dc' + (k === 'wx' ? ' dc-wx' : k === 'brief' ? ' dc-brief' : '') + (WIDE[k] ? ' dc-wide' : '');
  el.id = id; el.hidden = true;
  $('dhM').appendChild(el);
}
function shownCards() {
  const list = (ui.looks && ui.looks.dash_cards) || Object.keys(CARDS).map(id => ({ id, on: true }));
  return list.filter(c => c.on && CARDS[c.id]).map(c => c.id);
}
function placeHome() {
  const ids = shownCards(), key = ids.join(',') + (portrait() ? '|p' : '|l');
  if (key === D.placed) return;
  D.placed = key;
  const L = $('dhL'), M = $('dhM'), R = $('dhR');
  for (const [k, [id]] of Object.entries(CARDS)) { $(id).hidden = !ids.includes(k); $(id).classList.remove('dc-grow', 'dc-span'); }
  if (portrait()) {       // 竖屏小卡片两张一行：落单的那张（后面是宽卡片，或者是最后一张）占满一行
    let run = [];
    const close = () => { if (run.length % 2) $(CARDS[run[run.length - 1]][0]).classList.add('dc-span'); run = []; };
    for (const k of ids) { if (WIDE[k]) close(); else run.push(k); }
    close();
  }
  let cols;
  if (portrait()) cols = [[], ids, []];
  else {
    const wide = ids.filter(k => WIDE[k]), small = ids.filter(k => !WIDE[k]);
    if (wide.length) { const h = Math.ceil(small.length / 2); cols = [small.slice(0, h), wide, small.slice(h)]; }
    else { const t = Math.ceil(small.length / 3); cols = [small.slice(0, t), small.slice(t, 2 * t), small.slice(2 * t)]; }
  }
  [L, M, R].forEach((col, i) => {
    cols[i].forEach(k => col.appendChild($(CARDS[k][0])));
    col.hidden = !cols[i].length;
    if (!portrait() && cols[i].length) $(CARDS[cols[i][cols[i].length - 1]][0]).classList.add('dc-grow');
  });
  $('dash').querySelector('[data-dp="home"]').dataset.cols = String(cols.filter(c => c.length).length);
}
function home() {
  placeHome();
  for (const k of shownCards()) set(CARDS[k][0], CARDS[k][1]());
}

/* ---- 家居：左边房间栏（能上下滑、能拖宽），右边场景一行、分类计数、按房间的设备 ---- */
function smart() {
  const v = mi();
  if (!v || !v.account) {
    set('dHomeRooms', head('⌂', '我的家') + '<button class="d-empty" data-open="home">还没登录米家：点这里扫码</button>');
    set('dKinds', ''); set('dRooms', ''); set('dSceneBar', '');
    return;
  }
  const all = v.rooms.flatMap(r => r.cards), home = (v.homes || []).find(h => h.id === v.home_id);
  const rooms = ['全屋', ...v.rooms.map(r => r.name)];
  if (!rooms.includes(D.room)) D.room = '全屋';
  set('dHomeRooms', `<b class="dh-name">${esc(home ? home.name : '我的家')}</b><span class="dm d-ok">● ${all.filter(c => c.online).length} / ${all.length} 在线</span>` +
    `<div class="ds-roomlist">${rooms.map(r => `<button class="d-room${r === D.room ? ' on' : ''}" data-room="${esc(r)}">${esc(r)}` +
      `<small>${r === '全屋' ? all.length : v.rooms.find(x => x.name === r).cards.length}</small></button>`).join('')}</div>`);
  const sc = (ui.scenes && ui.scenes.scenes) || [];
  set('dSceneBar', sc.map(x => `<button data-scene="${esc(x.name)}">✦ ${esc(x.name)}</button>`).join(''));
  $('dSceneBar').hidden = !sc.length;
  const scope = D.room === '全屋' ? all : v.rooms.find(r => r.name === D.room).cards;
  const kinds = [['light', '灯', () => `${scope.filter(x => x.kind === 'light' && window.miApi.isOn(x)).length} 个开着`],
    ['plug', '插座', () => { const w = scope.filter(x => x.kind === 'plug' && x.state && typeof x.state.power_w === 'number').reduce((a, x) => a + x.state.power_w, 0); return w ? `正在用 ${Math.round(w)} W` : `${scope.filter(x => x.kind === 'plug' && window.miApi.isOn(x)).length} 个开着`; }],
    ['sensor', '传感器', () => { const t = scope.find(x => x.state && typeof x.state.temp === 'number'); return t ? `${t.state.temp}°${typeof t.state.humidity === 'number' ? ' · ' + t.state.humidity + '%' : ''}` : ''; }],
    ['cover', '窗帘', () => `${scope.filter(x => x.kind === 'cover' && window.miApi.isOn(x)).length} 个开着`],
    ['climate', '空调暖气', () => `${scope.filter(x => x.kind === 'climate' && window.miApi.isOn(x)).length} 个开着`]]
    .map(([k, name, sub]) => [k, name, scope.filter(x => x.kind === k).length, sub]).filter(x => x[2]).slice(0, 4);
  set('dKinds', kinds.map(([k, name, count, sub]) => `<div class="dc d-kind"><span class="dd-ic">${window.miApi.icon(k, 22)}</span><b>${name} ${count}</b><span class="dm">${sub()}</span></div>`).join(''));
  $('dKinds').style.gridTemplateColumns = `repeat(${Math.max(1, kinds.length)}, minmax(0, 1fr))`;
  const shown = D.room === '全屋' ? v.rooms : v.rooms.filter(r => r.name === D.room);
  set('dRooms', shown.map(r => `<div class="dc d-roomcard"><div class="dc-h"><b>${esc(r.name)}</b><small>${r.cards.length} 个设备 · ${r.cards.filter(c => window.miApi.isOn(c)).length} 个开着</small></div>` +
    `<div class="d-devs wide">${r.cards.map(devTile).join('')}</div></div>`).join('') || '<div class="dc dm">这个房间没有设备</div>');
}
// 房间栏的宽度：按住中间的竖条左右拖，横竖屏各记各的
const RZ = { min: 110, max: 380 };
function roomWidth(w) {
  const key = 'dashRoomW-' + (portrait() ? 'p' : 'l');
  if (w === undefined) { try { w = Number(localStorage.getItem(key)); } catch (e) { w = 0; } w = w || (portrait() ? 150 : 240); }
  else try { localStorage.setItem(key, String(w)); } catch (e) { /* private storage */ }
  $('dash').style.setProperty('--ds-w', Math.min(RZ.max, Math.max(RZ.min, w)) + 'px');
}
$('dRz').addEventListener('pointerdown', e => {
  const bar = e.currentTarget, left = $('dHomeRooms').getBoundingClientRect().left;
  bar.setPointerCapture(e.pointerId); bar.classList.add('drag');
  let w = 0;
  const move = ev => { w = Math.round(Math.min(RZ.max, Math.max(RZ.min, ev.clientX - left - 8))); $('dash').style.setProperty('--ds-w', w + 'px'); };
  const up = () => { bar.classList.remove('drag'); bar.removeEventListener('pointermove', move); if (w) roomWidth(w); };
  bar.addEventListener('pointermove', move);
  bar.addEventListener('pointerup', up, { once: true }); bar.addEventListener('pointercancel', up, { once: true });
});
roomWidth();

/* ---- 日程：月历、接下来（闹钟、提醒、天气播报、定时任务、作息排成一条时间线）、快捷倒计时、清单、留言 ---- */
const sameDay = (a, b) => a.toDateString() === b.toDateString();
const onDay = (days, d) => days === 'workdays' ? d.getDay() >= 1 && d.getDay() <= 5 : days === 'weekend' ? (d.getDay() === 0 || d.getDay() === 6) : true;
function agenda() {
  // {at: Date, title, sub, kind}：真的闹钟提醒，加上今天和明天按点会发生的事
  const out = [], now = new Date();
  for (const v of (ui.alarms && ui.alarms.items) || []) {
    if (v.kind === 'timer') continue;
    out.push({ at: new Date(v.at * 1000), title: v.label || (v.kind === 'alarm' ? '闹钟' : v.kind === 'broadcast' ? '天气播报' : '提醒'),
      sub: v.repeat || '', kind: { alarm: '闹钟', reminder: '提醒', broadcast: '播报' }[v.kind] || '提醒' });
  }
  const at = (d, hm) => { const [h, m] = String(hm).split(':').map(Number); const x = new Date(d); x.setHours(h, m || 0, 0, 0); return x; };
  for (let i = 0; i < 2; i++) {
    const d = new Date(now); d.setDate(d.getDate() + i);
    const add = (hm, title, sub, kind) => { const t = at(d, hm); if (t > now) out.push({ at: t, title, sub, kind }); };
    const b = ui.broadcast;
    if (b && b.enabled && onDay(b.days, d) && !out.some(x => x.kind === '播报' && sameDay(x.at, d))) for (const t of b.times || []) add(t, '天气播报', DAYS_TEXT[b.days] || '', '播报');
    for (const t of (ui.tasks && ui.tasks.tasks) || []) if (onDay(t.days, d)) add(t.time, t.prompt, DAYS_TEXT[t.days] || '', '定时任务');
    const sl = ui.status && ui.status.sleep;
    if (sl && sl.enabled) add(sl.start, '睡眠时段开始', `到 ${sl.end}`, '作息');
    const k = ui.kids;
    if (k && k.enabled && k.bedtime) add(k.bedtime, '孩子该睡觉了', '儿童模式', '作息');
  }
  return out.sort((a, b) => a.at - b.at);
}
function untilText(t) {
  const m = Math.round((t - Date.now()) / 60000);
  return m < 1 ? '马上' : m < 60 ? `还有 ${m} 分钟` : m < 24 * 60 ? `还有 ${Math.floor(m / 60)} 小时${m % 60 >= 30 ? '半' : ''}` : '';
}
function plan() {
  const now = new Date(), items = agenda(), lunar = ui.today && ui.today.lunar;
  // 月历：周一开头，今天高亮，有事的日子带点，点一天只看那天
  const first = new Date(now.getFullYear(), now.getMonth(), 1), start = new Date(first);
  start.setDate(1 - ((first.getDay() + 6) % 7));
  const has = new Set(items.map(x => x.at.toDateString()));
  let cells = '';
  for (let i = 0; i < 42; i++) {
    const d = new Date(start); d.setDate(start.getDate() + i);
    if (i >= 35 && d.getMonth() !== now.getMonth()) break;
    cells += `<button class="${d.getMonth() !== now.getMonth() ? 'o ' : ''}${sameDay(d, now) ? 't ' : ''}${has.has(d.toDateString()) ? 'e ' : ''}${D.day === d.toDateString() ? 'sel' : ''}" data-day="${d.toDateString()}">${d.getDate()}</button>`;
  }
  set('dCal', `<div class="dc-h"><b>${now.getFullYear()} 年 ${now.getMonth() + 1} 月</b><small>${lunar && lunar.text ? '农历' + esc(lunar.text) : ''}</small></div>` +
    `<div class="d-cal">${['一', '二', '三', '四', '五', '六', '日'].map(w => `<span>${w}</span>`).join('')}${cells}</div>` +
    `<div class="dm">${D.day ? '只看这一天，再点一下看全部' : '带点的那天有安排，点一天只看那天'}</div>`);
  // 快捷倒计时
  const timers = ((ui.alarms && ui.alarms.items) || []).filter(v => v.kind === 'timer');
  set('dTimers', head('◷', '快捷倒计时', '点一下就开始') + `<div class="d-quick">${[3, 5, 10, 15, 30, 60].map(n => `<button data-timer="${n}">${n} 分</button>`).join('')}</div>` +
    timers.map(v => `<div class="d-tl"><b data-left="${v.at}">${left(v.at)}</b><span>${esc(v.label || '倒计时')}</span><i>${hhmm(v.at)} 响</i></div>`).join(''));
  // 接下来
  const list = D.day ? items.filter(x => x.at.toDateString() === D.day) : items.slice(0, 12);
  let html = head('⏰', '接下来', '说一句就加：「明早 7 点叫我」'), last = '';
  for (const x of list) {
    const key = x.at.toDateString();
    if (key !== last) { last = key; html += `<div class="d-dayh">${dayText(x.at / 1000)} · ${x.at.getMonth() + 1}月${x.at.getDate()}日 周${WD[x.at.getDay()]}</div>`; }
    html += `<div class="d-tl"><b>${pad(x.at.getHours())}:${pad(x.at.getMinutes())}</b><span>${esc(x.title)}</span><em>${esc([x.sub, untilText(x.at)].filter(Boolean).join(' · '))}</em><i>${x.kind}</i></div>`;
  }
  if (!list.length) html += `<div class="d-blank"><b>${D.day ? '这一天没有安排' : '接下来没有安排'}</b><span class="dm">可以说「明早 7 点叫我」「10 分钟后提醒我晾衣服」「每天早上七点半播报天气」</span>` +
    '<div class="d-chips"><button data-ask="明早七点叫我">明早 7 点叫我</button><button data-ask="每天早上七点半播报天气">每天 7:30 播天气</button><button data-ask="我有哪些闹钟">我有哪些闹钟</button></div></div>';
  set('dNext', html);
  // 清单（带进度）
  const l = ui.lists && ui.lists.lists;
  const all = l ? Object.values(l).flat() : [], done = all.filter(x => x.done).length;
  set('dPlanLists', head('✓', '清单', all.length ? `办完 ${done} / ${all.length}` : '') +
    (all.length ? `<div class="d-bar"><i style="transform:scaleX(${done / all.length})"></i></div>` : '') +
    (l ? Object.keys(l).map(name => `<div class="dp-list"><b>${esc(name)}</b>` +
      (l[name].length ? l[name].slice(0, 6).map(x => `<button class="d-todo${x.done ? ' done' : ''}" data-check="${esc(name)}" data-text="${esc(x.text)}" data-done="${x.done ? 1 : 0}"><i></i>${esc(x.text)}</button>`).join('') : '<div class="dm">空的</div>') + '</div>').join('')
      : '<div class="dm">清单还没取到</div>') + '<div class="d-chips"><button data-phone="1">手机看</button><button data-ask="购物清单里有什么">念一遍</button></div>');
  const notes = (ui.notes && ui.notes.notes) || [];
  set('dPlanNotes', head('✉', '留言', notes.length ? `${notes.length} 条` : '') +
    (notes.length ? notes.slice(0, 5).map(n => `<button class="d-note" data-note="${esc(n.id)}"><b>${n.from ? esc(n.from) + ' → ' : '→ '}${esc(n.to)}</b><span>${esc(n.text)}</span><i>点一下念</i></button>`).join('')
      : '<div class="dm">说「给爸爸留言：记得取快递」，留言会出现在这里和首页</div>'));
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
  if (D.tab === 'home' || force) home();
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
  if (t.closest('[data-work]') && window.igApi) return window.igApi.openWork();
  const lt = t.closest('[data-light]');
  if (lt && window.igApi) return window.igApi.toggleLight(lt.dataset.light);
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
  const dy = t.closest('[data-day]');
  if (dy) { D.day = D.day === dy.dataset.day ? null : dy.dataset.day; return render(true); }
  const tm = t.closest('[data-timer]');
  if (tm) return post('/api/text', { text: `倒计时 ${tm.dataset.timer} 分钟` });
  const nt = t.closest('[data-note]');
  if (nt) return dailyPost('notes', { read: nt.dataset.note });
  if (t.closest('[data-phone]')) return $('listsPhone').click();
  const rm = t.closest('[data-room]');
  if (rm) { D.room = rm.dataset.room; return render(true); }
  const op = t.closest('[data-open]');
  if (op && window.miApi) return window.miApi.openHome(true);
  const q = t.closest('[data-ask]');
  if (q) return post('/api/text', { text: q.dataset.ask });
  if (t.closest('#dMic')) return post('/api/wake');
});
if (window.miApi) window.miApi.longPress($('dash'), '[data-dev]', el => window.miApi.openDetail(el.dataset.dev));
if (window.miApi && window.igApi) window.miApi.longPress($('dash'), '[data-light]', el => window.igApi.openLight(el.dataset.light));
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
window.dashRender = () => { loadCost(); D.placed = ''; render(true); };
addEventListener('resize', () => { D.placed = ''; roomWidth(); render(true); });
})();
