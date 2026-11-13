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

function renderWx() {
  const all = [{ key: '', name: `⌂ ${wx.home || '家'}` }, ...wx.cities.map(c => ({ key: c, name: c.split(/[市区县]/)[0] || c }))];
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
  $('fzBtns').innerHTML = !f.phase ? '<button class="upd-btn pri" data-fa="start">开始</button>'
    : `<button class="upd-btn" data-fa="${f.paused ? 'resume' : 'pause'}">${f.paused ? '继续' : '暂停'}</button><button class="upd-btn" data-fa="stop">结束</button>`;
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
  if (b) focusPost({ action: b.dataset.fa, minutes: ui.focus && ui.focus.minutes });
});

})();
