// 家居 (Mijia devices), the model's content cards (rendered in the conversation) and screen rotation. Loaded after app.js and uses
// its helpers ($, esc, toast, postJson, handlers); app.js connects the event stream once this has run.
// Everything here lives in its own scope: app.js is a classic script too, and a top-level name in
// both (KIND_ICON once was) makes the whole of this file fail to load.
(() => {

/* ---------------- 家居 ---------------- */
const mi = { view: null, open: false, detail: null, region: 'cn', qrShown: null, room: '全屋', scrollTab: true };

async function miFetch(path, body) {
  try {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                  body: JSON.stringify(body || {}) });
    return await r.json();
  } catch (e) {
    toast('客户端没有响应', 'error');
    return { ok: false };
  }
}

function openHome(on) {
  mi.open = on;
  $('home').classList.toggle('on', on);
  if (!on) closeDetail();
  if (!DEMO) {
    miFetch('/api/mijia/visible', { visible: on });
    if (on && mi.view && mi.view.account && mi.view.state !== 'loading') miFetch('/api/mijia/refresh');
  }
  renderHome();
}

const KIND_ICON = {
  light: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
  plug: '<rect x="5" y="5" width="14" height="14" rx="4"/><path d="M10 10v2M14 10v2M12 15v.01"/>',
  climate: '<rect x="3" y="6" width="18" height="8" rx="2.5"/><path d="M7 18c0 1 1 1.5 1 2.5M12 18c0 1 1 1.5 1 2.5M17 18c0 1 1 1.5 1 2.5M7 11h10"/>',
  fan: '<circle cx="12" cy="12" r="1.8"/><path d="M12 10.2C11 6 13 3 15.5 4.5S15 10 12 10.2zM13.8 12c4.2-1 7.2 1 5.7 3.5S14 15 13.8 12zM10.2 12c-4.2 1-7.2-1-5.7-3.5S10 9 10.2 12z"/>',
  purifier: '<rect x="6" y="3" width="12" height="18" rx="3"/><path d="M9 8h6M9 11h6M9 14h6"/>',
  cover: '<path d="M4 4h16M6 4v16M18 4v16M6 8c3 1 3 6 0 10M18 8c-3 1-3 6 0 10"/>',
  sensor: '<path d="M14 14.8V5a2 2 0 1 0-4 0v9.8a4 4 0 1 0 4 0z"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  speaker: '<rect x="6" y="3" width="12" height="18" rx="3"/><circle cx="12" cy="14" r="3"/><path d="M12 7v.01"/>',
  vacuum: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3"/>',
  camera: '<rect x="3" y="7" width="13" height="10" rx="2.5"/><path d="M16 11l5-3v8l-5-3"/>',
  appliance: '<rect x="5" y="3" width="14" height="18" rx="2.5"/><circle cx="12" cy="13" r="4"/><path d="M8 6.5h.01M11 6.5h.01"/>',
};
const icon = (k, size = 30) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${KIND_ICON[k] || KIND_ICON.appliance}</svg>`;

window.drawQr = drawQr;
function drawQr(canvas, text) {
  const qr = qrcode(0, 'M');
  qr.addData(text);
  qr.make();
  const n = qr.getModuleCount(), g = canvas.getContext('2d'), quiet = 2, s = canvas.width / (n + quiet * 2);
  g.fillStyle = '#fff'; g.fillRect(0, 0, canvas.width, canvas.height);
  g.fillStyle = '#0b1020';
  for (let y = 0; y < n; y++)
    for (let x = 0; x < n; x++)
      if (qr.isDark(y, x)) g.fillRect(Math.floor((x + quiet) * s), Math.floor((y + quiet) * s), Math.ceil(s), Math.ceil(s));
}

function renderHome() {
  const v = mi.view;
  if (!v) return;
  renderMiSettings();
  const loggedIn = !!v.account && v.state !== 'expired';
  // only one of the two takes the sheet's height: both flex:1, the QR pane overflowed its half and
  // the empty rooms box above it in the stack swallowed taps on 获取二维码
  $('hLogin').style.display = loggedIn ? 'none' : '';
  $('hRooms').style.display = loggedIn ? '' : 'none';
  $('hRefresh').style.display = loggedIn ? '' : 'none';
  $('hSub').textContent = loggedIn ? `米家 · ${v.account.user}` + (v.updated_at ? ` · ${new Date(v.updated_at * 1000).toLocaleTimeString('zh-CN', { hour12: false })} 更新` : '')
    : '用米家 App 扫码登录后显示家里的设备';
  // the home: its name with ▾, the others in a menu
  const homes = loggedIn ? v.homes : [];
  const current = homes.find(h => h.id === v.home_id) || homes[0];
  $('hHomeName').textContent = current ? current.name : '家居';
  $('hHomePick').classList.toggle('many', homes.length > 1);
  $('hHomeMenu').innerHTML = homes.map(h =>
    `<button data-id="${esc(h.id)}" class="${current && h.id === current.id ? 'on' : ''}">${esc(h.name)}${h.shared ? '<small>共享</small>' : ''}</button>`).join('');
  // message line
  const msg = v.state === 'expired' ? '米家登录已过期，请重新扫码' : v.state === 'loading' ? '正在读取米家设备…'
    : v.state === 'error' ? v.error : loggedIn && !v.rooms.length ? '这个家里还没有能显示的设备' : '';
  $('hMsg').textContent = msg;
  $('hMsg').classList.toggle('bad', v.state === 'error' || v.state === 'expired');
  if (!loggedIn) renderLogin(v);
  // rooms as tabs: 全屋 and each room; the summary chips and tiles follow the tab
  const rooms = loggedIn ? v.rooms : [];
  if (mi.room !== ALL && !rooms.some(r => r.name === mi.room)) mi.room = ALL;
  $('hTabs').style.display = $('hChips').style.display = loggedIn && rooms.length ? '' : 'none';
  $('hTabs').innerHTML = [ALL, ...rooms.map(r => r.name)].map(n =>
    `<button data-room="${esc(n)}" class="${n === mi.room ? 'on' : ''}">${esc(n)}</button>`).join('');
  const shown = mi.room === ALL ? rooms : rooms.filter(r => r.name === mi.room);
  $('hChips').innerHTML = chips(shown.flatMap(r => r.cards));
  $('hRooms').innerHTML = shown.map(r => `<div class="h-room">${mi.room === ALL ? `<h3>${esc(r.name)}</h3>` : ''}<div class="h-tiles">${
    r.cards.map(tile).join('')}</div></div>`).join('');
  const tab = $('hTabs').querySelector('.on');
  if (tab && mi.scrollTab) { tab.scrollIntoView({ inline: 'nearest', block: 'nearest' }); mi.scrollTab = false; }
  if (mi.detail) renderDetail();
}

const ALL = '全屋';
const CHIP_ICON = {
  temp: '<path d="M14 14.8V5a2 2 0 1 0-4 0v9.8a4 4 0 1 0 4 0z"/>',
  humidity: '<path d="M12 3.5s-6 6.6-6 10.5a6 6 0 0 0 12 0c0-3.9-6-10.5-6-10.5z"/>',
  light: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
};
// what the tab's devices say: temperature and humidity ranges (room sensors, not switch chips), lights on
function chips(cards) {
  const nums = k => cards.filter(c => c.online && typeof c.state[k] === 'number').map(c => c.state[k]);
  const range = (xs, unit) => xs.length ? (Math.min(...xs) === Math.max(...xs) ? `${Math.min(...xs)}${unit}`
    : `${Math.min(...xs)} - ${Math.max(...xs)}${unit}`) : null;
  const isLight = c => c.kind === 'light' || (c.kind === 'plug' && /灯/.test(c.name));
  const lights = cards.filter(c => isLight(c) && c.state.on === true).length;
  const out = [['temp', range(nums('temp'), '°')], ['humidity', range(nums('humidity'), '%')],
               ['light', cards.some(isLight) ? (lights ? `亮灯 ${lights}` : '灯都关着') : null]];
  return out.filter(([, t]) => t).map(([k, t]) => `<div class="h-chip">${
    `<svg width="30" height="30" viewBox="0 0 24 24" fill="currentColor" stroke="none">${CHIP_ICON[k]}</svg>`}<span>${esc(t)}</span></div>`).join('');
}

const isOn = c => c.state.on === true || (c.kind === 'cover' && c.state.position > 0);
// 首页「常用设备」磁贴：mi.view.favorites（米家服务按设备上存的顺序给出）
function renderQuick() {
  const box = $('homeDevices');
  const favs = (mi.view && mi.view.account && mi.view.state !== 'expired' && mi.view.favorites) || [];
  const html = favs.map(c => `<button class="hd-tile${isOn(c) ? ' on' : ''}${c.online ? '' : ' offline'}" data-q="${esc(c.id)}">
    <span class="h-ic k-${esc(c.kind)}">${icon(c.kind, 24)}</span><b>${esc(c.name)}</b><span>${esc(c.summary)}</span></button>`).join('');
  if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
  box.hidden = !favs.length;
}
function quickHome(on) {   // 首页显示着才查常用设备的状态（30 秒一次）；待机页、熄屏时不查
  if (DEMO || quickHome.on === on) return;
  quickHome.on = on;
  miFetch('/api/mijia/visible', { visible: mi.open, home: on });
}
const favIds = () => new Set(((mi.view && mi.view.favorites) || []).map(c => c.id));
function askFavorite(c) {
  const on = !favIds().has(c.id);
  askConfirm(on ? `把「${c.name}」放到首页的常用设备？` : `把「${c.name}」从首页的常用设备拿掉？`, async () => {
    const r = await miFetch('/api/mijia/favorite', { id: c.id, on });
    if (r && r.ok === false && r.error) toast(r.error, 'error');
    else toast(on ? '已放到首页' : '已从首页拿掉');
  });
}
// 长按：家居页的磁贴是「放到首页 / 拿掉」，首页的磁贴是打开详情
function longPress(el, selector, fn) {
  let timer = null, fired = false;
  el.addEventListener('pointerdown', e => {
    const t = e.target.closest(selector);
    if (!t || e.target.closest('[data-power]')) return;
    fired = false;
    timer = setTimeout(() => { fired = true; fn(t); }, 550);
  });
  const cancel = () => clearTimeout(timer);
  el.addEventListener('pointerup', cancel); el.addEventListener('pointerleave', cancel); el.addEventListener('pointercancel', cancel);
  el.addEventListener('click', e => { if (fired) { e.stopPropagation(); e.preventDefault(); fired = false; } }, true);
}

function tile(c) {
  const on = c.state.on === true || (c.kind === 'cover' && c.state.position > 0);
  const power = c.controls.power && c.online
    ? `<button class="h-power${on ? ' on' : ''}" data-power="${esc(c.id)}" aria-label="开关"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 3.5v8M7 6.3a7.5 7.5 0 1 0 10 0"/></svg></button>` : '';
  return `<div class="h-tile${on ? ' on' : ''}${c.online ? '' : ' offline'}" data-id="${esc(c.id)}">
    <div class="h-ic k-${esc(c.kind)}">${icon(c.kind)}</div>${power}${favIds().has(c.id) ? '<span class="fav">★ 常用</span>' : ''}
    <b>${esc(c.name)}</b><span>${esc(c.summary)}</span></div>`;
}

function renderLogin(v) {
  const l = v.login || { state: 'idle' };
  const regions = v.regions || { cn: '中国大陆' };
  if (l.region) mi.region = l.region;
  $('hRegions').innerHTML = Object.entries(regions).map(([k, name]) =>
    `<button data-r="${k}" class="${k === mi.region ? 'on' : ''}">${esc(name)}</button>`).join('');
  const mask = $('hQrMask'), btn = $('hQrBtn');
  if (l.state === 'waiting' && l.login_url) {
    if (mi.qrShown !== l.login_url) { drawQr($('hQr'), l.login_url); mi.qrShown = l.login_url; }
    mask.textContent = ''; mask.className = 'h-qr-mask';
    btn.textContent = '换一个二维码';
    const left = Math.max(0, Math.round(l.deadline - Date.now() / 1000));
    $('hLoginMsg').textContent = `打开米家 App，点右上角的「＋」→「扫一扫」，扫左边的二维码并确认登录。二维码 ${Math.floor(left / 60)} 分 ${left % 60} 秒后过期。`;
  } else {
    mi.qrShown = null;
    mask.className = 'h-qr-mask on';
    mask.textContent = l.state === 'starting' ? '正在获取…' : l.state === 'error' ? '获取失败' : '点「获取二维码」';
    btn.textContent = l.state === 'error' ? '重新获取' : '获取二维码';
    $('hLoginMsg').textContent = l.state === 'error' ? l.error : '打开米家 App，点右上角的「＋」→「扫一扫」，扫二维码并确认登录。';
  }
}
setInterval(() => { if (mi.open && mi.view && (mi.view.login || {}).state === 'waiting') renderLogin(mi.view); }, 1000);

function findCard(id) {
  for (const r of (mi.view || { rooms: [] }).rooms) for (const c of r.cards) if (c.id === id) return c;
  return null;
}

async function control(id, op, value, confirm) {
  const r = await miFetch('/api/mijia/control', { id, op, value, confirm: !!confirm });
  if (r.need_confirm) return askConfirm(r.text, () => control(id, op, value, true));
  if (!r.ok) toast(r.error || r.text || '没控制成功', 'error');
}

function askConfirm(text, yes) {
  $('hcText').textContent = text;
  $('hConfirm').classList.add('on');
  $('hcYes').onclick = () => { $('hConfirm').classList.remove('on'); yes(); };
  $('hcNo').onclick = () => $('hConfirm').classList.remove('on');
}

function openDetail(id) {
  mi.detail = id;
  $('hDetail').classList.add('on');
  renderDetail(true);
}
function closeDetail() {
  mi.detail = null;
  $('hDetail').classList.remove('on');
}

function renderDetail(first) {
  const c = findCard(mi.detail);
  if (!c) return closeDetail();
  $('hdName').textContent = c.name;
  $('hdSum').textContent = c.online ? c.summary : '离线';
  const box = $('hdControls');
  // don't rebuild under a finger: only on open or when nothing is being touched
  if (!first && box.contains(document.activeElement) && document.activeElement.type === 'range') return;
  const k = c.controls, s = c.state, parts = [];
  if (c.readonly || !c.online) parts.push(`<p class="opt-note">${c.online ? '这个设备只能查看状态' : '设备离线，暂时不能控制'}</p>`);
  else {
    if (k.power && c.kind !== 'cover') parts.push(`<div class="opt"><div class="opt-text"><b>开关</b></div>
      <button class="switch" data-op="toggle" role="switch" aria-checked="${s.on ? 'true' : 'false'}"><i></i></button></div>`);
    const range = (op, label, val, lo, hi, unit) => `<div class="bright"><div class="bright-head"><b>${label}</b><span class="bright-num">${val ?? '—'}${unit}</span></div>
      <div class="bright-row"><input type="range" data-op="${op}" min="${lo}" max="${hi}" step="1" value="${val ?? lo}"></div></div>`;
    if (k.brightness) parts.push(range('brightness', '亮度', s.brightness, 1, 100, '%'));
    if (k.color_temp) parts.push(range('color_temp', '色温', s.color_temp, k.color_temp[0], k.color_temp[1], 'K'));
    if (k.target_temp) {
      const [lo, hi, step] = k.target_temp;
      parts.push(`<div class="time"><small>目标温度</small><div class="stepper">
        <button data-temp="${-(step || 1)}" data-lo="${lo}" data-hi="${hi}">−</button><b>${s.target_temp ?? '—'}°</b>
        <button data-temp="${step || 1}" data-lo="${lo}" data-hi="${hi}">+</button></div></div>`);
    }
    if (k.mode) parts.push(`<div class="bright-head"><b class="sub">模式</b></div><div class="minutes">${
      k.mode.map(m => `<button data-op="mode" data-v="${esc(m)}" class="${m === s.mode ? 'on' : ''}">${esc(m)}</button>`).join('')}</div>`);
    if (k.fan_level === 'percent') parts.push(range('fan_level', '风速', parseInt(s.fan_level) || 0, 0, 100, '%'));
    else if (k.fan_level) parts.push(`<div class="bright-head"><b class="sub">风速</b></div><div class="minutes">${
      k.fan_level.map(m => `<button data-op="fan_level" data-v="${esc(m)}" class="${m === s.fan_level ? 'on' : ''}">${esc(m)}</button>`).join('')}</div>`);
    if (c.kind === 'cover') parts.push(`<div class="minutes"><button data-op="open">打开</button><button data-op="stop">停止</button><button data-op="close">关闭</button></div>`);
    if (k.position) parts.push(range('position', '开合', s.position, 0, 100, '%'));
    if (k.volume) parts.push(range('volume', '音量', s.volume, 0, 100, ''));
  }
  const detail = Object.entries(s).filter(([key]) => !['on'].includes(key)).map(([key, v]) =>
    `<span>${esc(LABEL[key] || key)}：${esc(fmt(key, v))}</span>`).join('');
  parts.push(`<div class="h-readings">${detail}</div>`);
  box.innerHTML = parts.join('');
}
const LABEL = { brightness: '亮度', color_temp: '色温', mode: '模式', status: '状态', motor: '电机', fan_level: '风速',
  target_temp: '目标温度', temp: '温度', device_temp: '设备温度', humidity: '湿度', target_humidity: '目标湿度', pm25: 'PM2.5', battery: '电量',
  power_w: '功率', energy: '用电量', volume: '音量', filter: '滤芯剩余', illum: '光照', aqi: '空气质量',
  position: '开合', target_position: '目标开合', motion: '人体', contact: '门窗', leak: '水浸', mute: '静音',
  swing: '摇头', door_state: '门状态' };
function fmt(key, v) {
  if (v === true) return { motion: '有人', contact: '关闭', leak: '漏水', mute: '是', swing: '开' }[key] || '是';
  if (v === false) return { motion: '无人', contact: '打开', leak: '无', mute: '否', swing: '关' }[key] || '否';
  const unit = { brightness: '%', color_temp: 'K', target_temp: '°C', temp: '°C', device_temp: '°C', humidity: '%', target_humidity: '%',
    battery: '%', power_w: 'W', energy: 'kWh', filter: '%', position: '%', target_position: '%', illum: 'lux' }[key] || '';
  return `${v}${unit}`;
}

$('btnHome').addEventListener('click', () => openHome(true));
$('hClose').addEventListener('click', () => openHome(false));
$('hRefresh').addEventListener('click', () => { miFetch('/api/mijia/refresh'); toast('正在刷新'); });
$('hQrBtn').addEventListener('click', () => miFetch('/api/mijia/login/start', { region: mi.region }));
$('hRegions').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  mi.region = b.dataset.r;
  renderLogin(mi.view);
  if ((mi.view.login || {}).state === 'waiting') miFetch('/api/mijia/login/start', { region: mi.region });
});
$('hHomePick').addEventListener('click', e => {
  e.stopPropagation();
  if ($('hHomePick').classList.contains('many')) $('hHomeMenu').classList.toggle('on');
});
$('hHomeMenu').addEventListener('click', e => {
  const b = e.target.closest('button');
  $('hHomeMenu').classList.remove('on');
  if (b && b.dataset.id !== (mi.view || {}).home_id) { mi.room = ALL; mi.scrollTab = true; miFetch('/api/mijia/home', { id: b.dataset.id }); }
});
$('home').addEventListener('click', e => { if (!e.target.closest('.h-head')) $('hHomeMenu').classList.remove('on'); });
$('hTabs').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  mi.room = b.dataset.room;
  try { localStorage.setItem('mijiaRoom', mi.room); } catch (err) { /* private storage */ }
  renderHome();
  $('hRooms').scrollTop = 0;
});
try { mi.room = localStorage.getItem('mijiaRoom') || ALL; } catch (err) { /* private storage */ }
$('hRooms').addEventListener('click', e => {
  const p = e.target.closest('[data-power]');
  if (p) {
    const c = findCard(p.dataset.power);
    if (c) control(c.id, c.kind === 'cover' ? (c.state.position > 0 ? 'close' : 'open') : 'toggle');
    return;
  }
  const t = e.target.closest('.h-tile');
  if (t) openDetail(t.dataset.id);
});
$('hdClose').addEventListener('click', closeDetail);
$('hDetail').addEventListener('click', e => { if (e.target === $('hDetail')) closeDetail(); });
$('hdControls').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b || !mi.detail) return;
  const c = findCard(mi.detail);
  if (b.dataset.temp) {
    const cur = Number(c.state.target_temp ?? b.dataset.lo);
    const v = Math.min(Number(b.dataset.hi), Math.max(Number(b.dataset.lo), cur + Number(b.dataset.temp)));
    return control(c.id, 'target_temperature', v);
  }
  if (b.dataset.op) control(c.id, b.dataset.op, b.dataset.v);
});
$('hdControls').addEventListener('change', e => {
  const r = e.target;
  if (r.type === 'range' && mi.detail) control(mi.detail, r.dataset.op, Number(r.value));
});
$('hdControls').addEventListener('input', e => {
  const r = e.target;
  if (r.type !== 'range') return;
  const num = r.closest('.bright').querySelector('.bright-num');
  if (num) num.textContent = r.value + ({ brightness: '%', color_temp: 'K', position: '%', fan_level: '%' }[r.dataset.op] || '');
});

/* settings: 米家账号 */
let miLogoutArmed = null;
function renderMiSettings() {
  const v = mi.view;
  if (!v) return;
  const btn = $('miBtn');
  if (v.account) {
    const exp = v.account.expires_at ? `，${new Date(v.account.expires_at * 1000).toLocaleDateString('zh-CN')} 前有效` : '';
    $('miSub').textContent = v.state === 'expired' ? '登录已过期，点「家居」重新扫码'
      : `已登录 ${v.account.user}（${(v.regions || {})[v.account.region] || v.account.region}）${exp}。也可以直接说「打开客厅灯」「卧室温度多少」`;
    btn.textContent = miLogoutArmed ? '确定退出？' : '退出登录';
    btn.classList.toggle('armed', !!miLogoutArmed);
  } else {
    $('miSub').textContent = '没登录。登录后在「家居」页看设备状态，也能直接说「打开客厅灯」';
    btn.textContent = '登录';
    btn.classList.remove('armed');
  }
}
$('miBtn').addEventListener('click', () => {
  if (!mi.view || !mi.view.account) { $('settings').classList.remove('on'); return openHome(true); }
  if (!miLogoutArmed) {
    miLogoutArmed = setTimeout(() => { miLogoutArmed = null; renderMiSettings(); }, 4000);
    return renderMiSettings();
  }
  clearTimeout(miLogoutArmed); miLogoutArmed = null;
  miFetch('/api/mijia/logout');
});

/* ---------------- content cards (self.screen.show_card): markdown for the conversation ---------------- */
// Markdown: everything is escaped first; only these marks become tags. No raw HTML, no links.
function inline(text) {
  const codes = [];
  let s = esc(text).replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
  s = s.replace(/!\[([^\]]*)\]\((https:\/\/[^)\s]+)\)/g, (_, alt, url) => `<img class="md-img" src="${url}" alt="${alt}" onerror="this.remove()">`)
       .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
       .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/__([^_]+)__/g, '<b>$1</b>')
       .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>')
       .replace(/~~([^~]+)~~/g, '<s>$1</s>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}
function markdown(md) {
  const lines = String(md || '').replace(/\r/g, '').split('\n'), out = [];
  let i = 0;
  const isTableSep = l => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
  const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => inline(c.trim()));
  while (i < lines.length) {
    const l = lines[i];
    if (/^\s*```/.test(l)) {
      const buf = [];
      for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) buf.push(lines[i]);
      i++;
      out.push(`<pre>${esc(buf.join('\n'))}</pre>`);
      continue;
    }
    let m;
    if ((m = /^(#{1,6})\s+(.*)$/.exec(l))) { const n = Math.min(4, m[1].length + 1); out.push(`<h${n}>${inline(m[2])}</h${n}>`); i++; continue; }
    if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(l)) { out.push('<hr>'); i++; continue; }
    if (l.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(l);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
      out.push(`<table><thead><tr>${head.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>${
        rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
      continue;
    }
    if (/^\s*>/.test(l)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''));
      out.push(`<blockquote>${buf.map(inline).join('<br>')}</blockquote>`);
      continue;
    }
    if (/^\s*([-*+]|\d+[.)])\s+/.test(l)) {
      const ordered = /^\s*\d+[.)]\s+/.test(l), items = [];
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i]))
        items.push(inline(lines[i++].replace(/^\s*([-*+]|\d+[.)])\s+/, '').replace(/^\[( |x)\]\s*/i, (_, x) => x.trim() ? '☑ ' : '☐ ')));
      out.push(`<${ordered ? 'ol' : 'ul'}>${items.map(t => `<li>${t}</li>`).join('')}</${ordered ? 'ol' : 'ul'}>`);
      continue;
    }
    if (!l.trim()) { i++; continue; }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*```|\s*>|\s*([-*+]|\d+[.)])\s+)/.test(lines[i])
           && !(lines[i].includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1]))) buf.push(lines[i++]);
    out.push(`<p>${buf.map(inline).join('<br>')}</p>`);
  }
  return out.join('');
}

// app.js renders role 'card' messages (self.screen.show_card) with this
window.cardHtml = card => (card.images || []).map(u => `<img class="md-img" src="${esc(u)}" alt="" onerror="this.remove()">`).join('')
  // the model often starts the markdown with the title again ("# 番茄炒蛋"): don't show it twice
  + markdown(String(card.markdown || '').replace(/^\s*#{1,6}\s*(.+?)\s*(\n|$)/, (all, t) => t === card.title ? '' : all));

/* ---------------- 屏幕方向 ---------------- */
let rot = null;
function renderRotation() {
  if (!rot) return;
  const names = Object.fromEntries(rot.choices);   // [[value, name]]: a JS object would put '90', '180', '270' first
  $('rotNow').textContent = rot.applying ? '正在切换…' : (names[rot.value] || rot.value);
  $('rotChoices').innerHTML = rot.choices.map(([k, name]) =>
    `<button data-v="${k}" class="${k === rot.value ? 'on' : ''}">${esc(name)}</button>`).join('');
  $('rotAuto').setAttribute('aria-checked', rot.auto ? 'true' : 'false');
  $('rotAuto').closest('.opt').style.display = rot.sensor ? '' : 'none';
}
$('rotChoices').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b || !rot || b.dataset.v === rot.value) return;
  toast('屏幕马上转过来，页面会重新载入');
  postJson('/api/rotation', { value: b.dataset.v });
});
$('rotAuto').addEventListener('click', () => rot && postJson('/api/rotation', { auto: !rot.auto }));

/* ---------------- 大模型费用（网关 core/billing） ---------------- */
async function renderCost() {
  if (DEMO) return;
  let d;
  try { d = await (await fetch('/api/billing')).json(); } catch (e) { d = { ok: false }; }
  if (!d || d.ok === false) { $('costNote').textContent = '网关没有返回（可能是旧版网关）'; return; }
  const sym = c => (d.currencies || {})[c] || c + ' ';
  const fmt = m => { const p = Object.entries(m || {}).filter(([, v]) => v > 0).map(([c, v]) => sym(c) + (v < 1 ? v.toFixed(3) : v.toFixed(2)));
    return p.length ? p.join(' + ') : '0'; };
  $('costToday').textContent = fmt(d.today);
  $('costMonth').textContent = fmt(d.month);
  $('costTotal').textContent = fmt(d.total);
  $('costNote').textContent = d.unpriced && d.unpriced.length ? `${d.unpriced.length} 个模型没设单价` : '';
  const q = d.quota || {}, parts = [];
  const line = (name, used, limit) => {
    if (!(limit > 0)) return;
    const r = Math.min(1, used / limit);
    parts.push(`<div>${name}额度 ${sym(q.currency)}${(+used).toFixed(2)} / ${sym(q.currency)}${(+limit).toFixed(2)}${r >= 1 ? '，已用完' : ''}
      <div class="bar"><i class="${r >= 1 ? 'over' : r >= 0.8 ? 'warn' : ''}" style="transform:scaleX(${r})"></i></div></div>`);
  };
  line('今天', q.used_today, q.daily);
  line('本月', q.used_month, q.monthly);
  $('costQuota').innerHTML = parts.join('');
}
$('btnSettings').addEventListener('click', renderCost);

/* ---------------- events ---------------- */
const baseSnapshot = handlers.snapshot;
handlers.snapshot = s => {
  baseSnapshot(s);
  if (s.mijia) { mi.view = s.mijia; renderHome(); }
  if (s.rotation) { rot = s.rotation; renderRotation(); }
};
handlers.mijia = v => { mi.view = v; renderHome(); renderQuick(); };
window.miAutoClose = () => { if (mi.open || mi.detail) { openHome(false); closeDetail(); } };
$('homeDevices').addEventListener('click', e => {
  const t = e.target.closest('[data-q]');
  const c = t && findCard(t.dataset.q);
  if (c && c.controls.power && c.online) control(c.id, c.kind === 'cover' ? (isOn(c) ? 'close' : 'open') : 'toggle');
  else if (c) openDetail(c.id);
});
longPress($('homeDevices'), '[data-q]', t => openDetail(t.dataset.q));
longPress($('hRooms'), '.h-tile', t => { const c = findCard(t.dataset.id); if (c) askFavorite(c); });
// 首页在屏幕上（不在待机页、没熄屏、家居页没开）时才查常用设备
setInterval(() => quickHome(!document.hidden && !mi.open && !(typeof sb !== 'undefined' && sb.shown)
  && document.body.dataset.state !== 'sleep' && ((mi.view && mi.view.favorites) || []).length > 0), 5000);
handlers.rotation = r => { rot = r; renderRotation(); };
})();
