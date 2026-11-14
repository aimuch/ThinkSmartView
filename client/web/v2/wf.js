// 天气详情、专注计时（设计画布第 47 块；想法来自 fubin0478/thinksmart-view，MIT）。
// 在 pack.js 之后、dash.js 之前加载；用 app.js 的 $、esc、toast、ui、handlers、postJson、openSheet 不用（自己的 sheet）。
(() => {

/* ---------------- 天气 ---------------- */
const wx = { city: '', data: {}, cities: [], home: null, armed: null };
const n0 = v => (v === null || v === undefined ? '?' : Math.round(v));
const AIR_CLS = { '优': 'ok', '良': 'mid' };

function curve(hours, w, h) {
  if (!hours.length) return '';
  const temps = hours.map(x => x.temp ?? 0), lo = Math.min(...temps) - 1, hi = Math.max(...temps) + 1;
  const X = i => 20 + i * (w - 40) / Math.max(1, hours.length - 1), Y = t => h - 34 - (t - lo) / (hi - lo || 1) * (h - 80);
  const bars = hours.map((x, i) => `<rect x="${X(i) - 6}" y="${h - 26 - (x.rain || 0) * 0.3}" width="12" height="${(x.rain || 0) * 0.3}" rx="3" class="wx-rain"/>`).join('');
  const line = `<polyline points="${hours.map((x, i) => `${X(i)},${Y(x.temp ?? 0)}`).join(' ')}" class="wx-line"/>`;
  const marks = hours.map((x, i) => i % 3 ? '' : `<text x="${X(i)}" y="${Y(x.temp ?? 0) - 9}" class="wx-tv">${n0(x.temp)}°</text>` +
    `<text x="${X(i)}" y="${h - 6}" class="wx-hr">${x.hour}时</text>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}" class="wx-curve" preserveAspectRatio="none">${bars}${line}${marks}</svg>`;
}

// 页签上的短名：「上海市青浦区」→「上海」，「四川成都市」→「成都」
const PROV = /^(河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|内蒙古|广西|西藏|宁夏|新疆)(省|自治区|壮族自治区|回族自治区|维吾尔自治区)?/;
const cut = s => s.split(/[市县]/)[0];
const short = name => { const s = String(name); return cut(s.replace(PROV, '')) || cut(s) || s; };   // 「吉林市」不能剩「市」
function renderWx() {
  const homeW = wx.data[''] && wx.data[''].weather;   // 设置里没填地址：用网关按这台设备查到的地名
  const all = [{ key: '', name: `⌂ ${short(wx.home || (homeW && homeW.place) || '家')}` }, ...wx.cities.map(c => ({ key: c, name: short(c) }))];
  $('wxCities').innerHTML = all.map(c => `<button data-wxc="${esc(c.key)}" class="${c.key === wx.city ? 'on' : ''} ${wx.armed === c.key && c.key ? 'armed' : ''}">` +
    `${esc(wx.armed === c.key && c.key ? '再点删除' : c.name)}</button>`).join('') + (wx.cities.length < 5 ? '<button class="add" id="wxAdd">＋ 城市</button>' : '');
  const r = wx.data[wx.city];
  if (!r) { $('wxBody').innerHTML = '<p class="wx-msg">正在查…</p>'; return; }
  if (!r.ok) { $('wxBody').innerHTML = `<p class="wx-msg bad">${esc(r.error || '没查到')}</p>`; return; }
  const w = r.weather, portrait = innerHeight > innerWidth;
  const lo = Math.min(...w.days.map(d => d.min ?? 99)), hi = Math.max(...w.days.map(d => d.max ?? -99)), span = Math.max(1, hi - lo);
  const days = w.days.map(d => `<div class="wx-day"><b>${esc(d.label)}</b><span class="i">${d.icon}</span><span class="r">${d.rain == null ? '' : n0(d.rain) + '%'}</span>` +
    `<span class="lo">${n0(d.min)}°</span><span class="bar"><i style="left:${(d.min - lo) / span * 100}%;right:${100 - (d.max - lo) / span * 100}%"></i></span><span class="hi">${n0(d.max)}°</span></div>`).join('');
  const air = w.air.map(a => `<div class="wx-aq"><small>${esc(a.name)}</small><b>${a.value}</b><em class="${AIR_CLS[a.level] || 'bad'}">${esc(a.level)}</em></div>`).join('');
  const pm = w.air.find(a => a.key === 'pm2_5');
  $('wxBody').innerHTML = `<div class="wx-col">
      <div class="wx-cur"><span class="big">${w.icon}</span><div><b>${n0(w.temp)}°</b><span>${esc(w.text)} · 体感 ${n0(w.feels)}° · 湿度 ${n0(w.humidity)}% · ${esc(w.wind)}</span>` +
      `<span>${esc(w.place)}${w.sunrise ? ` · 日出 ${w.sunrise} · 日落 ${w.sunset}` : ''}${w.uv != null ? ` · 紫外线 ${n0(w.uv)}` : ''}</span></div></div>
      <div class="wx-card"><div class="h">接下来 24 小时<small>温度和降水概率</small></div>${curve(w.hours, portrait ? 660 : 620, 190)}</div>
      ${air ? `<div class="wx-card"><div class="h">空气质量${pm ? `<small>PM2.5 ${pm.value} · ${esc(pm.level)}</small>` : ''}</div><div class="wx-aqs">${air}</div></div>` : ''}
    </div>
    <div class="wx-card"><div class="h">7 天</div>${days}</div>`;
}

async function loadWx(city) {
  wx.city = city;
  renderWx();
  try {
    const r = await (await fetch('/api/weather?city=' + encodeURIComponent(city), { cache: 'no-store' })).json();
    wx.cities = r.cities || wx.cities; wx.home = r.home || wx.home;
    wx.data[city] = r;
  } catch (e) { wx.data[city] = { ok: false, error: '客户端没有响应' }; }
  if (wx.city === city) renderWx();
}
// 加城市：页面里的输入框（系统键盘跟着输入框起来；设备的浏览器没有 prompt）
function addRow(on) {
  $('wxAddRow').hidden = !on;
  if (on) { $('wxAddIn').value = ''; setTimeout(() => $('wxAddIn').focus(), 50); } else $('wxAddIn').blur();
}
async function addCity() {
  const name = $('wxAddIn').value.trim();
  if (!name) return;
  $('wxAddGo').disabled = true;
  const r = await postJson('/api/weather/cities', { add: name });   // 出错时 postJson 自己会提示
  $('wxAddGo').disabled = false;
  if (!r.ok) return;
  addRow(false);
  wx.cities = r.cities;
  loadWx(r.cities[r.cities.length - 1]);
}
// pointerdown 而不是 click：键盘起着的时候 WebKitGTK 收不到 click（见 app.js 的 Wi-Fi 页）
$('wxAddGo').addEventListener('pointerdown', e => { e.preventDefault(); addCity(); });
$('wxAddNo').addEventListener('pointerdown', e => { e.preventDefault(); addRow(false); });
$('wxAddIn').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addCity(); } });
function openWx(on) {
  if (!on) addRow(false);
  $('wxSheet').classList.toggle('on', on);
  if (on) loadWx(wx.city);
}
window.openWeather = openWx;
$('wxClose').addEventListener('click', () => openWx(false));
$('wxCities').addEventListener('click', async e => {
  if (e.target.closest('#wxAdd')) return addRow(true);
  const b = e.target.closest('[data-wxc]');
  if (!b) return;
  const key = b.dataset.wxc;
  if (key && wx.armed === key) {                                   // 再点一次：删掉
    wx.armed = null;
    const r = await postJson('/api/weather/cities', { remove: key });
    wx.cities = r.cities || wx.cities; delete wx.data[key];
    return loadWx('');
  }
  wx.armed = null;
  if (key !== wx.city) loadWx(key);
});
if (window.miApi) window.miApi.longPress($('wxCities'), '[data-wxc]', el => {   // 长按一个城市：再点删除
  if (!el.dataset.wxc) return;
  wx.armed = el.dataset.wxc; renderWx();
  setTimeout(() => { if (wx.armed) { wx.armed = null; renderWx(); } }, 3000);
});
document.addEventListener('click', e => {
  if (e.target.closest('#homeWeather, .dw-top .dw-now, .dw-row')) openWx(true);
});

/* ---------------- 专注 ---------------- */
const SOUND_NAME = { rain: '🌧 雨声', birds: '🐦 鸟叫', ocean: '🌊 海浪', fan: '🌀 风扇', white: '〰 白噪音' };
let fzTimer = null;
const mmss = s => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function fzLeft(f) {
  if (!f || !f.phase) return null;
  return f.paused ? f.left : Math.max(0, f.ends - Date.now() / 1000);
}
function renderFocusTick() {
  const f = ui.focus;
  const left = fzLeft(f);
  $('focusPill').hidden = left == null;
  document.body.classList.toggle('focusing', left != null);   // 仪表盘顶栏挤：专注时「待机」只留圆点
  if (left != null) $('focusLeft').textContent = mmss(left);
  if (!$('fzSheet').classList.contains('on') || !f) return;
  const total = f.phase ? f.length : f.minutes * 60;
  const shown = left == null ? total : left;
  $('fzTime').textContent = mmss(shown);
  $('fzArc').style.strokeDashoffset = String(553 * (1 - shown / Math.max(1, total)));
}
function renderFocus() {
  const f = ui.focus;
  if (!f) return;
  const t = f.today || {};
  $('fzSub').textContent = `${f.phase === 'focus' ? `第 ${(t.rounds || 0) + 1} 个番茄 · ` : ''}今天专注 ${t.minutes || 0} 分钟`;
  $('fzPhase').textContent = !f.phase ? '准备好了' : f.paused ? '暂停了' : f.phase === 'break' ? '休息中' : '专注中';
  $('fzSounds').innerHTML = f.sound_choices.map(k => `<button data-fs="${k}" class="${f.sounds.includes(k) ? 'on' : ''}">${SOUND_NAME[k] || k}</button>`).join('') +
    `<button data-fs="" class="${f.sounds.length ? '' : 'on'}">不放</button>`;
  $('fzMinutes').innerHTML = f.choices.map(m => `<button data-fm="${m}" class="${m === f.minutes && f.phase !== 'break' ? 'on' : ''}">${m} 分钟</button>`).join('') +
    `<button data-fm="break" class="${f.phase === 'break' ? 'on' : ''}">休息 ${f.break} 分钟</button>`;
  $('fzBtns').innerHTML = (!f.phase ? '<button class="upd-btn pri" data-fa="start">开始</button>'
    : `<button class="upd-btn" data-fa="${f.paused ? 'resume' : 'pause'}">${f.paused ? '继续' : '暂停'}</button><button class="upd-btn" data-fa="stop">结束</button>`)
    + (ui.study && ui.study.camera ? '<button class="upd-btn fz-day" data-fa="day">📊 学习记录</button>' : '');
  clearInterval(fzTimer); fzTimer = null;
  if (f.phase) fzTimer = setInterval(renderFocusTick, 1000);
  renderFocusTick();
}
async function focusPost(body) {
  const r = await postJson('/api/focus', body);
  if (r.focus) { ui.focus = r.focus; renderFocus(); }
}
function openFocus(on) {
  $('fzSheet').classList.toggle('on', on);
  if (on) renderFocus();
}
window.openFocus = openFocus;
handlers.focus = f => { ui.focus = f; renderFocus(); };
$('fzClose').addEventListener('click', () => openFocus(false));
$('tryFocus').addEventListener('click', () => openFocus(true));
$('dFocusBtn').addEventListener('click', () => openFocus(true));
$('focusPill').addEventListener('click', () => openFocus(true));
$('fzSounds').addEventListener('click', e => {
  const b = e.target.closest('[data-fs]');
  if (!b || !ui.focus) return;
  const k = b.dataset.fs, cur = ui.focus.sounds;
  const next = !k ? [] : cur.includes(k) ? cur.filter(x => x !== k) : [...cur, k].slice(-2);
  focusPost({ sounds: next });
});
$('fzMinutes').addEventListener('click', e => {
  const b = e.target.closest('[data-fm]');
  if (!b) return;
  if (b.dataset.fm === 'break') focusPost({ action: 'break' });
  else focusPost(ui.focus && ui.focus.phase ? { action: 'start', minutes: +b.dataset.fm } : { minutes: +b.dataset.fm });
});
$('fzBtns').addEventListener('click', e => {
  const b = e.target.closest('[data-fa]');
  if (b && b.dataset.fa === 'day') return openStudyDay(true);
  if (b) focusPost({ action: b.dataset.fa, minutes: ui.focus && ui.focus.minutes });
});

/* ---------------- 学习陪伴（设计画布第 48 块） ---------------- */
const st = { date: null, data: null };
const agoText = s => s < 60 ? '刚刚' : s < 3600 ? `${Math.round(s / 60)} 分钟前` : `${Math.round(s / 3600)} 小时前`;
const hm = t => { const d = new Date(t * 1000); return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`; };
const dur = m => m >= 60 ? `${Math.floor(m / 60)} 小时${m % 60 ? ` ${m % 60} 分` : ''}` : `${m} 分钟`;
let studyGot = 0;   // 收到 study 的时间：「上次」多久以前从这里往后算

function studyLine(s) {
  if (s.error) return `没看成：${s.error}`;
  if (s.last) return `上次：${s.last.text || '看过了'}（${agoText(s.last.ago + (Date.now() - studyGot) / 1000)}）`;
  return s.enabled ? '马上看第一眼' : '每分钟看一眼坐姿，照片不保存';
}
function renderStudy() {
  const s = ui.study;
  if (!s) return;
  $('studyPill').hidden = !s.enabled;
  document.body.classList.toggle('studying', !!s.enabled);
  $('fzStudy').hidden = !s.camera;
  $('fzStudySw').setAttribute('aria-checked', String(!!s.enabled));
  $('fzStudySub').textContent = studyLine(s);
  $('studyCard').hidden = !s.camera;
  $('studySw').setAttribute('aria-checked', String(!!s.enabled));
  $('studySub').textContent = !s.enabled ? '关着'
    : s.auto_off_left ? `开着 · ${dur(Math.ceil(s.auto_off_left / 60))}后自动关` : '开着';
  $('studyInt').innerHTML = s.intervals.map(v => `<button data-si="${v}" class="${v === s.interval ? 'on' : ''}">${v / 60} 分钟</button>`).join('');
  $('studyOff').innerHTML = s.auto_off_choices.map(v => `<button data-so="${v}" class="${v === s.auto_off ? 'on' : ''}">${v ? v + ' 小时' : '不关'}</button>`).join('');
  $('studyIssues').innerHTML = s.issue_choices.map(k => `<button data-sk="${k}" class="${s.issues.includes(k) ? 'on' : ''}">${esc(s.issue_names[k])}</button>`).join('');
  if ($('fzSheet').classList.contains('on') && ui.focus) renderFocus();
}
let bubbleTimer = null;
function showBubble(said) {
  $('studyBubbleText').textContent = said.text;
  $('studyBubbleSub').textContent = `小麦说的${said.issue ? ' · ' + said.issue : ''}`;
  $('studyBubble').hidden = false;
  clearTimeout(bubbleTimer);
  bubbleTimer = setTimeout(() => { $('studyBubble').hidden = true; }, 6000);
}
handlers.study = s => {
  ui.study = s; studyGot = Date.now();
  renderStudy();
  if (s.said) showBubble(s.said);
  if ($('stSheet').classList.contains('on') && st.date === today()) loadStudyDay(st.date);
};
setInterval(() => { if (ui.study && ui.study.last) $('fzStudySub').textContent = studyLine(ui.study); }, 30000);
async function studyPost(body) {
  const r = await postJson('/api/study', body);
  if (r.study) handlers.study(r.study);
}
$('studySw').addEventListener('click', () => studyPost({ enabled: !(ui.study && ui.study.enabled) }));
$('fzStudySw').addEventListener('click', async () => {
  const r = await postJson('/api/focus', { study: !(ui.study && ui.study.enabled) });
  if (r.study) handlers.study(r.study);
});
$('studyInt').addEventListener('click', e => { const b = e.target.closest('[data-si]'); if (b) studyPost({ interval: +b.dataset.si }); });
$('studyOff').addEventListener('click', e => { const b = e.target.closest('[data-so]'); if (b) studyPost({ auto_off: +b.dataset.so }); });
$('studyIssues').addEventListener('click', e => {
  const b = e.target.closest('[data-sk]');
  if (!b || !ui.study) return;
  const k = b.dataset.sk, cur = ui.study.issues;
  studyPost({ issues: cur.includes(k) ? cur.filter(x => x !== k) : [...cur, k] });
});

// 学习记录：家长看今天（或前几天）陪了多久、专不专心、提醒了什么
function today() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function dayName(date) {
  const t = new Date(today() + 'T00:00:00'), d = new Date(date + 'T00:00:00');
  const n = Math.round((t - d) / 86400000);
  return n === 0 ? '今天' : n === 1 ? '昨天' : `${d.getMonth() + 1}月${d.getDate()}日`;
}
function renderStudyDay() {
  const r = st.data;
  const days = [...new Set([today(), ...((r && r.days) || [])])].slice(0, 6);
  $('stDays').innerHTML = days.map(d => `<button data-sd="${d}" class="${d === st.date ? 'on' : ''}">${dayName(d)}</button>`).join('');
  if (!r || !r.day) { $('stBody').innerHTML = '<p class="wx-msg">正在读…</p>'; return; }
  const d = r.day;
  if (!d.checks) {
    $('stBody').innerHTML = `<p class="wx-msg">${dayName(st.date)}没开学习陪伴。${ui.study && ui.study.camera ? '在专注页打开那一行，或者说「打开学习陪伴」。' : ''}</p>`;
    return;
  }
  const rounds = st.date === today() && ui.focus ? (ui.focus.today || {}).rounds || 0 : null;
  const t0 = d.spans[0].start, t1 = d.spans[d.spans.length - 1].end, span = Math.max(1, t1 - t0);
  const tl = d.spans.map(x => `<i class="${x.kind}" style="left:${(x.start - t0) / span * 100}%;width:${(x.end - x.start) / span * 100}%"></i>`).join('');
  const most = Math.max(1, ...Object.values(d.reminders));
  const rem = Object.entries(d.reminders).map(([k, n]) => `<div class="st-hb"><span>${esc(d.names[k])}</span><span class="bar"><i style="width:${n / most * 100}%"></i></span><b>${n}</b></div>`).join('');
  const recent = d.recent.map(x => `<span>${hm(x.t)}${x.until > x.t ? '–' + hm(x.until) : ''}</span><div>${esc(x.text || '人不在桌前')}${x.said ? ` → <b>提醒了${esc(x.said)}</b>` : ''}</div>`).join('');
  $('stBody').innerHTML = `
    <div class="st-kpi"><div><small>在桌前</small><b>${dur(d.present_minutes)}</b></div>
      <div><small>专心</small><b>${d.focused_pct == null ? '—' : d.focused_pct + '%'}</b></div>
      <div><small>提醒</small><b>${d.reminder_total} 次</b></div>
      <div><small>${rounds == null ? '看了' : '番茄'}</small><b>${rounds == null ? d.checks + ' 次' : rounds + ' 个'}</b></div></div>
    <div class="wx-card"><div class="h">时间线<small>${hm(t0)} – ${hm(t1)}</small></div>
      <div class="st-tl">${tl}</div>
      <div class="st-leg"><span><i class="focus"></i>专心</span><span><i class="drift"></i>走神、发呆</span><span><i class="bad"></i>玩手机、趴着、睡着</span><span><i class="absent"></i>不在桌前</span></div></div>
    <div class="st-grid"><div class="wx-card"><div class="h">提醒了什么</div>${rem}</div>
      <div class="wx-card"><div class="h">最近<small>只记文字，不存照片</small></div><div class="st-log">${recent}</div></div></div>`;
}
async function loadStudyDay(date) {
  st.date = date;
  if (!st.data || st.data.date !== date) { st.data = st.data && { ...st.data, day: null }; renderStudyDay(); }
  try {
    st.data = await (await fetch('/api/study?date=' + date, { cache: 'no-store' })).json();
  } catch (e) { return; }
  if (st.date === date) renderStudyDay();
}
function openStudyDay(on) {
  $('stSheet').classList.toggle('on', on);
  if (on) loadStudyDay(today());
}
window.openStudyDay = openStudyDay;
$('stClose').addEventListener('click', () => openStudyDay(false));
$('stDays').addEventListener('click', e => { const b = e.target.closest('[data-sd]'); if (b) loadStudyDay(b.dataset.sd); });
$('studyPill').addEventListener('click', () => openStudyDay(true));
$('studyDayBtn').addEventListener('click', () => openStudyDay(true));


/* ---------------- AirPlay 隔空播放（设计画布第 49 块） ---------------- */
let apNameEditing = false;
function renderAirPlay() {
  const a = ui.airplay;
  const playing = !!(a && a.enabled && a.playing);
  document.body.classList.toggle('ap-playing', playing);
  $('apChipText').textContent = playing ? [a.title, a.artist].filter(Boolean).join(' · ') || `${a.client || '手机'} 正在放` : '';
  if (!a) return;
  $('apCard').hidden = !a.available;
  const ready = a.installed && !a.installing;
  $('apSwitch').hidden = !ready;
  $('apSwitch').setAttribute('aria-checked', String(!!a.enabled));
  $('apCard').classList.toggle('off', ready && !a.enabled);
  $('apSub').textContent = a.installing ? '正在安装…' : !a.installed ? '还没装' : a.enabled ? `iPhone 上显示为「${a.name}」` : '';
  $('apProg').hidden = !a.installing;
  $('apInstall').hidden = a.installed || a.installing;
  $('apNow').hidden = !playing;
  $('apNameRow').hidden = !(ready && a.enabled) || playing;
  let note;
  if (a.error) note = `出错了：${a.error}`;
  else if (a.installing) note = '从 Ubuntu 软件源下载，一两分钟。装的时候别断电。';
  else if (!a.installed) note = 'iPhone、iPad、Mac 可以把歌直接放到这台设备上。需要先联网装一个小程序（shairport-sync，约 1 MB，不开的时候不占后台）。';
  else if (!a.enabled) note = '打开后，iPhone 的控制中心里点「隔空播放」就能选这台设备。';
  else note = `在 iPhone 的控制中心点「隔空播放」，选「${a.name}」。放歌时说话或闹钟响，歌声会自动变小或停下。`;
  $('apNote').textContent = note;
  if (playing) {
    $('apTitle').textContent = [a.title, a.artist].filter(Boolean).join(' · ') || '正在放';
    $('apClient').textContent = a.client ? `${a.client} 正在放` : '';
  }
  if (!apNameEditing) { $('apName').value = a.custom_name || ''; $('apName').placeholder = a.default_name; }
}
handlers.airplay = a => { ui.airplay = a; renderAirPlay(); };
async function apPost(body) {
  const r = await postJson('/api/airplay', body);
  if (r.airplay) handlers.airplay(r.airplay);
}
$('apSwitch').addEventListener('click', () => apPost({ enabled: !(ui.airplay && ui.airplay.enabled) }));
$('apInstall').addEventListener('click', () => apPost({ install: true }));
$('apChip').addEventListener('click', () => { setPane('net'); $('settings').classList.add('on'); });
$('apName').addEventListener('focus', () => { apNameEditing = true; });
$('apName').addEventListener('blur', () => { apNameEditing = false; });
function apSaveName() { apNameEditing = false; $('apName').blur(); apPost({ name: $('apName').value.trim() }); }
$('apNameSave').addEventListener('pointerdown', e => { e.preventDefault(); apSaveName(); });   // 键盘起着时收不到 click
$('apName').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); apSaveName(); } });
fetch('/api/airplay', { cache: 'no-store' }).then(r => r.json()).then(r => { if (r.airplay) handlers.airplay(r.airplay); }).catch(() => {});


/* ---------------- 复刻我的声音（设计画布第 50 块） ---------------- */
// 状态：idle → rec（念，最多 20 秒）→ done（起名）→ sending → sent；出错留在原来那步，下面一行写原因
const cl = { step: 'idle', t0: 0, seconds: 0, max: 20, name: '', timer: null, msg: '' };
const CL_NAMES = ['妈妈', '爸爸', '奶奶', '爷爷', '外婆', '外公'];
const CL_PASSAGE = '今天天气不错，我们一起去公园散散步吧。回来的路上顺便买点水果，晚上做你最爱吃的番茄炒蛋。小麦，记得提醒我明天早上七点起床哦。';
function renderClone() {
  const recording = cl.step === 'rec';
  const shown = recording ? Math.min(cl.max, (Date.now() - cl.t0) / 1000) : cl.seconds;
  $('clTime').textContent = `0:${String(Math.floor(shown)).padStart(2, '0')}`;
  $('clArc').style.strokeDashoffset = String(553 * (1 - shown / cl.max));
  $('clState').textContent = { idle: '准备好了', rec: '正在录', done: '录好了', sending: '正在交给百炼…', sent: '正在复刻' }[cl.step];
  $('clNames').hidden = cl.step !== 'done';
  $('clChips').innerHTML = CL_NAMES.map(n => `<button data-cn="${n}" class="${n === cl.name ? 'on' : ''}">${n}</button>`).join('');
  $('clBtns').innerHTML = {
    idle: '<button class="upd-btn pri" data-ca="start">开始录</button>',
    rec: '<button class="upd-btn" data-ca="restart">重录</button><button class="upd-btn pri" data-ca="stop">念完了</button>',
    done: `<button class="upd-btn" data-ca="restart">重录</button><button class="upd-btn pri" data-ca="upload" ${cl.name ? '' : 'disabled'}>交给百炼复刻</button>`,
    sending: '', sent: '<button class="upd-btn pri" data-ca="close">好的</button>' }[cl.step];
  $('clMsg').textContent = cl.msg;
  $('clMsg').classList.toggle('warn', cl.msg.startsWith('⚠'));
}
async function clAction(action) {
  const body = { action };
  if (action === 'upload') body.name = cl.name;
  const r = await postJson('/api/voice/clone', body);
  return r;
}
async function clStart() {
  clearInterval(cl.timer);
  const r = await clAction('start');
  if (!r.ok) { cl.msg = '⚠ ' + (r.error || '开不了录音'); return renderClone(); }
  Object.assign(cl, { step: 'rec', t0: Date.now(), max: r.seconds || 20, msg: '' });
  if (r.passage) $('clPassage').textContent = r.passage;
  cl.timer = setInterval(() => { renderClone(); if ((Date.now() - cl.t0) / 1000 >= cl.max) clStop(); }, 250);
  renderClone();
}
async function clStop() {
  clearInterval(cl.timer); cl.timer = null;
  const r = await clAction('stop');
  if (!r.ok) { Object.assign(cl, { step: 'idle', seconds: 0, msg: '⚠ ' + (r.error || '没录上') }); return renderClone(); }
  Object.assign(cl, { step: 'done', seconds: r.seconds, msg: `录了 ${r.seconds} 秒。这个声音叫什么？` });
  renderClone();
}
async function clUpload() {
  cl.step = 'sending'; cl.msg = ''; renderClone();
  const r = await clAction('upload');
  if (!r.ok) { Object.assign(cl, { step: 'done', msg: '⚠ ' + (r.error || '没复刻成') }); return renderClone(); }
  Object.assign(cl, { step: 'sent', msg: `「${r.clone.name}」交给百炼了，一般 1 分钟内复刻好。好了以后在「回答的声音」里选它。录音只发给百炼，网关不留。` });
  renderClone();
  if (typeof loadVoice === 'function') loadVoice();
}
function openClone(on) {
  if (!on && (cl.step === 'rec' || cl.step === 'done')) clAction('cancel');
  clearInterval(cl.timer); cl.timer = null;
  $('clSheet').classList.toggle('on', on);
  if (on) {
    Object.assign(cl, { step: 'idle', seconds: 0, name: '', msg: '' });
    $('clPassage').textContent = CL_PASSAGE;
    $('clName').value = '';
    renderClone();
  }
}
window.openClone = openClone;
$('clClose').addEventListener('click', () => openClone(false));
$('clBtns').addEventListener('click', e => {
  const b = e.target.closest('[data-ca]');
  if (!b) return;
  const a = b.dataset.ca;
  if (a === 'start' || a === 'restart') { if (a === 'restart' && cl.step === 'rec') clAction('cancel').then(clStart); else clStart(); }
  else if (a === 'stop') clStop();
  else if (a === 'upload') clUpload();
  else if (a === 'close') openClone(false);
});
$('clChips').addEventListener('click', e => {
  const b = e.target.closest('[data-cn]');
  if (b) { cl.name = b.dataset.cn; $('clName').value = ''; renderClone(); }
});
$('clName').addEventListener('input', () => {
  cl.name = $('clName').value.trim();
  for (const b of $('clChips').children) b.classList.remove('on');
  const up = $('clBtns').querySelector('[data-ca=upload]');
  if (up) up.disabled = !cl.name;
});
$('clName').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('clName').blur(); } });


/* ---------------- 视频对讲（设计画布第 51 块） ---------------- */
let callTick = null;
const mmssOf = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
function renderCall() {
  const c = ui.intercom;
  if (!c) return;
  const st = c.state, live = st === 'connecting' || st === 'talking';
  $('callView').hidden = st === 'idle';
  $('callView').dataset.state = st;
  document.body.classList.toggle('in-call', st !== 'idle');
  // 画面只在接通以后拉（不然一直开着连接）；结束时断开
  const want = live ? '/api/call/video/' : '';
  if (($('callRemote').dataset.on || '') !== want) {
    $('callRemote').dataset.on = want;
    $('callRemote').src = want ? want + 'remote' : '';
    $('callSelf').src = want ? want + 'self' : '';
    if (!want) { $('callRemote').removeAttribute('src'); $('callSelf').removeAttribute('src'); }
  }
  $('callRing').hidden = live;
  $('callCtrls').hidden = !live;
  $('callBig').hidden = st !== 'ringing';
  $('callAv').textContent = (c.caller || '家').slice(0, 1);
  $('callAv').classList.toggle('pulse', st === 'ringing');
  $('callTitle').textContent = st === 'ringing' ? `${c.caller} 打来视频电话` : st === 'ended' ? c.ended : c.caller || '';
  $('callNote').textContent = st === 'ringing' ? (c.auto_in != null && c.auto_answer ? `${c.auto_in} 秒后自动接通` : '通过 Tailscale · 手机') : '';
  $('callMute').classList.toggle('on', !!c.muted);
  $('callCam').classList.toggle('on', !!c.camera_off);
  clearInterval(callTick); callTick = null;
  const tick = () => { $('callTime').textContent = st === 'talking' && c.started ? `${mmssOf(Date.now() / 1000 - c.started)} · ${c.caller}` : '正在接通…'; };
  if (live) { tick(); callTick = setInterval(tick, 1000); }
  // 设置 → 家人 → 视频对讲
  $('callAuto').setAttribute('aria-checked', String(!!c.auto_answer));
  $('callHistory').innerHTML = (c.history || []).slice(0, 5).map(h => `<div class="al"><div class="al-text"><b>${esc(h.caller)}</b>` +
    `<span${h.missed ? ' class="bad"' : ''}>${new Date(h.t * 1000).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })} · ${h.missed ? '未接' : mmssOf(h.seconds)}</span></div></div>`).join('');
  $('callSub').textContent = (c.history || []).some(h => h.missed) ? '有未接来电' : '';
}
handlers.intercom = c => { ui.intercom = c; renderCall(); };
async function callPost(action, value) {
  const r = await postJson('/api/call', { action, value });
  if (r.intercom) handlers.intercom(r.intercom);
}
$('callView').addEventListener('click', e => {
  const b = e.target.closest('[data-ca2]');
  if (!b || !ui.intercom) return;
  const a = b.dataset.ca2;
  if (a === 'mute') callPost('mute', !ui.intercom.muted);
  else if (a === 'camera') callPost('camera', !ui.intercom.camera_off);
  else callPost(a);
});
$('callAuto').addEventListener('click', () => callPost('auto_answer', !(ui.intercom && ui.intercom.auto_answer)));
async function loadCallInfo() {
  try {
    const r = await (await fetch('/api/call/info', { cache: 'no-store' })).json();
    $('callUrl').innerHTML = r.url ? `家人手机装好 Tailscale、登进家里的网络后，用 Safari 打开 <b>${esc(r.url)}</b>，选这台设备点「视频通话」。`
      : esc(r.reason || '网关上还没装好 Tailscale');
  } catch (e) { $('callUrl').textContent = '连不上小麦网关'; }
}
document.addEventListener('click', e => { if (e.target.closest('[data-pane="family"]')) loadCallInfo(); });
window.loadCallInfo = loadCallInfo;
loadCallInfo();


/* ---------------- 说完以后怎么听（设计画布第 52 块） ---------------- */
const LISTEN_OPTS = { question: ['它问我时才接着听', '回答在问你（「要提醒你带伞吗？」）才接着听，默认'],
  always: ['每次都接着听', '可以连着聊；配合「只听刚才说话的人」不怕旁人插话'],
  never: ['每句都要唤醒', '回答完马上关麦，下一句先喊唤醒词'] };
const SCENE = { cook: '做菜模式中：现在每次都接着听', translate: '翻译模式中：现在每次都接着听（30 秒）',
  study: '学习陪伴开着：现在每次都接着听', sleep: '睡眠时段：现在每句都要唤醒' };
function renderListen() {
  const v = ui.listen;
  if (!v) return;
  $('listenOpts').innerHTML = v.choices.map(k => `<button data-lo="${k}" class="${k === v.follow_up ? 'on' : ''}"><b>${LISTEN_OPTS[k][0]}</b><span>${LISTEN_OPTS[k][1]}</span></button>`).join('');
  $('listenSecs').innerHTML = v.seconds_choices.map(s => `<button data-ls="${s}" class="${s === v.seconds ? 'on' : ''}">${s} 秒</button>`).join('');
  $('listenLock').setAttribute('aria-checked', String(!!v.lock));
  $('listenNote').textContent = (v.scene ? SCENE[v.scene] + '。' : '') + '做菜、翻译、学习陪伴时自动每次都接着听；睡眠时段自动每句都要唤醒。也可以说「以后每句都要叫我」「以后连着聊」。';
}
handlers.listen = v => { ui.listen = v; renderListen(); };
async function listenPost(body) {
  const r = await postJson('/api/listen', body);
  if (r.listen) handlers.listen(r.listen);
}
$('listenOpts').addEventListener('click', e => { const b = e.target.closest('[data-lo]'); if (b) listenPost({ follow_up: b.dataset.lo }); });
$('listenSecs').addEventListener('click', e => { const b = e.target.closest('[data-ls]'); if (b) listenPost({ seconds: +b.dataset.ls }); });
$('listenLock').addEventListener('click', () => listenPost({ lock: !(ui.listen && ui.listen.lock) }));

// 顶栏「还在听 · N 秒」：回答完接着听时，页面看得见麦克风还开着
let windowTimer = null;
handlers.window = w => {
  clearInterval(windowTimer); windowTimer = null;
  const until = w && w.until, total = (w && w.seconds) || 10;
  const tick = () => {
    const left = until ? until - Date.now() / 1000 : 0;
    $('windowPill').hidden = left <= 0;
    document.body.classList.toggle('follow-window', left > 0);
    if (left <= 0) { clearInterval(windowTimer); windowTimer = null; return; }
    $('windowLeft').textContent = `${Math.ceil(left)} 秒`;
    $('windowArc').style.strokeDashoffset = String(94 * (1 - left / total));
  };
  tick();
  if (until) windowTimer = setInterval(tick, 250);
};

})();
