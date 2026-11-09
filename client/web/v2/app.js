// ThinkSmart voice client page v2: renders what the client streams on /events and sends the buttons back.
// Same contract as v1. Open with ?demo (or ?demo=<scene>) to preview without a client.
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const pad = n => String(n).padStart(2, '0');
const WEEK = '日一二三四五六';
const DEMO = new URLSearchParams(location.search).get('demo');   // null when not a demo
if (DEMO !== null && /[?&]still\b/.test(location.search)) document.documentElement.classList.add('still');

const ui = {
  status: { state: 'idle', connected: false, uploading: false },
  models: { current: null, options: [] },
  thread: { entry: null, messages: [] },
  ptt: false,
  alarms: { synced: true, items: [], missed: [], ringing: null, volume: 70, tone: 'coffee', tones: {} },
  bluetooth: null,
  wake: null,   // wake words (wake_words.py): {words: [{label, pinyin}], primary, custom, recording, max}
};
const wakeName = () => esc((ui.wake && ui.wake.primary) || '小麦小麦');

const SUGGEST = [
  ['明天北京天气怎么样？', '#ffb54729', '#ffcf7a', '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6L6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4"/>'],
  ['帮我想个周末计划', '#5ee0ff24', '#8ff0ff', '<path d="M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z"/>'],
  ['讲个冷笑话', '#ff7ac824', '#ff9bd6', '<circle cx="12" cy="12" r="9"/><path d="M8 14.5a5 5 0 0 0 8 0M9 9.5h.01M15 9.5h.01"/>'],
  ['把音量调小一点', '#9b7bff2b', '#c8b4ff', '<path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M17 12h4"/>'],
];

/* ---- clock ---- */
function tick() {
  const now = new Date();
  const hm = pad(now.getHours()) + ':' + pad(now.getMinutes());
  $('time').textContent = hm;
  $('bigTime').textContent = hm;
  const lunar = ui.today && ui.today.lunar;
  $('bigDate').textContent = `${now.getMonth() + 1}月${now.getDate()}日  星期${WEEK[now.getDay()]}` +
    (lunar && lunar.text ? `  ·  农历${lunar.text}${lunar.term ? ' ' + lunar.term : ''}${lunar.festival ? ' ' + lunar.festival : ''}` : '');
  const h = now.getHours();
  $('greet').textContent = h < 5 ? '夜深了' : h < 9 ? '早上好' : h < 12 ? '上午好' : h < 14 ? '中午好' : h < 18 ? '下午好' : h < 23 ? '晚上好' : '夜深了';
  $('ringTime').textContent = hm;
  tickAlarms();
}

/* ---- the orb's corona: 48 bars around the orb, one shape per state ----
   idle: short and still. connecting: 12 dashes, turned slowly by CSS. thinking: a comet orbiting (CSS).
   listening: the whole ring pushes outward with your voice, all bars together — loud is big, quiet barely moves.
   speaking: three lobes turning round the ring, swelling with the reply's volume.
   Only listening/speaking redraw every frame; any state change morphs the bars over 0.4 s, then the loop stops.
   All bars are drawn on one canvas: as 48 elements, restyling them 30 times a second kept
   the device's main thread at ~93% and the page at ~17 fps. */
const BARS = 48, R_IN = 110, BAR_W = 6, REST = 8, REACH = 46;   // px; REST + REACH stays inside the canvas
const corona = document.createElement('canvas');
const CSIZE = 330, DPR = Math.min(2, window.devicePixelRatio || 1);
corona.width = corona.height = CSIZE * DPR;
corona.style.width = corona.style.height = '100%';
$('corona').appendChild(corona);
const cx = corona.getContext('2d');
const ANG = Array.from({ length: BARS }, (_, i) => i / BARS * Math.PI * 2);
const COS = ANG.map(Math.cos), SIN = ANG.map(Math.sin);
// per bar: [length px, opacity]; what is on screen, where a morph starts, and where the state wants it
const cur = new Float32Array(BARS * 2), from = new Float32Array(BARS * 2), tgt = new Float32Array(BARS * 2);
let barFill = null, barFillState = '';
function fillFor(state) {
  // one radial gradient for all bars: inner end --c2, outer end --c1
  if (barFillState !== state) {
    const css = getComputedStyle(document.body);
    barFill = cx.createRadialGradient(0, 0, R_IN, 0, 0, R_IN + REST + REACH);
    barFill.addColorStop(0, css.getPropertyValue('--c2').trim());
    barFill.addColorStop(1, css.getPropertyValue('--c1').trim());
    barFillState = state;
  }
  return barFill;
}
// Bars of (nearly) the same opacity share one path and one fill: 48 separate fills cost the device's
// main thread ~20% of a core while speaking. The path takes each bar's transform as it is added.
const bucket = new Uint8Array(BARS);
function drawBars(bars) {
  const c = CSIZE * DPR / 2;
  cx.setTransform(1, 0, 0, 1, 0, 0);
  cx.clearRect(0, 0, corona.width, corona.height);
  cx.fillStyle = fillFor(document.body.dataset.state || 'idle');
  for (let i = 0; i < BARS; i++) bucket[i] = bars[i * 2] < .5 ? 0 : Math.max(1, Math.round(bars[i * 2 + 1] * 20));
  for (let q = 1; q <= 20; q++) {
    if (!bucket.includes(q)) continue;
    cx.beginPath();
    for (let i = 0; i < BARS; i++) {
      if (bucket[i] !== q) continue;
      cx.setTransform(COS[i] * DPR, SIN[i] * DPR, -SIN[i] * DPR, COS[i] * DPR, c, c);
      cx.roundRect(-BAR_W / 2, -R_IN - bars[i * 2], BAR_W, bars[i * 2], BAR_W / 2);
    }
    cx.setTransform(DPR, 0, 0, DPR, c, c);   // the radial gradient is centred on the orb
    cx.globalAlpha = q / 20;
    cx.fill();
  }
  cx.globalAlpha = 1;
}

// target shape of a state into out; v = sound level 0..1, p = phase (s), t = seconds in this state
function shapeOf(state, v, p, t, out) {
  for (let i = 0; i < BARS; i++) {
    const a = ANG[i], f = i / BARS;
    let len = REST, alpha = 1;
    if (state === 'thinking') { len = 6.6 + 28.5 * f * f; alpha = .1 + .9 * f ** 1.6; }
    else if (state === 'connecting') { len = i % 4 ? 4.2 : 16.5; alpha = i % 4 ? .35 : 1; }
    else if (state === 'listening') {
      // a short burst on waking says "I'm here", then the ring follows the voice as one
      const env = Math.max(v, .5 * Math.max(0, 1 - t / .6) ** 2);
      const grain = .84 + .16 * Math.sin(a * 5 + p * 3.1) * Math.sin(a * 3 - p * 2.3);
      len = REST - 1 + 1.2 * Math.sin(a * 4 + p * 1.5) + REACH * env * grain;
    } else if (state === 'speaking') {
      const lobe = .5 + .5 * Math.sin(a * 3 - p * 1.3);
      len = REST + 4 * lobe + REACH * v * (.3 + .7 * lobe);
      alpha = .6 + .4 * lobe;
    }
    out[i * 2] = len;
    out[i * 2 + 1] = alpha;
  }
}

// The mic level (RMS) in decibels, so a quiet voice visibly moves the corona. Measured on the device
// (2026-11-01, 9 listening turns): room noise -74..-80 dBFS with rare blips to -62, normal speech
// -60..-42 dBFS. The old linear scale (full at -23 dBFS) moved the corona 1–7% for that speech.
// Now noise stays 0, a quiet voice (-55) reaches ~30%, a normal one (-45) ~58%, a loud one (-30) 100%.
const MIC_FLOOR = -66, MIC_FULL = -30;   // dBFS
const micLevel = rms => Math.min(1, Math.max(0, (20 * Math.log10(rms + 1e-6) - MIC_FLOOR) / (MIC_FULL - MIC_FLOOR)));

const STILL = document.documentElement.classList.contains('still');
const level = { target: 0, value: 0, phase: 0, raf: 0, last: 0, state: '', since: 0, mix: 1 };
const isLive = s => s === 'listening' || s === 'speaking';
function setCorona(state) {
  if (state === level.state) return;
  // morph from what is on screen; a spinning corona is turned by CSS, so carry its angle into the bars
  const m = getComputedStyle($('corona')).transform.match(/matrix\(([^,]+),\s*([^,]+)/);
  const k = m ? Math.round(Math.atan2(+m[2], +m[1]) / (Math.PI * 2) * BARS) : 0;
  for (let i = 0; i < BARS; i++) {
    const j = ((i - k) % BARS + BARS) % BARS;
    from[i * 2] = cur[j * 2]; from[i * 2 + 1] = cur[j * 2 + 1];
  }
  level.state = state;
  level.since = performance.now();
  level.mix = level.last ? 0 : 1;   // the first paint just draws
  if (!isLive(state)) level.target = 0;
  if (STILL) {   // screenshots: one fairly loud, settled frame
    shapeOf(state, isLive(state) ? .7 : 0, 1.2, 9, cur);
    drawBars(cur);
    if (isLive(state)) drawLive(.7, 1.2);
    return;
  }
  if (!level.raf) {
    level.last = performance.now() - 34;
    level.raf = requestAnimationFrame(levelLoop);
  }
}
function levelLoop(now) {
  level.raf = requestAnimationFrame(levelLoop);
  if (now - level.last < 33) return;      // ~30 fps is plenty
  const dt = Math.min(.1, (now - level.last) / 1000);
  level.last = now;
  level.value += (level.target - level.value) * (level.target > level.value ? .55 : .2);
  level.phase += dt * 2.4;
  const live = isLive(level.state);
  shapeOf(level.state, level.value, level.phase, (now - level.since) / 1000, tgt);
  if (level.mix < 1) {
    level.mix = Math.min(1, level.mix + dt / .4);
    const e = 1 - (1 - level.mix) ** 3;
    for (let i = 0; i < tgt.length; i++) cur[i] = from[i] + (tgt[i] - from[i]) * e;
  } else cur.set(tgt);
  drawBars(cur);
  if (live || level.mix < 1) drawLive(level.value, level.phase);   // after live: let the level settle
  else {
    cancelAnimationFrame(level.raf);
    level.raf = 0;
    level.target = level.value = 0;
    $('corona').style.transform = $('core').style.transform = $('micRing').style.transform = $('mouth').style.transform = '';
  }
}
// The whole corona layer grows with the level too (compositor only, no repaint): with the longer bars,
// the tips go from r ≈ 117 px when quiet to ≈ 190 px when loud. Core, mouth, mic ring and the wave follow.
function drawLive(v, p) {
  $('corona').style.transform = `scale(${(1 + v * .16).toFixed(3)})`;
  $('core').style.transform = `scale(${(1 + v * .13).toFixed(3)})`;
  $('mouth').style.transform = `scaleY(${(.55 + v * 1.6).toFixed(3)})`;
  if (level.state === 'listening') $('micRing').style.transform = `scale(${(1 + v * .16).toFixed(3)})`;   // invisible otherwise
  const pending = $('pending');
  if (pending) pending.querySelectorAll('.wave b').forEach((b, i) => { b.style.transform = `scaleY(${(.18 + v * (.5 + .5 * Math.sin(p * 3 + i * 1.3))).toFixed(3)})`; });
}

/* ---- status: orb, title, hint, buttons ---- */
function modelName() {
  const cur = ui.models.current;
  return cur ? cur.label : '模型';
}
const shownState = () => (ui.ptt ? 'listening' : ui.status.state);

function renderStatus() {
  const { state, connected } = ui.status;
  const shown = shownState();
  document.body.dataset.state = shown;
  // 睡眠时段里没在对话：形象去睡觉（设计画布第 29 块）
  document.body.classList.toggle('sleeping', !!(ui.status.sleep && ui.status.sleep.active) && shown === 'idle');
  if (typeof showAvPose === 'function') showAvPose();
  // 叫醒时家居页、设备详情自动收起，屏幕让给对话（上游 client issue #1 网友的建议）
  if (shown === 'listening' && window.miAutoClose) window.miAutoClose();
  const text = {
    idle: [`说<em>「${wakeName()}」</em>`, connected ? '对话还在，唤醒后可以接着聊' : '或者点麦克风；按住可以直接说'],
    connecting: ['正在连接…', '连上小麦网关后就开始听'],
    listening: ui.ptt ? ['松开<em>发送</em>', '说完松开手指'] : ['我在听', '说完停一下，我就开始回答'],
    thinking: [`<em>${esc(modelName())}</em> 正在思考`, '复杂的问题可能要想一会儿'],
    speaking: ['正在回答', `喊「${(ui.wake && ui.wake.primary) || '小麦小麦'}」或点打断就能停下`],
  }[shown] || ['', ''];
  if (ui.status.recognising && !ui.ptt) text.splice(0, 2, '正在识别', '你说完了，正在把话转成文字');
  const micOff = ui.status.mic === false;
  document.body.classList.toggle('mic-off', micOff);
  const reports = ui.status.reports || 0;
  const sleeping = !!(ui.status.sleep && ui.status.sleep.active);
  document.body.classList.toggle('has-reports', reports > 0);
  document.body.classList.toggle('sleeping', sleeping);
  $('reportText').textContent = reports > 1 ? `有 ${reports} 条待汇报` : '有一条待汇报';
  if (micOff && shown === 'idle') text.splice(0, 2, '麦克风已关', '拨开机身侧面的开关就能说话');
  else if (reports && shown === 'idle') text[1] = `有待汇报：点右上角收听，或叫一声「${(ui.wake && ui.wake.primary) || '小麦小麦'}」`;
  else if (sleeping && shown === 'idle' && !connected) text[1] = '睡眠时段：汇报不会自动念';
  $('title').innerHTML = text[0];
  $('hint').textContent = text[1];
  // recognising, thinking or speaking: the turn can be interrupted (the question is dropped if nothing was said yet)
  $('btnAbort').disabled = !connected || !(state === 'speaking' || state === 'thinking' || (ui.status.recognising && !ui.ptt));
  $('btnEnd').disabled = !connected;
  const link = $('link');
  link.classList.toggle('on', connected);
  link.querySelector('span').textContent = connected ? '对话中' : '待机';
  renderSide();
  renderPending();
  setCorona(shown);
  renderSettings();
  renderBright();
  renderScreenOff();
  renderVol();
  renderUpdate();
}

/* ---- models ---- */
const logoSrc = m => (m && m.logo ? `/static/logos/${m.logo}.svg` : '');

function renderModels() {
  const cur = ui.models.current;
  $('pillLogo').src = logoSrc(cur);
  $('pillLabel').textContent = cur ? cur.label : '选择模型';
  $('pillModel').textContent = cur ? (cur.model || cur.company || '') : '';
  const grid = $('grid');
  grid.textContent = '';
  for (const m of ui.models.options) {
    const on = cur && cur.entry === m.entry;
    const b = document.createElement('button');
    b.className = 'card' + (on ? ' on' : '');
    b.innerHTML = `<img class="tile" src="${esc(logoSrc(m))}" alt="">` + (on ? '<span class="badge">使用中</span>' : '') +
      `<span><b>${esc(m.label)}</b><small>${esc(m.model || '')}</small><small class="co">${esc(m.company || '')}</small></span>`;
    b.addEventListener('click', () => switchModel(m));
    grid.appendChild(b);
  }
  renderHead();
}

function openSheet(on) {
  $('sheet').classList.toggle('on', on);
}

// The page switches at once and the hub catches up; if it fails, back to what the client last said.
// While a switch is on its way, the client's events about the old model don't flip the page back.
const server = { models: null, thread: null };
const seenThreads = {};   // entry -> the client's messages for that model, shown at once when switching to it
let switching = null;

async function switchModel(m) {
  openSheet(false);
  if (ui.models.current && ui.models.current.entry === m.entry) return;
  if (DEMO) return demo.switchTo(m);
  switching = m.entry;
  ui.models = Object.assign({}, ui.models, { current: m });
  ui.thread = { entry: m.entry, messages: seenThreads[m.entry] || [] };
  renderModels(); renderThread(); renderStatus();
  let error = null;
  try {
    const r = await (await fetch('/api/model', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                 body: JSON.stringify({ kind: m.kind, vendor: m.vendor, entry: m.entry }) })).json();
    if (r.ok === false) error = r.error || '切换失败';
  } catch (e) {
    error = '切换失败：客户端没有响应';
  }
  if (switching !== m.entry) return;   // another switch since
  switching = null;
  if (server.models) ui.models = server.models;
  if (server.thread) ui.thread = server.thread;
  renderModels(); renderThread(); renderStatus();
  toast(error || `已切换到 ${m.label}`, error ? 'error' : '');
}

/* ---- settings: sleep hours ---- */
const sleepEdit = {};   // changes sent but not yet confirmed by a state event
let sleepTimer = null;
function sleepSetting() {
  return Object.assign({ enabled: false, start: '22:00', end: '07:00', active: false }, ui.status.sleep || {}, sleepEdit);
}
function renderSettings() {
  const s = sleepSetting();
  $('sleepSwitch').setAttribute('aria-checked', String(s.enabled));
  $('sleepTimes').classList.toggle('off', !s.enabled);
  $('sleepStart').textContent = s.start;
  $('sleepEnd').textContent = s.end;
  $('sleepSliders').classList.toggle('off', !s.enabled);
  for (const [id, k, d] of [['sleepBright', 'brightness', 10], ['sleepVol', 'volume', 30]]) {
    const v = s[k] === undefined ? d : s[k];
    if (document.activeElement !== $(id)) $(id).value = v;
    $(id + 'V').textContent = v;
  }
  $('sleepNote').textContent = !s.enabled ? '睡眠时段已关闭：补报一到就念，音量和亮度照常'
    : s.active ? `现在是睡眠时段，到 ${s.end} 结束` : `每天 ${s.start} 到 ${s.end} 是睡眠时段，现在不在`;
}
function sendSleep(changes, delay) {
  Object.assign(sleepEdit, changes);
  renderSettings();
  clearTimeout(sleepTimer);
  sleepTimer = setTimeout(async () => {
    const body = Object.assign({}, sleepEdit);
    if (DEMO) { ui.status.sleep = Object.assign(sleepSetting(), body); for (const k in body) delete sleepEdit[k]; return renderStatus(); }
    try {
      const r = await (await fetch('/api/sleep', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                   body: JSON.stringify(body) })).json();
      if (r.ok === false) toast(r.error || '没保存上', 'error');
    } catch (e) {
      toast('没保存上：客户端没有响应', 'error');
    }
    for (const k in body) if (sleepEdit[k] === body[k]) delete sleepEdit[k];
    renderSettings();
  }, delay);
}
function stepTime(hhmm, minutes) {
  const [h, m] = hhmm.split(':').map(Number);
  const t = ((h * 60 + m + minutes) % 1440 + 1440) % 1440;
  return pad(Math.floor(t / 60)) + ':' + pad(t % 60);
}
$('sleepSwitch').addEventListener('click', () => sendSleep({ enabled: !sleepSetting().enabled }, 0));
$('sleepBright').addEventListener('input', e => sendSleep({ brightness: +e.target.value }, 500));
$('sleepVol').addEventListener('input', e => sendSleep({ volume: +e.target.value }, 500));
for (const b of document.querySelectorAll('.stepper button')) {
  b.addEventListener('click', () => sendSleep({ [b.dataset.k]: stepTime(sleepSetting()[b.dataset.k], Number(b.dataset.d)) }, 600));
}
/* ---- settings: screen brightness (the client hints the system's screen blanker) ---- */
let brightEdit = null;   // the slider's value while the finger is on it or the change is on its way
let brightTimer = null, brightSent = 0, brightDrag = false;
function brightSetting() {
  return Object.assign({ level: 100, dimmed: false }, ui.status.brightness || {},
                       brightEdit === null ? {} : { level: brightEdit, dimmed: false });
}
function renderBright() {
  const b = brightSetting();
  if (brightEdit === null) $('brightRange').value = b.level;
  $('brightRange').style.setProperty('--fill', b.level + '%');
  $('brightNum').textContent = b.level + '%';
  $('brightNote').textContent = b.dimmed ? '睡眠时段内屏幕调暗了；拖一下滑条，这次时段就按你调的亮度'
    : `熄屏再亮也保持这个亮度；也可以说「${(ui.wake && ui.wake.primary) || '小麦小麦'}，屏幕亮一点」「调到最亮」`;
}
async function sendBright(body) {
  if (DEMO) { ui.status.brightness = { level: body.level, dimmed: false }; return; }
  try {
    const r = await (await fetch('/api/brightness', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                       body: JSON.stringify(body) })).json();
    if (r.ok === false) toast(r.error || '没保存上', 'error');
  } catch (e) {
    toast('没保存上：客户端没有响应', 'error');
  }
}
// The slider follows the finger itself (pointer events, like the mic button) instead of relying on
// the browser's own range dragging under touch. While dragging: at most one change every 150 ms
// (the blanker looks every 200 ms); the last one on release.
function dragBright(e) {
  const r = $('brightRange').getBoundingClientRect(), thumb = 48;
  brightEdit = Math.round(Math.max(0, Math.min(1, (e.clientX - r.left - thumb / 2) / (r.width - thumb))) * 100);
  $('brightRange').value = brightEdit;
  renderBright();
  clearTimeout(brightTimer);
  const send = () => { brightSent = Date.now(); sendBright({ level: brightEdit }); };
  if (Date.now() - brightSent > 150) send();
  else brightTimer = setTimeout(send, 150);
}
$('brightRange').addEventListener('pointerdown', e => {
  e.preventDefault();
  brightDrag = true;
  try { $('brightRange').setPointerCapture(e.pointerId); } catch (err) { /* keeps following without capture */ }
  dragBright(e);
});
$('brightRange').addEventListener('pointermove', e => {
  if (brightDrag) dragBright(e);
});
function dropBright() {
  if (!brightDrag) return;
  brightDrag = false;
  clearTimeout(brightTimer);
  const level = brightEdit;
  sendBright({ level }).then(() => { if (brightEdit === level) brightEdit = null; renderStatus(); });
}
$('brightRange').addEventListener('pointerup', dropBright);
$('brightRange').addEventListener('pointercancel', dropBright);

/* ---- settings: auto screen-off (the client hints the system's screen blanker) ---- */
const offEdit = {};   // a change on its way
function offSetting() {
  return Object.assign({ enabled: true, minutes: 5 }, ui.status.screen_off || {}, offEdit);
}
function renderScreenOff() {
  const s = offSetting();
  $('offSwitch').setAttribute('aria-checked', String(s.enabled));
  $('offMinutes').classList.toggle('off', !s.enabled);
  for (const b of $('offMinutes').children) b.classList.toggle('on', Number(b.dataset.m) === s.minutes);
  $('offNote').textContent = s.enabled ? `${s.minutes} 分钟没碰屏幕、也没在对话，就熄屏` : '屏幕一直亮着，不会自动熄屏';
}
async function sendScreenOff(changes) {
  Object.assign(offEdit, changes);
  renderScreenOff();
  if (DEMO) { ui.status.screen_off = offSetting(); for (const k in changes) delete offEdit[k]; return renderStatus(); }
  try {
    const r = await (await fetch('/api/screen-off', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                      body: JSON.stringify(changes) })).json();
    if (r.ok === false) toast(r.error || '没保存上', 'error');
  } catch (e) {
    toast('没保存上：客户端没有响应', 'error');
  }
  for (const k in changes) if (offEdit[k] === changes[k]) delete offEdit[k];
  renderScreenOff();
}
$('offSwitch').addEventListener('click', () => sendScreenOff({ enabled: !offSetting().enabled }));
for (const b of $('offMinutes').children) b.addEventListener('click', () => sendScreenOff({ minutes: Number(b.dataset.m) }));

/* ---- settings: volumes (the device's, which the keys and voice change too, and the alarms' own) ---- */
// Same finger-following slider as the brightness: at most one change every 150 ms while dragging, the
// last one on release; what the client says meanwhile doesn't move the thumb under the finger.
function slider(id, numId, get, send) {
  const el = $(id);
  let edit = null, timer = null, sent = 0, drag = false;
  function render() {
    const v = edit === null ? get() : edit;
    if (edit === null && v != null) el.value = v;
    el.style.setProperty('--fill', (v == null ? 0 : v) + '%');
    $(numId).textContent = v == null ? '' : v + '%';
  }
  function move(e) {
    const r = el.getBoundingClientRect(), thumb = 48;
    edit = Math.round(Math.max(0, Math.min(1, (e.clientX - r.left - thumb / 2) / (r.width - thumb))) * 100);
    el.value = edit;
    render();
    clearTimeout(timer);
    const go = () => { sent = Date.now(); send(edit); };
    if (Date.now() - sent > 150) go();
    else timer = setTimeout(go, 150);
  }
  el.addEventListener('pointerdown', e => {
    e.preventDefault();
    drag = true;
    try { el.setPointerCapture(e.pointerId); } catch (err) { /* keeps following without capture */ }
    move(e);
  });
  el.addEventListener('pointermove', e => { if (drag) move(e); });
  function drop() {
    if (!drag) return;
    drag = false;
    clearTimeout(timer);
    const v = edit;
    Promise.resolve(send(v)).then(() => { if (edit === v) edit = null; render(); });
  }
  el.addEventListener('pointerup', drop);
  el.addEventListener('pointercancel', drop);
  return render;
}
async function postJson(path, body) {
  try {
    const r = await (await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                         body: JSON.stringify(body || {}) })).json();
    if (r.ok === false) toast(r.error || '没保存上', 'error');
    return r;
  } catch (e) {
    toast('没保存上：客户端没有响应', 'error');
    return { ok: false };
  }
}
const renderVol = slider('volRange', 'volNum', () => ui.status.volume,
  v => (DEMO ? (ui.status.volume = v) : postJson('/api/volume', { volume: v })));
const renderAlarmVol = slider('alarmVolRange', 'alarmVolNum', () => ui.alarms.volume,
  v => (DEMO ? (ui.alarms.volume = v) : postJson('/api/alarm-volume', { volume: v })));

/* ---- wake words (wake_words.py): hold the button and say a new one; the hub writes it down ---- */
const ww = { holding: false, busy: false, heard: null, picks: [], resetArmed: 0 };
function renderWake() {
  const w = ui.wake;
  for (const el of document.querySelectorAll('.ww-name')) el.textContent = (w && w.primary) || '小麦小麦';
  if (!w) return;
  const one = w.words.length <= 1;
  $('wwCount').textContent = `${w.words.length} 个${w.words.length > 1 ? '，叫哪个都行' : ''}`;
  $('wwList').innerHTML = w.words.map(v => `<div class="al"><div class="al-text"><b>${esc(v.label)}</b><span>${esc(v.pinyin)}</span></div>` +
    (one ? '' : `<button class="al-del" data-ww="${esc(v.label)}">删除</button>`) + '</div>').join('');
  $('wwReset').hidden = !w.custom;
  $('wwReset').classList.toggle('armed', Date.now() - ww.resetArmed < 4000);
  $('wwReset').textContent = $('wwReset').classList.contains('armed') ? '确定恢复默认' : '恢复默认';
  const h = ww.heard;
  $('wwPre').hidden = !h;
  if (h) {
    $('wwPre').classList.toggle('risky', !!(h.error || h.warning));
    $('wwChars').innerHTML = h.chars.map((c, i) => {
      const multi = c.pinyin.length > 1;
      return `<button class="ww-ch${multi ? ' multi' : ''}" data-i="${i}" ${multi ? '' : 'tabindex="-1"'}>` +
        `<b>${esc(c.ch)}</b><span>${esc(c.pinyin[ww.picks[i] || 0] || '?')}</span></button>`;
    }).join('');
    const msg = h.error || h.warning;
    $('wwMsg').className = 'ww-msg' + (msg ? ' warn' : '');
    $('wwMsg').textContent = msg ? '⚠ ' + msg
      : h.chars.some(c => c.pinyin.length > 1) ? '读音不对就点那个字换一个（虚线框的字有几种读法）' : '听到的是这几个字，确认后添加';
    $('wwAdd').hidden = !!h.error;
    $('wwAdd').textContent = h.warning ? '仍然添加' : '添加';
    $('wwAdd').classList.toggle('pri', !h.warning);
    $('wwRetry').classList.toggle('pri', !!(h.error || h.warning));
    $('wwChars').hidden = !h.chars.length;
  }
  const full = w.words.length >= w.max;
  $('wwHold').disabled = full || ww.busy;
  $('wwHold').classList.toggle('on', ww.holding);
  $('wwHoldText').textContent = ww.holding ? '正在听，说完松手' : ww.busy ? '正在识别…'
    : full ? `最多 ${w.max} 个，先删掉一个` : '＋ 按住，说一个新的唤醒词';
}
function wakeMeter(v) {
  const bars = $('wwHold').querySelectorAll('.ww-meter i');
  bars.forEach((b, i) => {
    const shape = [.45, .75, 1, .6, .9, .5, .3][i];
    b.style.transform = `scaleY(${Math.max(.15, Math.min(1, v * shape * (0.8 + Math.random() * 0.4)))})`;
  });
}
async function wakeHoldStart(e) {
  e.preventDefault();
  if (ww.holding || ww.busy || $('wwHold').disabled) return;
  ww.holding = true; ww.heard = null; renderWake();
  if (DEMO !== null) return;
  const r = await postJson('/api/wake-words/record/start');
  if (!r.ok) { ww.holding = false; renderWake(); }
}
async function wakeHoldEnd() {
  if (!ww.holding) return;
  ww.holding = false; ww.busy = true; renderWake();
  if (DEMO !== null) {
    ww.busy = false; ww.picks = [0, 0, 0, 0];
    ww.heard = { label: '小乐同学', error: null, warning: null, chars: [
      { ch: '小', pinyin: ['xiǎo'] }, { ch: '乐', pinyin: ['lè', 'yuè'] }, { ch: '同', pinyin: ['tóng', 'tòng'] }, { ch: '学', pinyin: ['xué'] }] };
    return renderWake();
  }
  const r = await postJson('/api/wake-words/record/stop');
  ww.busy = false;
  if (r.ok) { ww.heard = r.heard; ww.picks = r.heard.chars.map(() => 0); }
  renderWake();
}
$('wwHold').addEventListener('pointerdown', wakeHoldStart);
$('wwHold').addEventListener('pointerup', wakeHoldEnd);
$('wwHold').addEventListener('pointercancel', wakeHoldEnd);
$('wwHold').addEventListener('pointerleave', wakeHoldEnd);
$('wwChars').addEventListener('click', e => {
  const b = e.target.closest('.ww-ch.multi');
  if (!b || !ww.heard) return;
  const i = Number(b.dataset.i);
  ww.picks[i] = ((ww.picks[i] || 0) + 1) % ww.heard.chars[i].pinyin.length;
  renderWake();
});
$('wwRetry').addEventListener('click', () => { ww.heard = null; renderWake(); });
$('wwAdd').addEventListener('click', async () => {
  const h = ww.heard;
  if (!h || h.error) return;
  if (DEMO !== null) { ui.wake.words.push({ label: h.label, pinyin: h.chars.map((c, i) => c.pinyin[ww.picks[i]]).join(' ') }); ww.heard = null; return renderWake(); }
  const r = await postJson('/api/wake-words/add', { label: h.label, picks: ww.picks });
  if (r.ok) { ww.heard = null; ui.wake = r.wake_words; renderWake(); renderStatus(); toast(`已添加「${h.label}」，现在就能叫`); }
});
$('wwList').addEventListener('click', e => {
  const b = e.target.closest('.al-del');
  if (!b) return;
  if (!b.classList.contains('armed')) {
    b.classList.add('armed');
    b.textContent = '确认删除';
    setTimeout(() => { b.classList.remove('armed'); b.textContent = '删除'; }, 4000);
    return;
  }
  if (DEMO !== null) { ui.wake.words = ui.wake.words.filter(v => v.label !== b.dataset.ww); return renderWake(); }
  postJson('/api/wake-words/remove', { label: b.dataset.ww }).then(r => { if (r.ok) toast('已删除'); });
});
$('wwReset').addEventListener('click', () => {
  if (Date.now() - ww.resetArmed >= 4000) {
    ww.resetArmed = Date.now(); renderWake();
    setTimeout(renderWake, 4100);
    return;
  }
  ww.resetArmed = 0;
  postJson('/api/wake-words/reset').then(r => { if (r.ok) toast('已恢复默认的唤醒词'); });
});

/* ---- 家人声音 (设计画布第 13 块): name, then three sentences ---- */
const vc = { holding: false, busy: false, step: null, name: '', child: false, n: 0, msg: '', armed: null };
const vcPost = (act, body) => DEMO === null ? postJson('/api/voices/' + act, body || {}) : Promise.resolve({ ok: true });
function renderVoices() {
  const v = ui.voices || { people: [], sentences: [], available: true };
  $('voicesCount').textContent = v.people.length ? `${v.people.length} 个人` : '';
  $('voicesList').innerHTML = v.people.map(p => `<div class="al"><div class="al-text"><b>${esc(p.name)}${p.child ? '（孩子）' : ''}</b>` +
    `<span>录了 ${p.samples} 段</span></div><button class="al-del ${vc.armed === p.name ? 'armed' : ''}" data-vdel="${esc(p.name)}">${vc.armed === p.name ? '确认删除' : '删除'}</button></div>`).join('');
  const on = !!vc.step;
  $('voicesPre').hidden = !on;
  $('voicesDots').innerHTML = on && vc.step !== 'name' ? [0, 1, 2].map(i => `<i class="${i < vc.n ? 'on' : ''}"></i>`).join('') : '';
  $('voicesChild').hidden = vc.step !== 'named';
  for (const b of $('voicesChild').children) b.classList.toggle('on', (b.dataset.c === '1') === vc.child);
  $('voicesNext').hidden = vc.step !== 'named';
  $('voicesMsg').className = 'ww-msg' + (vc.msg.startsWith('⚠') ? ' warn' : '');
  $('voicesMsg').textContent = vc.msg;
  $('voicesSay').textContent = vc.step === 'sample' ? `「${v.sentences[vc.n] || ''}」` : vc.step === 'named' ? vc.name : '';
  $('voicesHold').hidden = vc.step === 'named';
  $('voicesHold').disabled = vc.busy || !v.available;
  $('voicesHold').classList.toggle('on', vc.holding);
  $('voicesHoldText').textContent = !v.available ? '网关上还没有声纹模型' : vc.holding ? '正在听，说完松手' : vc.busy ? '正在处理…'
    : vc.step === 'sample' ? `按住念第 ${vc.n + 1} 句，念完松手` : '＋ 加一个人：按住说名字';
}
$('voicesHold').addEventListener('pointerdown', async e => {
  e.preventDefault();
  if (vc.holding || vc.busy) return;
  vc.holding = true; if (!vc.step) { vc.step = 'name'; vc.msg = '说这个人的称呼，比如「妈妈」「宝宝」'; } renderVoices();
  const r = await vcPost('start');
  if (!r.ok) { vc.holding = false; vc.msg = '⚠ ' + (r.error || '没开始录'); renderVoices(); }
});
async function voicesHoldEnd() {
  if (!vc.holding) return;
  vc.holding = false; vc.busy = true; renderVoices();
  if (vc.step === 'name') {
    const r = await vcPost('name');
    vc.busy = false;
    if (r.ok && r.name) { vc.name = r.name; vc.step = 'named'; vc.msg = '听到的名字是：'; }
    else vc.msg = '⚠ ' + ((r && (r.error)) || '没听清');
  } else {
    const r = await vcPost('sample', { name: vc.name, child: vc.child });
    vc.busy = false;
    if (r.ok && !r.error) { vc.n++; vc.msg = vc.n >= 3 ? '' : `第 ${vc.n} 句录好了`; if (vc.n >= 3) { toast(`录好了，以后能认出${vc.name}`); vc.step = null; vc.n = 0; } }
    else vc.msg = '⚠ ' + ((r && r.error) || '没录上');
  }
  renderVoices();
}
$('voicesHold').addEventListener('pointerup', voicesHoldEnd);
$('voicesHold').addEventListener('pointercancel', voicesHoldEnd);
$('voicesHold').addEventListener('pointerleave', voicesHoldEnd);
$('voicesChild').addEventListener('click', e => { const b = e.target.closest('[data-c]'); if (b) { vc.child = b.dataset.c === '1'; renderVoices(); } });
$('voicesNext').addEventListener('click', () => { vc.step = 'sample'; vc.n = 0; vc.msg = `正常说话的距离，念下面这句：`; renderVoices(); });
$('voicesCancel').addEventListener('click', () => { vc.step = null; vc.n = 0; vc.msg = ''; renderVoices(); });
$('voicesList').addEventListener('click', e => {
  const b = e.target.closest('[data-vdel]');
  if (!b) return;
  if (vc.armed !== b.dataset.vdel) { vc.armed = b.dataset.vdel; renderVoices(); setTimeout(() => { vc.armed = null; renderVoices(); }, 3000); return; }
  vc.armed = null;
  vcPost('remove', { name: b.dataset.vdel }).then(r => { if (r.ok) { ui.voices = r.voices; renderVoices(); } });
});
// the settings sheet asks the hub who is enrolled when it opens
$('btnSettings').addEventListener('click', () => vcPost('list').then(r => { if (r.ok && r.voices) { ui.voices = r.voices; renderVoices(); } }));

/* ---- 定时任务、主动告警、翻译模式 (设计画布第 12 块) ---- */
const proPost = (kind, body) => DEMO === null ? postJson('/api/proactive/' + kind, body).then(r => { if (!r.ok && r.error) toast(r.error, 'error'); return r; }) : Promise.resolve({});
const DAYS_TEXT = { daily: '每天', workdays: '工作日', weekend: '周末', once: '只一次' };
let taskArmed = null;
function renderTasks() {
  const t = ui.tasks;
  if (!t) return;
  $('tasksCount').textContent = t.tasks.length ? `${t.tasks.length} 个` : '';
  $('tasksList').innerHTML = t.tasks.length ? t.tasks.map(x => `<div class="al"><div class="al-text"><b>${DAYS_TEXT[x.days] || ''} ${esc(x.time)}</b><span>${esc(x.prompt)}</span></div>` +
    `<button class="al-del ${taskArmed === x.id ? 'armed' : ''}" data-task="${esc(x.id)}">${taskArmed === x.id ? '确认删除' : '删除'}</button></div>`).join('')
    : '<div class="alarm-empty">还没有定时任务</div>';
}
$('tasksList').addEventListener('click', e => {
  const b = e.target.closest('[data-task]');
  if (!b) return;
  if (taskArmed !== b.dataset.task) { taskArmed = b.dataset.task; renderTasks(); setTimeout(() => { taskArmed = null; renderTasks(); }, 3000); return; }
  taskArmed = null; proPost('tasks', { delete: b.dataset.task });
});
function banners() {
  document.body.classList.toggle('has-banner', !$('alertBar').hidden || !$('trBar').hidden);
  document.body.classList.toggle('both-bars', !$('alertBar').hidden && !$('trBar').hidden);   // 两条都在：翻译条排在告警条下面
}
function renderAlertsUi() {
  const a = ui.alerts;
  if (!a) return;
  $('alertLeak').setAttribute('aria-checked', String(!!a.leak));
  $('alertDoor').setAttribute('aria-checked', String(!!a.door_night));
  $('alertsNow').textContent = a.sensors ? `米家 ${a.sensors} 个传感器` : '';
  $('alertBar').hidden = !a.alert;
  if (a.alert) { $('alertText').textContent = a.alert.text; $('alertTime').textContent = hhmm(a.alert.time); }
  banners();
}
$('alertLeak').addEventListener('click', () => proPost('alerts', { leak: !(ui.alerts && ui.alerts.leak) }));
$('alertDoor').addEventListener('click', () => proPost('alerts', { door_night: !(ui.alerts && ui.alerts.door_night) }));
$('alertOk').addEventListener('click', () => proPost('alerts', { clear: true }));
function renderTranslate() {
  const t = ui.translate;
  $('trBar').hidden = !(t && t.enabled);
  if (t && t.enabled) $('trText').textContent = `翻译模式 · ${t.a} ⇄ ${t.b}`;
  banners();
}
$('trExit').addEventListener('click', () => proPost('translate', { enabled: false }));

/* ---- 电台和助眠声、场景、留言、清单 (media.py, daily.py; 设计画布第 11 块) ---- */
let mediaMinutes = 30, nowTimer = null;
const dailyPost = (kind, body) => DEMO === null ? postJson('/api/daily/' + kind, body).then(r => { if (!r.ok && r.error) toast(r.error, 'error'); return r; }) : Promise.resolve({});
function renderMedia() {
  const m = ui.media, now = m && m.playing;
  $('nowBar').hidden = !now;
  clearInterval(nowTimer);
  if (now) {
    $('nowIcon').textContent = now.kind === 'radio' ? '📻' : '🌙';
    $('nowName').textContent = now.name;
    const tick = () => {
      const left = now.ends ? Math.max(0, Math.round((now.ends - Date.now() / 1000) / 60)) : null;
      $('nowSub').textContent = now.kind === 'radio' ? '正在播放' : left !== null ? `还剩 ${left} 分钟` : '一直放';
    };
    tick(); nowTimer = setInterval(tick, 30000);
  }
  if (!m) return;
  $('mediaNow').textContent = now ? `正在放：${now.name}` : '';
  $('mediaSounds').innerHTML = m.sounds.map(x => `<button data-sound="${x.id}" class="${now && now.id === x.id ? 'on' : ''}">${esc(x.name)}</button>`).join('');
  $('mediaStations').innerHTML = m.stations.map(x => `<button data-station="${x.id}" class="${now && now.id === x.id ? 'on' : ''}">${esc(x.name)}</button>`).join('');
  for (const b of $('mediaMinutes').children) b.classList.toggle('on', Number(b.dataset.min) === mediaMinutes);
}
$('nowStop').addEventListener('click', () => dailyPost('media', { stop: true }));
$('mediaMinutes').addEventListener('click', e => { const b = e.target.closest('[data-min]'); if (b) { mediaMinutes = Number(b.dataset.min); renderMedia(); } });
$('mediaSounds').addEventListener('click', e => {
  const b = e.target.closest('[data-sound]');
  if (!b) return;
  const now = ui.media && ui.media.playing;
  dailyPost('media', now && now.id === b.dataset.sound ? { stop: true } : { kind: 'sound', name: b.dataset.sound, minutes: mediaMinutes });
});
$('mediaStations').addEventListener('click', e => {
  const b = e.target.closest('[data-station]');
  if (!b) return;
  const now = ui.media && ui.media.playing;
  dailyPost('media', now && now.id === b.dataset.station ? { stop: true } : { kind: 'radio', name: b.dataset.station });
});
let sceneArmed = null;
function renderScenes() {
  const x = ui.scenes;
  if (!x) return;
  $('scenesCount').textContent = x.scenes.length ? `${x.scenes.length} 个` : '';
  $('scenesList').innerHTML = x.scenes.length ? x.scenes.map(sc => `<div class="al"><div class="al-text"><b>${esc(sc.name)}</b><span>${esc(sc.steps)}</span></div>` +
    `<button class="al-del ${sceneArmed === sc.name ? 'armed' : ''}" data-scene="${esc(sc.name)}">${sceneArmed === sc.name ? '确认删除' : '删除'}</button></div>`).join('')
    : '<div class="alarm-empty">还没有场景</div>';
}
$('scenesList').addEventListener('click', e => {
  const b = e.target.closest('[data-scene]');
  if (!b) return;
  if (sceneArmed !== b.dataset.scene) { sceneArmed = b.dataset.scene; renderScenes(); setTimeout(() => { sceneArmed = null; renderScenes(); }, 3000); return; }
  sceneArmed = null;
  dailyPost('scenes', { delete: b.dataset.scene });
});
function renderNotes() {
  const notes = (ui.notes && ui.notes.notes) || [];
  $('homeNotes').innerHTML = notes.slice(0, 2).map(n => `<div class="sticky" data-read="${esc(n.id)}" role="button"><div class="st-text">` +
    `<b>📝 给${esc(n.to)}的留言</b><p>${esc(n.text)}</p><small>${n.from ? esc(n.from) + ' · ' : ''}${dayText(n.time)} ${hhmm(n.time)} · 点一下念出来</small></div>` +
    `<button class="st-ok" data-done="${esc(n.id)}">知道了</button></div>`).join('');
}
$('homeNotes').addEventListener('click', e => {
  const d = e.target.closest('[data-done]');
  if (d) { e.stopPropagation(); return dailyPost('notes', { done: d.dataset.done }); }
  const r = e.target.closest('[data-read]');
  if (r) dailyPost('notes', { read: r.dataset.read });
});
function renderLists() {
  const l = ui.lists;
  if (!l) { $('homeList').hidden = true; return; }
  const names = Object.keys(l.lists);
  const todo = n => l.lists[n].filter(x => !x.done);
  const shop = names.find(n => n === '购物清单') || names[0];
  const items = shop ? todo(shop) : [];
  $('homeList').hidden = !items.length;
  if (items.length) $('homeList').innerHTML = `🛒 ${esc(shop)} <span>${esc(items.slice(0, 4).map(x => x.text).join('、'))}${items.length > 4 ? '…' : ''} · ${items.length} 项</span>`;
  $('listsCount').textContent = `${names.length} 个清单`;
  $('listsList').innerHTML = names.map(n => `<div class="al"><div class="al-text"><b>${n === '购物清单' ? '🛒' : '✅'} ${esc(n)}</b>` +
    `<span>${esc(todo(n).map(x => x.text).join('、') || '空的')}</span></div></div>`).join('');
}
function showQr(kicker, title, note, url) {
  $('qrKicker').textContent = kicker; $('qrTitle').innerHTML = title; $('qrNote').textContent = note;
  if (window.drawQr) window.drawQr($('qrCanvas'), url);
  $('qrOv').hidden = false;
}
$('qrClose').addEventListener('click', () => { $('qrOv').hidden = true; });
$('listsPhone').addEventListener('click', () => { if (ui.lists && ui.lists.url) showQr('清单', '用手机扫码<br>看清单', '在超市边买边划掉，小麦这边也会同步。手机要连家里的 Wi‑Fi。', ui.lists.url); });
$('homeList').addEventListener('click', () => { if (ui.lists && ui.lists.url) showQr('清单', '用手机扫码<br>看清单', '在超市边买边划掉，小麦这边也会同步。手机要连家里的 Wi‑Fi。', ui.lists.url); });

/* ---- 做菜模式 (cook.py) ---- */
$('messages').addEventListener('click', e => {
  const b = e.target.closest('[data-cook]');
  if (!b || DEMO !== null) return;
  postJson('/api/cook/start', { card: b.dataset.cook }).then(r => { if (!r.ok) toast(r.error || '没开始', 'error'); });
});
function renderCook() {
  const c = ui.cook;
  $('cookOv').hidden = !(c && c.on);
  if (!c || !c.on) return;
  $('cookTitle').textContent = c.title;
  $('cookCount').textContent = `第 ${c.i + 1} / ${c.steps.length} 步`;
  $('cookDots').innerHTML = c.steps.map((_, i) => `<i class="${i <= c.i ? 'on' : ''}"></i>`).join('');
  $('cookStep').textContent = `第 ${c.i + 1} 步`;
  $('cookText').textContent = c.steps[c.i];
  $('cookPrev').disabled = c.i === 0;
  $('cookNext').textContent = c.i === c.steps.length - 1 ? '做好了' : '下一步';
  $('cookTimer').hidden = !c.minutes;
  if (c.minutes) $('cookTimer').textContent = `⏱ 定 ${c.minutes} 分钟`;
}
const cookPost = (path, body) => DEMO === null && postJson(path, body || {}).then(r => { if (!r.ok && r.error) toast(r.error, 'error'); });
$('cookPrev').addEventListener('click', () => cookPost('/api/cook/go', { by: -1 }));
$('cookNext').addEventListener('click', () => ui.cook && ui.cook.i === ui.cook.steps.length - 1 ? cookPost('/api/cook/stop') : cookPost('/api/cook/go', { by: 1 }));
$('cookExit').addEventListener('click', () => cookPost('/api/cook/stop'));
$('cookTimer').addEventListener('click', () => cookPost('/api/cook/timer'));

/* ---- 待机页 and the family album ---- */
const sb = { last: Date.now(), shown: false, idx: 0, layer: 0, armed: null, timer: null };
function renderStandbyCard() {
  const st = ui.standby;
  if (!st) return;
  $('sbCard').classList.toggle('off', !st.enabled);
  $('sbSwitch').setAttribute('aria-checked', String(!!st.enabled));
  $('sbAlbum').setAttribute('aria-checked', String(!!st.album));
  $('sbNow').textContent = st.enabled ? `没人说话 ${st.minutes} 分钟后` : '';
  $('sbMinutes').innerHTML = st.choices.map(m => `<button data-m="${m}" class="${m === st.minutes ? 'on' : ''}">${m} 分钟</button>`).join('');
  $('sbAlbumSub').textContent = st.photos.length ? `${st.photos.length} 张照片，每 15 秒换一张` : '还没有照片，用手机传几张';
  $('sbThumbs').innerHTML = st.photos.map(u => `<button data-del="${esc(u.split('/').pop())}" class="${sb.armed === u.split('/').pop() ? 'armed' : ''}"><img src="${esc(u)}" alt="" loading="lazy"></button>`).join('');
  $('sbAdd').hidden = st.photos.length >= st.max;
}
async function setStandby(body) {
  if (DEMO !== null) return;
  const r = await postJson('/api/standby', body);
  if (r.ok) { ui.standby = r.standby; renderStandbyCard(); } else if (r.error) toast(r.error, 'error');
}
$('sbSwitch').addEventListener('click', () => setStandby({ enabled: !(ui.standby && ui.standby.enabled) }));
$('sbAlbum').addEventListener('click', () => setStandby({ album: !(ui.standby && ui.standby.album) }));
$('sbMinutes').addEventListener('click', e => { const b = e.target.closest('[data-m]'); if (b) setStandby({ minutes: Number(b.dataset.m) }); });
$('sbThumbs').addEventListener('click', e => {
  const b = e.target.closest('[data-del]');
  if (!b) return;
  if (sb.armed !== b.dataset.del) { sb.armed = b.dataset.del; renderStandbyCard(); setTimeout(() => { sb.armed = null; renderStandbyCard(); }, 3000); return; }
  sb.armed = null; setStandby({ delete: b.dataset.del });
});
$('sbAdd').addEventListener('click', () => DEMO === null && postJson('/api/album/start').then(r => { if (!r.ok) toast(r.error || '没开成', 'error'); }));
function standbyBusy() {
  const st = ui.status || {};
  // 设备详情、确认框不是 .sheet：也算在用，待机页不盖上来
  return st.state !== 'idle' || document.querySelector('.sheet.on, .h-detail.on, .h-confirm.on') || ui.photo || (ui.cook && ui.cook.on) || !$('qrOv').hidden
    || (ui.alarms && ui.alarms.ringing) || ww.holding || bc.holding || (typeof slides !== 'undefined' && slides.show);
}
function showStandby(on) {
  if (on === sb.shown) return;
  sb.shown = on;
  $('standby').hidden = !on;
  clearInterval(sb.timer);
  if (on) { renderStandby(); sb.timer = setInterval(renderStandby, 15000); }
}
function renderStandby() {
  const now = new Date(), st = ui.standby || {}, photos = (st.album && !(ui.status.sleep || {}).active) ? st.photos || [] : [];
  $('sbTime').textContent = pad(now.getHours()) + ':' + pad(now.getMinutes());
  $('sbDate').textContent = $('bigDate').textContent;
  const w = ui.today && ui.today.weather, n = v => (v === null || v === undefined ? '?' : Math.round(v));
  $('sbWx').innerHTML = w ? `<span style="font-size:64px">${esc(w.icon || '')}</span><b>${n(w.temp)}°</b><span>${esc(w.text)} · ${n((w.today || {}).min)}~${n((w.today || {}).max)}°</span>` : '';
  const next = ui.alarms && ui.alarms.items.find(v => v.kind !== 'timer');
  $('sbAlarm').hidden = !next;
  if (next) $('sbAlarm').textContent = `⏰ ${dayText(next.at)} ${hhmm(next.at)} ${next.label || ''}`;
  $('standby').classList.toggle('plain', !photos.length);
  const imgs = [$('sbPhotoA'), $('sbPhotoB')];
  if (photos.length) {
    sb.idx = (sb.idx + 1) % photos.length;
    const nextImg = imgs[sb.layer ^ 1];
    nextImg.onload = () => { nextImg.classList.add('on'); imgs[sb.layer].classList.remove('on'); sb.layer ^= 1; };
    nextImg.src = photos[sb.idx];
  } else imgs.forEach(i => i.classList.remove('on'));
}
// any touch: back to the home page (the touch that ends the standby page does nothing else)
document.addEventListener('pointerdown', e => {
  sb.last = Date.now();
  if (sb.shown) { e.preventDefault(); e.stopPropagation(); showStandby(false); sb.eatClick = Date.now(); }
}, true);
document.addEventListener('click', e => {
  if (sb.eatClick && Date.now() - sb.eatClick < 800) { e.preventDefault(); e.stopPropagation(); sb.eatClick = 0; }
}, true);
setInterval(() => {
  const st = ui.standby;
  if (!st || !st.enabled || standbyBusy()) { sb.last = Math.max(sb.last, standbyBusy() ? Date.now() : 0); if (sb.shown) showStandby(false); return; }
  if (!sb.shown && Date.now() - sb.last > st.minutes * 60000) showStandby(true);
}, 5000);

/* ---- 儿童模式 (kids.py, 设计画布第 10 块) ---- */
function renderKids() {
  const k = ui.kids;
  if (!k) return;
  $('kidsCard').classList.toggle('off', !k.enabled);
  $('kidsSwitch').setAttribute('aria-checked', String(!!k.enabled));
  $('kidsNow').textContent = k.enabled ? `${k.age} 岁 · 今天聊了 ${k.used_minutes} 分钟${k.blocked === 'limit' ? '（到上限了）' : k.blocked === 'bedtime' ? '（睡觉时间）' : ''}` : '';
  $('kidsAge').textContent = `${k.age} 岁`;
  $('kidsMin').textContent = k.daily_minutes ? `${k.daily_minutes} 分` : '不限';
  $('kidsBed').textContent = k.bedtime || '不限';
  for (const b of $('kidsVoice').children) b.classList.toggle('on', b.dataset.v === k.voice);
}
async function setKids(body) {
  if (DEMO !== null) return;
  const r = await postJson('/api/kids', body);
  if (r.ok) { ui.kids = r.kids; renderKids(); } else if (r.error) toast(r.error, 'error');
}
$('kidsSwitch').addEventListener('click', () => setKids({ enabled: !(ui.kids && ui.kids.enabled) }));
$('kidsVoice').addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (b) setKids({ voice: b.dataset.v }); });
$('kidsCard').addEventListener('click', e => {
  const b = e.target.closest('.bc-step [data-k]');
  if (!b || !ui.kids) return;
  const d = Number(b.dataset.d);
  if (b.dataset.k === 'age') setKids({ age: Math.max(1, Math.min(14, ui.kids.age + d)) });
  else if (b.dataset.k === 'min') setKids({ minutes_step: d });
  else setKids({ bedtime_shift: d });
});

/* ---- 外观: theme, avatar, home blocks (looks.py, 设计画布第 8 块) ---- */
const AVATAR_DOT = {
  orb: 'radial-gradient(circle at 34% 28%,#f2f4ff,#b4bde8 20%,#5b65a8 58%,#191d45)',
  cat: 'radial-gradient(circle at 34% 28%,#fff1df,#f6b16b 28%,#e0782f 72%,#8a3d10)',
  dog: 'radial-gradient(circle at 34% 28%,#fff4e6,#e9c79c 30%,#c48f5a 74%,#6b4224)',
  bear: 'radial-gradient(circle at 34% 28%,#ecd2b6,#c08a5e 30%,#8a5a37 72%,#4a2c18)',
  rabbit: 'radial-gradient(circle at 34% 28%,#fff,#fff0f5 34%,#f2c6d6 78%,#b97f97)',
  panda: 'radial-gradient(circle at 34% 28%,#fff,#f4f4f7 40%,#cfd0d8 82%,#8c8e9c)',
  wheat: 'radial-gradient(circle at 34% 28%,#fffbe6,#f5d37a 30%,#d9a22e 72%,#7d5410)',
  robot: 'linear-gradient(160deg,#f2f5fb,#c5cde0 45%,#7d88a6)',
};
const THEME_DOT = { night: 'linear-gradient(135deg,#18245a,#050813)', dawn: 'linear-gradient(135deg,#ffe3c4,#f3ecff)',
  forest: 'linear-gradient(135deg,#1d4a3a,#06100c)', candy: 'linear-gradient(135deg,#ffd6ec,#c9ecff)', black: '#000',
  auto: 'linear-gradient(90deg,#ffe3c4 50%,#18245a 50%)' };
const BLOCK_ELS = { clock: ['greet', 'bigTime'], date: ['bigDate'], weather: ['homeWeather'], alarms: ['nextAlarms', 'missedBox', 'homeNotes', 'homeList'],
  suggest: ['tryLabel', 'chips'], devices: ['homeDevices'] };
let looksArmed = null;
// 照片形象的几个样子（设计画布第 26 块）：待机、在听、在想、在说；没传的状态用待机那张
let avPoses = null;
const POSE_OF = { idle: 0, connecting: 0, listening: 1, thinking: 2, speaking: 3 };
function showAvPose() {
  if (!avPoses) return;
  const pose = document.body.classList.contains('sleeping') ? 4 : POSE_OF[document.body.dataset.state] || 0;
  const url = avPoses[pose] || avPoses[0];
  document.body.classList.toggle('av-has-sleep', !!avPoses[4]);
  if ($('avPhoto').getAttribute('src') !== url) $('avPhoto').src = url;
}
function renderLooks() {
  const l = ui.looks;
  if (!l) return;
  document.body.dataset.theme = l.theme_now;
  const photo = l.avatar.startsWith('photo:') && l.photos.find(p => p.id === l.avatar);
  document.body.dataset.avatar = photo ? 'photo' : l.avatar;
  document.body.classList.toggle('av-photo-on', !!photo);
  document.body.classList.toggle('av-cutout', !!(photo && photo.cutout));   // 去了白底的公仔：全身显示
  avPoses = photo ? (photo.poses && photo.poses.length ? photo.poses : [photo.url]) : null;
  showAvPose();
  // home blocks: CSS order inside the welcome column; missed alarms always show with the alarms
  l.blocks.forEach((b, i) => (BLOCK_ELS[b.id] || []).forEach((id, k) => {
    const el = $(id);
    if (!el) return;
    el.style.order = String(i * 10 + k);
    el.classList.toggle('hidden-block', !b.on && id !== 'missedBox' && id !== 'homeNotes');   // missed alarms and notes always show
  }));
  $('looksNow').textContent = `${l.themes[l.theme]} · ${(l.avatars.concat(l.photos).find(a => a.id === l.avatar) || {}).name || ''}`;
  $('looksThemes').innerHTML = Object.entries(l.themes).map(([k, name]) =>
    `<button data-theme="${k}" class="card2 ${k === l.theme ? 'on' : ''}" style="background:var(--card2)"><i style="background:${THEME_DOT[k]}"></i>${esc(name)}</button>`).join('');
  $('looksAvatars').innerHTML = l.avatars.map(a =>
    `<button data-avatar="${a.id}" class="${a.id === l.avatar ? 'on' : ''}"><span class="dot" style="background:${AVATAR_DOT[a.id]}"></span>${esc(a.name)}</button>`).join('') +
    l.photos.map(p => `<button data-avatar="${p.id}" class="${p.id === l.avatar ? 'on' : ''}"><img src="${esc(p.url)}" alt="">` +
      `${looksArmed === p.id ? '确定删除？' : '照片形象'}<span class="x" data-del="${p.id}">×</span></button>`).join('') +
    `<button class="add" id="looksAdd"><span style="font-size:34px">＋</span>用照片生成</button>`;
  $('looksBlocks').innerHTML = l.blocks.map((b, i) =>
    `<div class="al ${b.on ? '' : 'off'}"><div class="al-text"><b>${esc(b.name)}</b></div>` +
    `<div class="bc-step"><button data-move="${b.id}" data-by="-1" ${i ? '' : 'disabled'} aria-label="上移">↑</button>` +
    `<button data-move="${b.id}" data-by="1" ${i < l.blocks.length - 1 ? '' : 'disabled'} aria-label="下移">↓</button></div>` +
    `<button class="switch" data-toggle="${b.id}" role="switch" aria-checked="${b.on}" aria-label="${esc(b.name)}"><i></i></button></div>`).join('');
}
async function setLooks(body) {
  if (DEMO !== null) return;
  const r = await postJson('/api/looks', body);
  if (r.ok) { ui.looks = r.looks; renderLooks(); } else if (r.error) toast(r.error, 'error');
}
$('looksThemes').addEventListener('click', e => { const b = e.target.closest('[data-theme]'); if (b) setLooks({ theme: b.dataset.theme }); });
$('looksAvatars').addEventListener('click', e => {
  const del = e.target.closest('[data-del]');
  if (del) {
    e.stopPropagation();
    if (looksArmed !== del.dataset.del) { looksArmed = del.dataset.del; renderLooks(); setTimeout(() => { looksArmed = null; renderLooks(); }, 3000); return; }
    looksArmed = null;
    return setLooks({ delete: del.dataset.del });
  }
  if (e.target.closest('#looksAdd')) return startAvatarPhoto();
  const b = e.target.closest('[data-avatar]');
  if (b) setLooks({ avatar: b.dataset.avatar });
});
$('looksBlocks').addEventListener('click', e => {
  const m = e.target.closest('[data-move]');
  if (m) return setLooks({ move: { id: m.dataset.move, by: Number(m.dataset.by) } });
  const t = e.target.closest('[data-toggle]');
  if (t) setLooks({ toggle: t.dataset.toggle });
});
function renderToday() {
  const w = ui.today && ui.today.weather;
  $('homeWeather').hidden = !w;
  if (w) {
    const n = v => (v === null || v === undefined ? '?' : Math.round(v));
    const t = w.today || {};
    $('homeWeather').innerHTML = `<span class="hw-icon">${esc(w.icon || '')}</span><span class="hw-temp">${n(w.temp)}°</span>` +
      `<div class="hw-text"><b>${esc(w.text)}${w.feels !== null && w.feels !== undefined ? ` · 体感 ${n(w.feels)}°` : ''}</b>` +
      `今天 ${n(t.min)}~${n(t.max)}°${t.rain !== null && t.rain !== undefined ? ` · 降水 ${n(t.rain)}%` : ''}${w.air ? ` · 空气${esc(w.air)}` : ''}</div>`;
  }
  tick();
}
async function startAvatarPhoto() {
  if (DEMO !== null) return;
  const r = await postJson('/api/avatar/start');
  if (!r.ok) toast(r.error || '没开成', 'error');
}

/* ---- 拍照问答: QR code for the phone, then its photo while the vision model looks (设计画布第 7 块) ---- */
let photoQrShown = null, photoTimer = null;
// 机身摄像头：有摄像头的拍照问先显示实时画面，3 秒后倒数 3、2、1 自动拍（设计画布第 27 块）
const cam = { on: false, timer: null, token: null };
function camStop() {
  clearTimeout(cam.timer); cam.timer = null; cam.on = false;
  $('camOv').hidden = true; $('camCount').textContent = '';
  $('camView').removeAttribute('src');   // 断开实时画面：没人看了客户端就关摄像头
}
function camCountdown(n) {
  $('camCount').textContent = n || '';
  if (n > 0) { cam.timer = setTimeout(() => camCountdown(n - 1), 1000); return; }
  $('camFlash').classList.add('on'); requestAnimationFrame(() => requestAnimationFrame(() => $('camFlash').classList.remove('on')));
  postJson('/api/camera/shoot').then(r => { if (r && !r.ok && r.error) { toast(r.error, 'error'); camArm(); } });
}
function camArm() { clearTimeout(cam.timer); $('camCount').textContent = ''; cam.timer = setTimeout(() => camCountdown(3), 3000); }
function renderCamera(p) {
  const want = !!(p && p.camera && p.state === 'waiting' && p.purpose === 'ask');
  if (!want) { if (cam.on) camStop(); return false; }
  $('camOv').classList.toggle('shooting', !!p.shooting);
  if (cam.on && cam.token === p.token) return true;
  cam.on = true; cam.token = p.token;
  $('camQ').textContent = `「${p.question}」`;
  $('camOv').hidden = false;
  $('camView').src = '/api/camera/preview?t=' + Date.now();
  camArm();
  return true;
}
$('camShoot').addEventListener('click', () => { clearTimeout(cam.timer); camCountdown(0); });
$('camPhone').addEventListener('click', () => { camStop(); postJson('/api/camera/phone'); });
$('camCancel').addEventListener('click', () => { camStop(); postJson('/api/photo/cancel'); });

function renderPhoto() {
  const p = ui.photo;
  const onCamera = renderCamera(p);
  $('photoOv').hidden = !p || onCamera;
  clearInterval(photoTimer);
  if (!p) return;
  const failed = ['error', 'expired', 'cancelled'].includes(p.state);
  const avatar = p.purpose === 'avatar';
  $('photoOv').classList.toggle('avatar', avatar);
  $('photoOv').classList.toggle('looking', p.state === 'received' && !avatar || p.state === 'generating');
  $('photoOv').classList.toggle('failed', failed || !!(avatar && p.error));
  if (photoQrShown !== p.url && window.drawQr) { window.drawQr($('photoQr'), p.url); photoQrShown = p.url; }
  const shown = avatar && p.state === 'generated' && p.preview ? p.preview : p.image;
  $('photoQr').hidden = !!shown;
  $('photoImg').hidden = !shown;
  if (shown && $('photoImg').getAttribute('src') !== shown) $('photoImg').src = shown;
  $('photoQ').hidden = avatar;
  $('photoQ').textContent = `「${p.question}」`;
  const pick = avatar && ['received', 'generated'].includes(p.state);
  $('photoStyles').hidden = !pick;
  if (pick) $('photoStyles').innerHTML = (p.styles || []).map(st =>
    `<button data-style="${st.id}" class="${st.id === p.style ? 'on' : ''}">${esc(st.name)}</button>`).join('');
  $('photoUse').hidden = !(avatar && p.state === 'generated' && p.preview);
  if (p.purpose === 'album') {
    $('photoQ').hidden = true;
    $('photoTitle').innerHTML = failed ? '没传成' : '用手机选几张<br>家里的照片';
    $('photoNote').textContent = failed ? (p.error || '出错了') : p.received ? `已经收到 ${p.received} 张，传完点「完成」` : '可以多选，一次最多 30 张；手机和小麦连同一个 Wi‑Fi';
    $('photoCancel').textContent = p.received ? '完成' : '取消';
    $('photoQr').hidden = false; $('photoImg').hidden = true;
    return;
  }
  if (avatar) {
    $('photoTitle').innerHTML = failed ? '没成功' : p.state === 'waiting' ? '拍一张正脸照<br>或者你的公仔' : p.state === 'received' ? '选一个画风'
      : p.state === 'generating' ? '正在画…' : '画好了';
    $('photoNote').textContent = failed || p.error ? (p.error || '出错了') : p.state === 'waiting' ? '脸要清楚、光线亮一点；手机和小麦连同一个 Wi‑Fi'
      : p.state === 'generating' ? '大约 15 秒' : p.state === 'generated' ? '满意就用它，不满意换个画风' : '通义万相会把照片画成卡通形象';
  } else {
    $('photoTitle').innerHTML = failed ? '没看成' : p.state === 'waiting' ? '用手机扫码，<br>拍一张照片' : '收到了，<br>正在看…';
    $('photoNote').textContent = failed ? (p.error || '出错了') : p.state === 'waiting' ? '手机和小麦连同一个 Wi‑Fi 才能打开' : '一般 3～8 秒';
  }
  $('photoCancel').textContent = failed ? '关闭' : '取消';
  const tick = () => {
    const s = Math.max(0, Math.round(p.expires - Date.now() / 1000));
    $('photoTime').textContent = p.state === 'waiting' ? `${Math.floor(s / 60)}:${pad(s % 60)} 后失效` : '';
  };
  tick();
  if (p.state === 'waiting') photoTimer = setInterval(tick, 1000);
}
$('photoCancel').addEventListener('click', () => { ui.photo = null; renderPhoto(); if (DEMO === null) postJson('/api/photo/cancel'); });
$('photoStyles').addEventListener('click', async e => {
  const b = e.target.closest('[data-style]');
  if (!b || DEMO !== null) return;
  const r = await postJson('/api/avatar/style', { style: Number(b.dataset.style) });
  if (!r.ok) toast(r.error || '没开始画', 'error');
});
$('photoUse').addEventListener('click', async () => {
  if (DEMO !== null) return;
  const r = await postJson('/api/avatar/use');
  if (r.ok) toast('换上新形象了'); else toast(r.error || '没换成', 'error');
});
$('btnPhoto').addEventListener('click', async () => {
  if (DEMO !== null) {
    ui.photo = { token: 'demo', url: 'http://10.10.10.224:16688/photo/demo', question: '这是什么', expires: Date.now() / 1000 + 300, state: 'waiting' };
    return renderPhoto();
  }
  const r = await postJson('/api/photo', { question: '这是什么' });
  if (!r.ok) toast(r.error || '没开成', 'error');
  else if (!r.vision_ready) toast('网关还没配置看图模型：在网关后台「大模型 → 看图模型」填 Key', 'error');
});

/* ---- weather broadcasts and the home address (broadcast.py, 设计画布第 6 块) ---- */
const bc = { holding: false, busy: false, found: null, armed: null };
function holdMeter(btn, v) {
  btn.querySelectorAll('.ww-meter i').forEach((b, i) => {
    const shape = [.45, .75, 1, .6, .9, .5, .3][i];
    b.style.transform = `scaleY(${Math.max(.15, Math.min(1, v * shape * (0.8 + Math.random() * 0.4)))})`;
  });
}
function renderBroadcast() {
  const b = ui.broadcast;
  if (!b) return;
  $('bcCard').classList.toggle('off', !b.enabled);
  $('bcSwitch').setAttribute('aria-checked', String(!!b.enabled));
  $('bcCount').textContent = b.enabled ? (b.times.length ? `${{ daily: '每天', workdays: '工作日', weekend: '周末' }[b.days]} ${b.times.length} 次` : '还没有时间') : '';
  $('bcTimes').innerHTML = b.times.map(t => {
    const armed = bc.armed === t;
    return `<div class="al"><div class="al-text"><b>${t}</b><span>${b.about[t] === 'tomorrow' ? '明天的天气' : '今天的天气'}</span></div>` +
      `<div class="bc-step"><button data-t="${t}" data-m="-${b.step}" aria-label="提前">−</button><button data-t="${t}" data-m="${b.step}" aria-label="推后">+</button></div>` +
      `<button class="al-del${armed ? ' armed' : ''}" data-del="${t}">${armed ? '确认删除' : '删除'}</button></div>`;
  }).join('');
  $('bcAdd').hidden = b.times.length >= b.max_times;
  for (const el of $('bcDays').children) el.classList.toggle('on', el.dataset.d === b.days);
  for (const el of $('bcExtras').querySelectorAll('[data-x]')) el.classList.toggle('on', !!b[el.dataset.x]);
  const f = bc.found;
  $('bcHome').classList.toggle('warn', !!(f && f.error));
  $('bcConfirm').hidden = !(f && f.place);
  $('bcHold').hidden = !!(f && f.place);
  if (f && f.place) {
    $('bcHomeLabel').textContent = `听到「${f.heard}」，找到的是`;
    $('bcHomeName').textContent = f.place.name;
    $('bcHomeSub').textContent = coords(f.place);
  } else {
    $('bcHomeLabel').textContent = '住宅地址（音箱所在地）';
    $('bcHomeName').textContent = b.home ? b.home.name : '还没设';
    $('bcHomeSub').textContent = f && f.error ? '⚠ ' + f.error : b.home ? coords(b.home) : '没设时按网关后台「查天气」里的设备所在地';
  }
  $('bcHold').disabled = bc.busy;
  $('bcHold').classList.toggle('on', bc.holding);
  $('bcHoldText').textContent = bc.holding ? '正在听，说完松手' : bc.busy ? '正在查找…' : f && f.error ? '按住重说' : '按住说新地址';
}
function coords(p) {
  const lat = Number(p.latitude), lon = Number(p.longitude);
  return `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? 'N' : 'S'} ${Math.abs(lon).toFixed(2)}°${lon >= 0 ? 'E' : 'W'}`;
}
async function setBroadcast(body) {
  if (DEMO !== null) return;
  const r = await postJson('/api/broadcast', body);
  if (r.ok) { ui.broadcast = r.broadcast; renderBroadcast(); } else if (r.error) toast(r.error, 'error');
}
$('bcSwitch').addEventListener('click', () => setBroadcast({ enabled: !(ui.broadcast && ui.broadcast.enabled) }));
$('bcDays').addEventListener('click', e => { const b = e.target.closest('[data-d]'); if (b) setBroadcast({ days: b.dataset.d }); });
$('bcExtras').addEventListener('click', e => {
  const b = e.target.closest('[data-x]');
  if (b) setBroadcast({ [b.dataset.x]: !ui.broadcast[b.dataset.x] });
});
$('bcAdd').addEventListener('click', () => {
  // the next free time an hour after the last one (07:30 first)
  const times = ui.broadcast.times;
  let t = times.length ? times[times.length - 1] : '06:30';
  for (let i = 0; i < 24 && (i === 0 || times.includes(t)); i++) {
    const [h, m] = t.split(':').map(Number);
    t = pad((h + 1) % 24) + ':' + pad(m);
  }
  setBroadcast({ add_time: t });
});
$('bcTimes').addEventListener('click', e => {
  const step = e.target.closest('[data-m]');
  if (step) return setBroadcast({ shift: { time: step.dataset.t, minutes: Number(step.dataset.m) } });
  const del = e.target.closest('[data-del]');
  if (!del) return;
  if (bc.armed !== del.dataset.del) {
    bc.armed = del.dataset.del; renderBroadcast();
    setTimeout(() => { if (bc.armed === del.dataset.del) { bc.armed = null; renderBroadcast(); } }, 3000);
    return;
  }
  bc.armed = null;
  setBroadcast({ remove_time: del.dataset.del });
});
async function homeHoldStart(e) {
  e.preventDefault();
  if (bc.holding || bc.busy) return;
  bc.holding = true; bc.found = null; renderBroadcast();
  if (DEMO !== null) return;
  const r = await postJson('/api/home/record/start');
  if (!r.ok) { bc.holding = false; renderBroadcast(); if (r.error) toast(r.error, 'error'); }
}
async function homeHoldEnd() {
  if (!bc.holding) return;
  bc.holding = false; bc.busy = true; renderBroadcast();
  if (DEMO !== null) {
    bc.busy = false; bc.found = { heard: '杭州西湖区', place: { name: '浙江省杭州市西湖区', latitude: 30.26, longitude: 120.13 } };
    return renderBroadcast();
  }
  const r = await postJson('/api/home/record/stop');
  bc.busy = false;
  bc.found = r.ok ? r : { heard: '', error: r.error };
  renderBroadcast();
}
$('bcHold').addEventListener('pointerdown', homeHoldStart);
$('bcHold').addEventListener('pointerup', homeHoldEnd);
$('bcHold').addEventListener('pointercancel', homeHoldEnd);
$('bcHold').addEventListener('pointerleave', homeHoldEnd);
$('bcRetry').addEventListener('click', () => { bc.found = null; renderBroadcast(); });
$('bcUse').addEventListener('click', async () => {
  const f = bc.found;
  if (!f || !f.place) return;
  if (DEMO !== null) { ui.broadcast.home = f.place; bc.found = null; return renderBroadcast(); }
  const r = await postJson('/api/home', { place: f.place });
  if (r.ok) { bc.found = null; ui.broadcast = r.broadcast; renderBroadcast(); toast(`住宅地址改成了${f.place.name}`); }
  else toast(r.error || '没改成', 'error');
});
$('bcTest').addEventListener('click', () => {
  if (DEMO !== null) return;
  postJson('/api/broadcast', { test: true });
  toast('马上播一次');
});

/* ---- countdowns, alarms and reminders (alarms.py: the client keeps and rings them) ---- */
const KIND_ICON = { timer: '计时', alarm: '闹钟', reminder: '提醒', broadcast: '播报' };
const now_s = () => Date.now() / 1000;
function hhmm(t) {
  const d = new Date(t * 1000);
  return pad(d.getHours()) + ':' + pad(d.getMinutes());
}
function dayText(t) {
  const d = new Date(t * 1000), today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()) - today) / 86400000);
  return { '-1': '昨天', 0: '今天', 1: '明天', 2: '后天' }[days] || `${d.getMonth() + 1}月${d.getDate()}日 星期${WEEK[d.getDay()]}`;
}
function left(t) {
  const s = Math.max(0, Math.round(t - now_s()));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return (h ? h + ':' + pad(m) : m) + ':' + pad(s % 60);
}
function alarmRow(v) {
  if (v.kind === 'timer') {
    return [`<span data-left="${v.at}">${left(v.at)}</span>${v.label ? ' ' + esc(v.label) : ''}`,
            `倒计时，${hhmm(v.at)} 响${v.snoozed ? ' · 再睡中' : ''}`];
  }
  const sub = [dayText(v.at)];
  if (v.repeat) sub.push(esc(v.repeat));
  if (v.snoozed) sub.push('再睡中');
  if (v.kind === 'reminder') sub.push(v.spoken ? '到点念出来' : v.spoken === false ? '只响铃（没合成出语音）' : '正在合成语音');
  return [`${hhmm(v.at)}${v.label ? ' ' + esc(v.label) : ''}`, sub.join(' · ')];
}
function renderAlarms() {
  const a = ui.alarms;
  document.body.classList.toggle('alarms-paused', !a.synced);
  document.body.classList.toggle('ringing', !!a.ringing);
  document.body.classList.toggle('has-timer', a.items.some(v => v.kind === 'timer'));
  // home: the running countdowns and the next alarm or reminder
  const timers = a.items.filter(v => v.kind === 'timer').slice(0, 3);
  const next = a.items.find(v => v.kind !== 'timer');
  const chips = timers.map(v => `<div class="na timer">倒计时 <b data-left="${v.at}">${left(v.at)}</b>${v.label ? esc(v.label) : ''}</div>`);
  if (next) chips.push(`<div class="na">${KIND_ICON[next.kind]} <b>${dayText(next.at)} ${hhmm(next.at)}</b>${esc(next.label || '')}</div>`);
  if (!a.synced && a.items.length) chips.unshift('<div class="na paused">时间未同步，闹钟暂停</div>');
  $('nextAlarms').innerHTML = chips.join('');
  // settings: the list, the tones, the missed ones
  $('alarmCount').textContent = a.items.length ? `${a.items.length} 条` : '';
  $('alarmList').innerHTML = a.items.length ? a.items.map(v => {
    const [main, sub] = alarmRow(v);
    return `<div class="al ${v.kind}"><i>${KIND_ICON[v.kind]}</i><div class="al-text"><b>${main}</b><span>${sub}</span></div>` +
      `<button class="al-del" data-id="${v.id}">删除</button></div>`;
  }).join('') : '<div class="alarm-empty">还没有闹钟和提醒</div>';
  $('tones').innerHTML = Object.entries(a.tones || {}).map(([k, name]) =>
    `<button data-tone="${esc(k)}" class="${k === a.tone ? 'on' : ''}">${esc(name)}</button>`).join('');
  // home: the missed ones (newest first, at most 3 shown) until 知道了
  $('missedBox').hidden = !a.missed.length;
  $('missedList').innerHTML = a.missed.slice(0, 3).map(m =>
    `<div class="mh"><b>${dayText(m.at)} ${hhmm(m.at)}</b>${KIND_ICON[m.kind] || ''}${m.label ? ' · ' + esc(m.label) : ''}` +
    `<small>${m.why === 'off' ? '设备关着' : m.why === 'busy' ? '当时一直在忙，没有播' : m.kind === 'reminder' ? '提醒了 3 分钟没人停' : '响了 10 分钟没人停'}</small></div>`).join('') +
    (a.missed.length > 3 ? `<div class="mh more">还有 ${a.missed.length - 3} 条</div>` : '');
  renderAlarmVol();
  renderRing();
  tickAlarms();
}
function renderRing() {
  const r = ui.alarms.ringing;
  if (!r) return;
  const items = r.items || [];
  $('ringKind').textContent = [...new Set(items.map(v => v.type))].join('、') || '闹钟';
  $('ringLabel').textContent = items.map(v => v.label).filter(Boolean).join('、');
  $('ringNote').textContent = (r.notes || []).join('；');
}
function tickAlarms() {
  for (const el of document.querySelectorAll('[data-left]')) el.textContent = left(Number(el.dataset.left));
  const timer = ui.alarms.items.find(v => v.kind === 'timer');
  $('timerText').textContent = timer ? left(timer.at) : '';
}
$('alarmList').addEventListener('click', e => {
  const b = e.target.closest('.al-del');
  if (!b) return;
  if (!b.classList.contains('armed')) {
    b.classList.add('armed');
    b.textContent = '确认删除';
    setTimeout(() => { b.classList.remove('armed'); b.textContent = '删除'; }, 4000);
    return;
  }
  if (DEMO) { ui.alarms.items = ui.alarms.items.filter(v => v.id !== Number(b.dataset.id)); return renderAlarms(); }
  postJson('/api/alarms/delete', { id: Number(b.dataset.id) }).then(r => { if (r.ok) toast('已删除'); });
});
$('tones').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  if (DEMO) { ui.alarms.tone = b.dataset.tone; return renderAlarms(); }
  postJson('/api/alarm-tone', { tone: b.dataset.tone }).then(() => postJson('/api/alarm-tone', { tone: b.dataset.tone, preview: true }));
});
$('missedClear').addEventListener('click', () => (DEMO ? handlers.alarms(Object.assign({}, ui.alarms, { missed: [] }))
                                                       : postJson('/api/alarms/missed/clear')));
$('ringStop').addEventListener('click', () => post('/api/alarms/stop'));
$('ringSnooze').addEventListener('click', () => {
  if (DEMO) return post('/api/alarms/snooze');
  postJson('/api/alarms/snooze').then(r => { if (r.ok) toast('好的，10 分钟后再叫你'); });   // postJson shows the error
});

/* ---- settings: software update (update.py: the root updater installs, the page only asks) ---- */
const UPD_STAGE = { preparing: '正在准备', downloading: '正在下载', installing: '正在安装',
                    testing: '正在重启并自检', rolling_back: '自检没通过，正在退回' };
let updArmed = 0;   // first tap on 更新 arms it, a second one within 5 s installs
let updStarting = 0;   // request sent, the updater hasn't reported yet (it takes a few seconds to start)
const mb = n => (n / 1048576).toFixed(n < 10485760 ? 1 : 0) + ' MB';
function ago(t) {
  const m = Math.max(0, Math.round((Date.now() / 1000 - t) / 60));
  return m < 2 ? '刚刚' : m < 60 ? `${m} 分钟前` : m < 1440 ? `${Math.round(m / 60)} 小时前` : `${Math.round(m / 1440)} 天前`;
}
// After an update: the state it left, both versions, whatever this run installed. One tap installs
// the system layer and then the client, and an older updater keeps only the last one's message
// (已更新到 0.1.5); the versions themselves are always right.
function updDoneText(u, job) {
  const sys = (u || {}).system || {};
  if (!sys.current || !ui.version) return job.message || '更新完成';
  return `更新完成：系统层 ${sys.current} · 客户端 ${ui.version}`
    + (/重启设备后生效/.test(job.message || '') ? '，部分改动重启设备后生效' : '');
}
function updParts(u) {
  // what one tap installs, in the updater's order: the system layer (shell, audio...) first
  const parts = [], sys = u.system || {}, sa = sys.available;
  if (sa && !(sa.missing || []).length) parts.push({ key: 'system', name: '系统层', ...sa });
  if (u.available) parts.push({ key: 'client', name: '客户端', ...u.available });
  return parts;
}
function renderUpdate() {
  const u = ui.update || {}, job = u.job || {}, sys = u.system || {};
  const parts = updParts(u), avail = parts.length > 0;
  const busy = job.state in UPD_STAGE;
  // between the confirming tap and the updater's first report: don't show the 更新 button again
  // (it looks like the tap missed). Ends when the updater reports (busy, or a result newer than the
  // tap) or after 30 s of silence.
  if (updStarting && (busy || (job.time || 0) * 1000 > updStarting - 2000 || Date.now() - updStarting > 30000)) updStarting = 0;
  const starting = !!updStarting;
  const recent = !!job.time && Date.now() / 1000 - job.time < 86400;
  const failed = !busy && recent && (job.state === 'failed' || job.state === 'rolled_back');
  const cur = !ui.version ? '' : sys.current ? `客户端 ${ui.version} · 系统层 ${sys.current}` : `当前版本 ${ui.version}`;
  // a system layer this image can't take (it needs packages only a newer image has)
  const blocked = sys.available && (sys.available.missing || []).length
    ? `系统层 ${sys.available.version} 要重刷新镜像才能装` : '';
  let title = '软件更新', text, bad = false, label = '', notes = '';
  if (!u.enabled) text = (cur ? cur + '。' : '') + '这台设备还没装更新服务';
  else if (starting) {
    title = '正在开始更新…';
    text = '正在连接更新服务器，请稍等';
  } else if (busy) {
    const what = job.component === 'system' ? '系统层' : '客户端';
    title = `正在更新${what} ${job.version}`;
    const pct = job.state === 'downloading' && job.progress != null ? ` ${Math.round(job.progress * 100)}%` : '';
    text = `${UPD_STAGE[job.state]}${pct}。` + (job.component === 'system' ? '屏幕可能会闪一下，' : '')
      + '语音助手会暂停一会儿，请不要断电';
  } else if (avail) {
    title = '有新版本 ' + parts.map(p => `${p.name} ${p.version}`).join(' · ');
    text = failed ? job.message + '。可以再试一次'
      : `${cur}，下载约 ${mb(parts.reduce((n, p) => n + (p.size || 0), 0))}` + (blocked ? `。${blocked}` : '');
    bad = failed;
    if (Date.now() - updArmed < 5000) {
      // waiting for the confirming tap: the hint stays on the card for those 5 s
      bad = false;
      text = `更新时语音助手会停一会儿（约 ${parts.length > 1 ? 2 : 1} 分钟）`
        + (parts.some(p => p.key === 'system') ? '，屏幕可能会闪一下' : '') + '。再点一下「确认更新」开始';
    }
    notes = parts.map(p => (parts.length > 1 ? `<li class="h">${p.name} ${esc(p.version)}</li>` : '')
                          + (p.notes || []).map(n => `<li>${esc(n)}</li>`).join('')).join('');
    label = Date.now() - updArmed < 5000 ? '确认更新' : '更新';
  } else {
    text = recent && job.state === 'done' ? `${updDoneText(u, job)}，已是最新`
      : failed ? job.message
      : u.check_error ? `${cur}。${u.check_error}`
      : blocked ? `${cur}。${blocked}`
      : `${cur}，已是最新` + (u.checked ? `（${ago(u.checked)}检查过）` : '');
    bad = failed || (!!u.check_error && !(recent && job.state === 'done'));
    label = '检查更新';
  }
  $('gearDot').classList.toggle('on', avail && !busy && !starting);
  $('updTitle').textContent = title;
  $('updSub').textContent = text;
  $('updSub').classList.toggle('bad', !!bad);   // toggle(x, undefined) would flip it
  $('updNotes').innerHTML = notes;
  const btn = $('updBtn');
  btn.hidden = !label;
  btn.textContent = label;
  btn.disabled = avail && ui.status.state !== 'idle';   // not in the middle of a conversation
  $('upd').classList.toggle('new', avail && !busy && !starting);
  $('updBar').hidden = !busy && !starting;
  $('updBar').classList.toggle('busy', starting || (busy && job.state !== 'downloading'));
  $('updFill').style.transform = job.state === 'downloading' ? `scaleX(${job.progress || 0})` : '';
}
function updateFinished(job) {
  // once per install, also when the page reloaded because the client restarted with the new version
  if (!job || !(job.state === 'done' || job.state === 'rolled_back' || job.state === 'failed')) return;
  if (Date.now() / 1000 - job.time > 300) return;
  try { if (localStorage.getItem('updShown') === String(job.time)) return; localStorage.setItem('updShown', String(job.time)); } catch (e) { /* no storage */ }
  toast(job.state === 'done' ? updDoneText(ui.update, job) : job.message, job.state === 'done' ? '' : 'error');
}
$('updBtn').addEventListener('click', async () => {
  const parts = updParts(ui.update || {});
  if (!parts.length) {
    if (DEMO) return toast('正在检查更新…');
    const r = await (await post('/api/update/check'))?.json?.();
    return toast(r && r.ok ? '正在检查更新…' : '这台设备还没装更新服务', r && r.ok ? '' : 'error');
  }
  const sys = parts.find(p => p.key === 'system'), cli = parts.find(p => p.key === 'client');
  if (Date.now() - updArmed > 5000) {
    updArmed = Date.now();
    renderUpdate();
    return setTimeout(renderUpdate, 5100);
  }
  updArmed = 0;
  updStarting = Date.now();
  renderUpdate();
  setTimeout(renderUpdate, 30100);
  if (DEMO) return;   // ?demo: shows the waiting state (and the button again after 30 s)
  const r = await (await post('/api/update/install', { version: cli ? cli.version : '', system: sys ? sys.version : '' }))?.json?.();
  if (!r || !r.ok) {
    updStarting = 0;
    renderUpdate();
    toast((r && r.error) || '没能开始更新', 'error');
  }
});
if (DEMO !== null) {
  // ?demo&upd=new|sys|both|busy|failed: preview the update card (opens the settings sheet)
  const kind = new URLSearchParams(location.search).get('upd');
  const avail = { version: '0.2.0', size: 9.4 * 1048576, notes: ['聊天页的光球加了表情', '修好了睡眠时段跨午夜时不生效'] };
  const savail = { version: '0.3.0', size: 0.2 * 1048576, notes: ['设置页能切换 WiFi', '可以当蓝牙音箱'], missing: [] };
  const now = Date.now() / 1000;
  ui.version = '0.1.0';
  ui.update = { enabled: true, checked: now - 3600, available: kind && kind !== 'sys' ? avail : null,
                system: { current: '0.2.0', available: kind === 'sys' || kind === 'both' ? savail : null },
                job: kind === 'busy' ? { state: 'downloading', version: '0.3.0', progress: .42, time: now, component: 'system' }
                   : kind === 'failed' ? { state: 'rolled_back', version: '0.3.0', time: now, component: 'system',
                                           message: '系统层 0.3.0 没装成功（屏幕界面没有启动起来），已退回 0.2.0' } : null };
  if (kind) $('settings').classList.add('on');
}

/* ---- settings: chat history (kept on the hub; clearing also clears the hub's copy) ---- */
let clearArmed = 0, memArmed = 0;   // first tap arms a button, a second one within 5 s clears
function renderClear() {
  $('clearBtn').textContent = Date.now() - clearArmed < 5000 ? '确认清除' : '清除';
  $('clearBtn').classList.toggle('armed', Date.now() - clearArmed < 5000);
  $('clearMemBtn').textContent = Date.now() - memArmed < 5000 ? '确认只清除记忆' : '只清除记忆、保留记录';
  $('clearMemBtn').classList.toggle('armed', Date.now() - memArmed < 5000);
}
function agentNote(r) {
  // Claude Code and Grok Bot keep their own context: clearing here doesn't touch it
  return (r.agents || []).length ? `${r.agents.join('和')}自己那边的对话没有动` : '';
}
$('clearBtn').addEventListener('click', async () => {
  if (Date.now() - clearArmed > 5000) {
    clearArmed = Date.now();
    renderClear();
    setTimeout(renderClear, 5100);
    return toast('清除后所有模型的聊天记录都会删掉，再点一下「确认清除」');
  }
  clearArmed = 0;
  renderClear();
  if (DEMO !== null) { handlers.thread({ entry: ui.thread.entry, messages: [] }); return toast('聊天记录已清除'); }
  const r = await (await post('/api/history/clear', {}))?.json?.();
  if (!r || !r.ok) return toast((r && r.error) || '没能清除', 'error');
  const note = agentNote(r);
  toast('聊天记录已清除' + (note ? '。' + note : ''));
});
$('clearMemBtn').addEventListener('click', async () => {
  if (Date.now() - memArmed > 5000) {
    memArmed = Date.now();
    renderClear();
    setTimeout(renderClear, 5100);
    return toast('模型会忘掉以前聊过的，聊天记录留着；再点一下「确认只清除记忆」');
  }
  memArmed = 0;
  renderClear();
  if (DEMO !== null) return toast('记忆已清除，聊天记录还在');
  const r = await (await post('/api/history/clear', { memory_only: true }))?.json?.();
  toast(r && r.ok ? '记忆已清除，聊天记录还在' : (r && r.error) || '没能清除', r && r.ok ? '' : 'error');
});
renderClear();

// The device's address, read each time the sheet opens (it can change with the WiFi)
async function renderIp() {
  let ip = DEMO !== null ? '192.168.1.23' : null, admin = null;
  if (DEMO === null) {
    try { ({ ip, admin } = await (await fetch('/api/network')).json()); } catch (e) { ip = null; }
  }
  $('ipNum').textContent = ip || '未联网';
  // 网关后台在网关那台电脑上（地址由网关告诉设备），不是这台设备的 IP
  $('ipSub').textContent = !ip ? '连上 WiFi 后这里显示设备的地址'
    : admin ? `网关后台：在同一网络的电脑上打开 ${admin}` : '网关后台的地址在网关连上后显示';
}
$('btnSettings').addEventListener('click', () => { $('settings').classList.add('on'); renderIp(); renderWifiCard(); });
/* ---- settings: WiFi (the kiosk shell scans and joins: thinksmart-kiosk.py, the same code as its welcome page) ---- */
// The shell answers the client's page only on the device's own screen (127.0.0.1); window.thinksmartShell
// says it can. An older shell can't: the card then only says so.
const shell = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.thinksmart;
const shellWifi = !!(shell && window.thinksmartShell && window.thinksmartShell.wifi);
const shellCalls = {};
let shellSeq = 0;
// The shell answers every command (a connect takes at most ~60 s: three short nmcli calls, a
// watched activation of at most 50 s, the cleanup); an older shell (the 20261101 image) doesn't when
// nmcli hangs. Waiting for ever would keep the Wi-Fi panel busy: after this, it counts as failed.
const SHELL_TIMEOUT = 90000;
function shellCall(cmd, args) {
  return new Promise(resolve => {
    const id = ++shellSeq;
    shellCalls[id] = resolve;
    setTimeout(() => window.thinksmartReply({ id, ok: false, reason: 'noreply' }), SHELL_TIMEOUT);
    const msg = Object.assign({ id, cmd }, args || {});
    if (shellWifi) shell.postMessage(JSON.stringify(msg));
    else wifiMock(msg);
  });
}
window.thinksmartReply = function (r) {
  const done = shellCalls[r.id];
  delete shellCalls[r.id];
  if (done) done(r);
};
// outside the kiosk (a desktop browser, ?demo): pretend to be a device; password "wrongpass" fails
function wifiMock(m) {
  const reply = (r, ms) => setTimeout(() => window.thinksmartReply(Object.assign({ id: m.id }, r)), ms);
  if (DEMO === null) return reply({ ok: false, reason: 'noshell' }, 0);
  if (m.cmd === 'wifi_status') reply({ ok: true, ssid: 'Home_5G', signal: 82 }, 100);
  else if (m.cmd === 'scan') reply({ ok: true, networks: [
    { ssid: 'Home_5G', signal: 82, secure: true, active: true, saved: true },
    { ssid: 'Home', signal: 70, secure: true, saved: true },
    { ssid: '客厅的 WiFi', signal: 54, secure: true },
    { ssid: 'Guest', signal: 40, secure: false },
    { ssid: 'TP-LINK_8A2C', signal: 22, secure: true },
  ] }, 700);
  else if (m.cmd === 'connect') {
    ['scan', 'join', 'auth', 'ip'].forEach((s, i) => setTimeout(() => window.thinksmartProgress({ id: m.id, stage: s }), 300 + i * 400));
    reply(m.password === 'wrongpass' ? { ok: false, reason: 'password' } : { ok: true }, 2000);
  } else reply({ ok: true }, 200);
}

const WIFI_REASONS = {
  password: '密码不对，请再试一次',
  notfound: '找不到这个网络，可能离得太远或名称有误',
  timeout: '连接超时，请靠近路由器后再试',
  noreply: '设备没有回应，请过一会儿再看看网络状态',
};
const WIFI_STAGES = { scan: '正在搜索网络…', join: '正在连接路由器…', auth: '正在验证密码…', ip: '密码正确，正在获取网络地址…' };
const wifi = { nets: [], target: null, ssid: '', pw: '', showPw: false, busy: false, connectId: null };
const signalText = s => s > 75 ? '信号很好' : s > 50 ? '信号良好' : s > 25 ? '信号一般' : '信号较弱';

async function renderWifiCard() {
  if (!shellWifi && DEMO === null) {
    $('wifiSub').textContent = '这台设备的系统层版本较旧，不能在这里切换 WiFi。在上面的「软件更新」里更新系统层后就可以。';
    $('wifiBtn').hidden = true;
    return;
  }
  const r = await shellCall('wifi_status');
  $('wifiSub').textContent = !r.ok ? '读不到 WiFi 状态' : r.ssid ? `已连接 ${r.ssid} · ${signalText(r.signal)}` : '没有连接 WiFi';
}
function openWifi() {
  $('wifi').classList.add('on');
  wifiList();
  wifiScan();
}
function closeWifi() {
  if (wifi.busy) return;   // a connection attempt is on: it rolls back by itself if it fails
  if (document.activeElement) document.activeElement.blur();
  $('wifi').classList.remove('on');
  renderWifiCard();
  renderIp();
}
function wifiPane(id) {
  for (const p of document.querySelectorAll('.w-pane')) p.classList.toggle('on', p.id === id);
}
function wifiList() {
  if (document.activeElement) document.activeElement.blur();
  wifiPane('wList');
  renderNets();
}
async function wifiScan() {
  $('wScan').classList.add('spin');
  if (!wifi.nets.length) $('wNets').innerHTML = '<div class="w-empty">正在搜索附近的 WiFi…</div>';
  const r = await shellCall('scan');
  $('wScan').classList.remove('spin');
  wifi.nets = r.ok ? r.networks : [];
  renderNets(r.ok ? '' : '搜索失败，点右上角刷新再试');
}
function bars(signal) {
  const lit = signal > 75 ? 4 : signal > 50 ? 3 : signal > 25 ? 2 : 1;
  return '<span class="bars">' + [1, 2, 3, 4].map(i => `<i class="${i <= lit ? 'lit' : ''}"></i>`).join('') + '</span>';
}
const LOCK = '<svg class="w-lock" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#b6c0e2" stroke-width="2"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';
function renderNets(error) {
  const box = $('wNets');
  if (error || !wifi.nets.length) {
    box.innerHTML = `<div class="w-empty">${esc(error || '附近没有搜到 WiFi')}</div>`;
    return;
  }
  box.innerHTML = wifi.nets.map((n, i) =>
    `<button class="w-net${n.active ? ' cur' : ''}" data-i="${i}">${bars(n.signal)}<span class="name">${esc(n.ssid)}</span>` +
    (n.active ? '<span class="meta">已连接</span>' : n.saved ? '<span class="meta saved">已保存</span>' : '') +
    (n.secure ? LOCK : '') + '</button>').join('');
}
$('wNets').addEventListener('click', e => {
  const b = e.target.closest('.w-net');
  if (b) wifiJoin(wifi.nets[Number(b.dataset.i)], false);
});

// mode: 'type' (enter a password), 'saved' (a saved network: connect or forget), 'info' (the one
// connected: forget). Saved networks wait for a tap on 连接 so 忘记 stays reachable.
function wifiJoin(net, hidden) {
  wifi.target = { ssid: net ? net.ssid : '', secure: net ? net.secure : true, hidden, saved: !!(net && net.saved),
                  mode: net && net.active ? 'info' : net && net.saved ? 'saved' : 'type' };
  wifi.ssid = wifi.target.ssid;
  wifi.pw = '';
  wifi.showPw = false;
  const t = wifi.target, q = net ? signalText(net.signal) : '';
  $('wJoinTitle').textContent = hidden ? '添加隐藏网络' : t.ssid;
  $('wJoinHint').textContent = hidden ? '输入网络名称和密码（没有密码就留空），然后点键盘上的「连接」'
    : t.mode === 'info' ? '已连接 · ' + q
    : t.mode === 'saved' ? '已保存的网络 · ' + q + '，点「连接」用保存的密码连接'
    : t.secure ? '加密网络 · ' + q + '，输入密码后点键盘上的「连接」' : '开放网络 · ' + q;
  wifiMsg('');
  wifiPane('wJoin');
  renderJoin();
  if (t.mode === 'type' && !t.secure && !hidden) wifiConnect();   // open network: nothing to type
  else wifiFocus();
}
const typing = () => wifi.target.mode === 'type';
// the system keyboard (injected by the kiosk shell) comes up with the focused field
function wifiFocus() {
  if (!typing() || wifi.busy) return;
  const t = wifi.target;
  const el = t.hidden && !wifi.ssid ? $('wSsid') : t.secure ? $('wPw') : $('wSsid');
  setTimeout(() => el.focus(), 50);
}
function canConnect() {
  const t = wifi.target;
  // a hidden network may be open: an empty password is allowed there, 1-7 characters never are
  const pwOk = t.hidden ? (wifi.pw.length === 0 || wifi.pw.length >= 8) : (!t.secure || wifi.pw.length >= 8);
  return !wifi.busy && (!t.hidden || wifi.ssid.length > 0) && pwOk;
}
function renderJoin() {
  const t = wifi.target;
  if ($('wSsid').value !== wifi.ssid) $('wSsid').value = wifi.ssid;
  if ($('wPw').value !== wifi.pw) $('wPw').value = wifi.pw;
  $('wPw').type = wifi.showPw ? 'text' : 'password';
  $('wPw').placeholder = t.hidden ? '没有密码就留空' : '至少 8 位';
  $('wSsid').disabled = $('wPw').disabled = wifi.busy;
  $('wEye').textContent = wifi.showPw ? '隐藏' : '显示';
  $('wFieldSsid').style.display = typing() && t.hidden ? '' : 'none';
  $('wFieldPw').style.display = typing() && t.secure ? '' : 'none';
  $('wForget').hidden = !(t.saved || t.mode === 'info');
  $('wJoinBtn').hidden = t.mode !== 'saved';
  $('wJoinBtn').disabled = $('wForget').disabled = wifi.busy;
}
function wifiMsg(text, kind) {
  $('wMsg').className = 'w-msg' + (kind ? ' ' + kind : '');
  $('wMsg').innerHTML = (kind === 'wait' ? '<span class="w-spin"></span>' : '') + esc(text);
}
async function wifiConnect() {
  const t = wifi.target;
  if (t.mode !== 'saved' && !canConnect()) return;
  wifi.busy = true;
  renderJoin();
  const usingSaved = t.mode === 'saved';
  wifiMsg(usingSaved ? `正在用已保存的密码连接 ${wifi.ssid}…` : `正在连接 ${wifi.ssid}…`, 'wait');
  wifi.connectId = shellSeq + 1;   // the id shellCall() is about to use: progress reports carry it
  const r = await shellCall('connect', { ssid: wifi.ssid, password: usingSaved || !t.secure ? '' : wifi.pw, hidden: !!t.hidden });
  wifi.connectId = null;
  wifi.busy = false;
  if (r.ok) {
    t.mode = 'info';
    t.saved = true;
    wifiMsg(`已连接 ${wifi.ssid}`, 'ok');
    renderJoin();
    setTimeout(() => { if (!wifi.busy && $('wJoin').classList.contains('on')) { wifiList(); wifiScan(); } }, 1500);
    return;
  }
  // whatever went wrong (the shell already went back to the previous network), let the user type a password next
  if (t.secure) t.mode = 'type';
  if (r.reason === 'password') wifi.pw = '';
  wifiMsg(usingSaved && r.reason === 'password' ? '保存的密码已失效，请重新输入'
    : WIFI_REASONS[r.reason] || ('连接失败' + (r.detail ? '：' + r.detail : '')) + '，已连回原来的网络', 'bad');
  renderJoin();
  wifiFocus();
}
window.thinksmartProgress = function (p) {
  if (wifi.busy && p.id === wifi.connectId && WIFI_STAGES[p.stage]) wifiMsg(WIFI_STAGES[p.stage], 'wait');
};
async function wifiForget() {
  if (wifi.busy) return;
  const ssid = wifi.target.ssid;
  wifi.busy = true;
  wifiMsg(`正在忘记 ${ssid}…`, 'wait');
  renderJoin();
  const r = await shellCall('forget', { ssid });
  wifi.busy = false;
  if (!r.ok) {
    wifiMsg(`没能忘记 ${ssid}，请再试一次`, 'bad');
    return renderJoin();
  }
  wifi.target.saved = false;
  wifiMsg(`已忘记 ${ssid}，下次连接需要重新输入密码`, 'ok');
  renderJoin();
  wifi.nets = wifi.nets.map(n => n.ssid === ssid ? Object.assign({}, n, { saved: false }) : n);
  setTimeout(() => { if (!wifi.busy) { wifiList(); wifiScan(); } }, 1400);
}
$('wifiBtn').addEventListener('click', openWifi);
$('wClose').addEventListener('click', closeWifi);
$('wScan').addEventListener('click', wifiScan);
$('wHidden').addEventListener('click', () => wifiJoin(null, true));
$('wBack').addEventListener('click', () => { if (!wifi.busy) wifiList(); });
$('wForget').addEventListener('click', wifiForget);
$('wJoinBtn').addEventListener('click', wifiConnect);
// On pointerdown, not click: cancelling a touch's pointerdown (to keep the field focused and the system
// keyboard up) makes WebKitGTK send no click at all, so a click handler never ran on the device's screen
// (a mouse in a desktop browser still clicks). The on-screen keyboard's keys work the same way.
$('wEye').addEventListener('pointerdown', e => { e.preventDefault(); wifi.showPw = !wifi.showPw; renderJoin(); });
$('wSsid').addEventListener('input', () => { wifi.ssid = $('wSsid').value; });
$('wPw').addEventListener('input', () => { wifi.pw = $('wPw').value; });
$('wSsid').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  e.preventDefault();   // keep the keyboard up: on to the password
  if (wifi.target.secure) $('wPw').focus();
});
$('wPw').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  if (!canConnect()) { e.preventDefault(); return; }   // keep typing
  wifiConnect();
});

/* ---- settings: Bluetooth speaker (bt.py; pairing only in the 3-minute "add a phone" window) ---- */
function btPlaying(d) {
  const song = [d.title, d.artist].filter(Boolean).join(' · ');
  return song || '正在播放';
}
function renderBluetooth() {
  const b = ui.bluetooth;
  const on = !!(b && b.available && b.enabled);
  const live = b && b.enabled && (b.devices || []).find(d => d.streaming);
  document.body.classList.toggle('bt-playing', !!live);
  $('btChipText').textContent = live ? `${live.name} · ${btPlaying(live)}` : '';
  if (!b) return;
  $('btSwitch').setAttribute('aria-checked', String(on));
  $('btSwitch').disabled = !b.available;
  $('btCard').classList.toggle('off', !on);
  $('btName').textContent = on && b.name ? `手机上显示为「${b.name}」` : '';
  let note;
  if (!b.available) note = '找不到蓝牙，或者蓝牙服务没有运行。';
  else if (!b.enabled) note = '打开后，手机可以把这台设备当蓝牙音箱放歌。';
  else if (b.window) note = `正在等手机连接（还剩 ${b.window} 秒）：在手机的蓝牙设置里点「${b.name}」，配对不用输密码。`;
  else note = '连过的手机在蓝牙设置里点一下就能再连上。放歌时说话或闹钟响，歌声会自动变小或暂停。';
  if (on && b.direct) note += ' 注意：系统层版本较旧，手机的声音没经过客户端，放歌时很难唤醒。';
  $('btNote').textContent = note;
  const phones = b.devices || [];
  $('btList').innerHTML = phones.length ? phones.map(d =>
    `<div class="al phone${d.streaming ? ' live' : ''}"><i>手机</i><div class="al-text"><b>${esc(d.name)}</b>` +
    `<span${d.streaming ? ' class="live"' : ''}>${d.streaming ? esc(btPlaying(d)) : d.connected ? '已连接' : '已配对，没连着'}</span></div>` +
    (btIsArmed(d.address) ? `<button class="al-del armed" data-addr="${esc(d.address)}">确认忘记</button></div>`
                          : `<button class="al-del" data-addr="${esc(d.address)}">忘记</button></div>`)).join('')
    : '<div class="alarm-empty">还没有连过的手机</div>';
  $('btAdd').classList.toggle('on', !!b.window);
  $('btAdd').textContent = b.window ? `停止等待（${b.window} 秒）` : '＋ 添加手机';
}
// 「忘记」 needs a second tap within 4 s. Kept by address, not on the button: while 添加手机 waits
// the list is redrawn every second (the countdown), which would drop a state kept on the DOM.
let btArmed = null;   // {addr, until}
const btIsArmed = addr => !!btArmed && btArmed.addr === addr && Date.now() < btArmed.until;
async function btPost(body) {
  if (DEMO !== null) {
    const b = ui.bluetooth;
    if ('enabled' in body) b.enabled = body.enabled;
    if ('window' in body) b.window = body.window ? 180 : 0;
    if (body.remove) b.devices = b.devices.filter(d => d.address !== body.remove);
    renderBluetooth();
    return { ok: true };
  }
  return postJson('/api/bluetooth', body);   // {ok: false} after it has shown the error
}
$('btSwitch').addEventListener('click', () => btPost({ enabled: !(ui.bluetooth && ui.bluetooth.enabled) }));
$('btAdd').addEventListener('click', () => btPost({ window: !(ui.bluetooth && ui.bluetooth.window) }));
$('btList').addEventListener('click', e => {
  const b = e.target.closest('.al-del');
  if (!b) return;
  if (!btIsArmed(b.dataset.addr)) {
    btArmed = { addr: b.dataset.addr, until: Date.now() + 4000 };
    renderBluetooth();
    setTimeout(renderBluetooth, 4100);
    return;
  }
  btArmed = null;
  btPost({ remove: b.dataset.addr }).then(r => { if (r && r.ok !== false) toast('已忘记，这台手机要重新配对才能连'); });
});


// 关机: halts the system (it stays dark with the power cable in), so unplugging can't damage it
let powerArmed = 0;
function renderPower() {
  $('powerBtn').textContent = Date.now() - powerArmed < 5000 ? '确认关机' : '关机';
  $('powerBtn').classList.toggle('armed', Date.now() - powerArmed < 5000);
}
$('powerBtn').addEventListener('click', async () => {
  if (Date.now() - powerArmed > 5000) {
    powerArmed = Date.now();
    renderPower();
    setTimeout(renderPower, 5100);
    return toast('关机后要拔掉电源，等 30 秒再插上才会开机，再点一下「确认关机」');
  }
  powerArmed = 0;
  renderPower();
  if (DEMO !== null) return toast('演示模式不会真的关机');
  const r = await (await post('/api/power-off', {}))?.json?.();
  if (!r || !r.ok) toast((r && r.error) || '没能关机', 'error');
});
$('settingsClose').addEventListener('click', () => {
  $('settings').classList.remove('on');
  if (!DEMO) postJson('/api/alarm-tone', { stop: true });   // a tone being tried stops with the settings
});
$('reportPill').addEventListener('click', () => { post('/api/reports/play'); toast('马上念给你听'); });

/* ---- right side: welcome screen or conversation ---- */
// The welcome screen (clock) when idle with nothing going on: no messages yet, or the conversation
// is over (the hub closed the connection). The hub keeps the history, so the thread is rarely empty;
// it comes back with the chat page on the next wake. While the connection lasts (~2 minutes) the
// chat page stays. A new conversation shows the chat (and its "listening" bubble) from the wake on.
function welcomeShown(messages, state, connected) {
  return ['idle', 'connecting'].includes(state) && (!messages || !connected);
}
function renderSide() {
  document.body.classList.toggle('fresh', welcomeShown(ui.thread.messages.length, shownState(), !!ui.status.connected));
}

function entryModel(entry) {
  return ui.models.options.find(o => o.entry === entry) || ui.models.current;
}

function renderHead() {
  const m = entryModel(ui.thread.entry);
  $('headLogo').src = logoSrc(m);
  $('headTitle').textContent = m ? `与 ${m.label} 的对话` : '对话';
  const n = ui.thread.messages.length;
  $('headMeta').textContent = n ? `${n} 条消息 · ${ui.status.connected ? '对话中' : '记录已保存'}` : '新对话';
}

function fmtTime(t) {
  const d = new Date(t * 1000);
  return pad(d.getHours()) + ':' + pad(d.getMinutes());
}

// A reply grows sentence by sentence; the text just shows as it is (no per-sentence highlight)
// a weather broadcast's request (broadcast.py): shown as a small label, not as something the user said
// (the hub's echo drops the leading 【; the conversation loaded back from the hub keeps it)
const BROADCAST_TAG = /^【?([^【】]{1,20})】/;
const broadcastTag = m => {
  const tag = m.role === 'user' && (BROADCAST_TAG.exec(m.text || '') || [])[1];
  return tag && (tag.includes('播报') || tag.startsWith('拍照问') || tag.startsWith('定时任务')) ? tag : null;
};
function fillMsg(el, m) {
  const tag = broadcastTag(m);
  if (tag) { el.innerHTML = `${tag.startsWith('早安') ? '☀' : tag.startsWith('拍照') ? '📷' : tag.startsWith('定时') ? '⏰' : '⛅'} ${esc(tag)}`; return; }
  if (m.role === 'card') {   // self.screen.show_card: a recipe, a list, a table (markdown rendered by home.js)
    const steps = ((m.card || {}).markdown || '').split('\n').filter(l => /^\s*(\d+[.、)）]|第[一二三四五六七八九十\d]+步)/.test(l)).length;
    el.querySelector('.body').innerHTML = `<h3 class="rich-title">${esc(m.text)}</h3>`
      + (window.cardHtml ? window.cardHtml(m.card || {}) : esc((m.card || {}).markdown || ''))
      + (steps >= 2 ? `<button class="card-cook" data-cook="${esc(m.id)}">开始做菜 · 一步一步来</button>` : '')
      + `<span class="ts">${fmtTime(m.time)}</span>`;
    return;
  }
  const html = esc(m.text) + (m.interrupted ? '<span class="cut">已打断</span>' : '') + `<span class="ts">${fmtTime(m.time)}</span>`;
  if (m.role === 'bot') el.querySelector('.body').innerHTML = html;
  else el.innerHTML = html;
}

function msgEl(m) {
  const el = document.createElement('div');
  el.className = 'msg ' + (m.role === 'card' ? 'bot rich' : broadcastTag(m) ? 'tag' : m.role);
  el.dataset.id = m.id;
  if (m.role === 'bot' || m.role === 'card') {
    el.innerHTML = `<img class="tile" src="${esc(logoSrc(entryModel(m.entry)))}" alt=""><div class="body"></div>`;
  }
  fillMsg(el, m);
  return el;
}

function scrollDown() {
  const box = $('messages');
  box.scrollTop = box.scrollHeight;
}

function renderThread() {
  const box = $('messages');
  box.textContent = '';
  for (const m of ui.thread.messages) {
    const el = msgEl(m);
    el.style.animation = 'none';
    box.appendChild(el);
  }
  markLive();
  renderHead();
  renderSide();
  renderPending();
  scrollDown();
}

function upsertMessage(m) {
  if (m.entry !== ui.thread.entry) return;
  const i = ui.thread.messages.findIndex(x => x.id === m.id);
  const box = $('messages');
  if (i >= 0) {
    ui.thread.messages[i] = m;
    const el = box.querySelector(`[data-id="${m.id}"]`);
    if (el) fillMsg(el, m);
  } else {
    ui.thread.messages.push(m);
    box.insertBefore(msgEl(m), $('pending'));
  }
  markLive();
  renderHead();
  renderSide();
  scrollDown();
}

function markLive() {
  // the reply being spoken right now: its logo gets a ring
  const box = $('messages');
  box.querySelectorAll('.live').forEach(el => el.classList.remove('live'));
  const last = ui.thread.messages[ui.thread.messages.length - 1];
  if (ui.status.state === 'speaking' && last && last.role === 'bot') {
    const el = box.querySelector(`[data-id="${last.id}"]`);
    if (el) el.classList.add('live');
  }
}

// Placeholder at the end of the conversation while the user talks or the model thinks
function renderPending() {
  const shown = shownState();
  // recognising: the user stopped and the hub is turning it into text (it can take a few seconds)
  const kind = ui.status.recognising && !ui.ptt ? 'user recog' : shown === 'listening' ? (ui.ptt ? 'user ptt' : 'user') :
    shown === 'thinking' ? 'bot' : '';
  let el = $('pending');
  if (el && el.dataset.kind === kind) return;
  if (el) el.remove();
  if (!kind) return;
  el = document.createElement('div');
  el.id = 'pending';
  el.dataset.kind = kind;
  el.className = 'msg pending ' + kind;
  if (kind.startsWith('user')) {
    el.innerHTML = `<span class="wave">${'<b></b>'.repeat(5)}</span>${kind === 'user recog' ? '正在识别…' : ui.ptt ? '说完松开' : '正在听…'}`;
  } else {
    el.innerHTML = `<img class="tile" src="${esc(logoSrc(entryModel(ui.thread.entry)))}" alt="">` +
      `<div class="body"><span class="dots"><i></i><i></i><i></i></span><span class="label">正在思考</span></div>`;
  }
  $('messages').appendChild(el);
  scrollDown();
}

/* ---- overlays ---- */
let toastTimer = null;
function toast(text, kind) {
  const t = $('toast');
  t.textContent = text;
  t.className = 'toast on' + (kind === 'error' ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = 'toast' + (kind === 'error' ? ' error' : ''); }, kind === 'error' ? 4500 : 2500);
}

let hudTimer = null;
function showVolume(v) {
  $('hudFill').style.transform = `scaleX(${Math.max(0, Math.min(100, v)) / 100})`;
  $('hudNum').textContent = v;
  $('hud').classList.add('on');
  clearTimeout(hudTimer);
  hudTimer = setTimeout(() => $('hud').classList.remove('on'), 1600);
}

/* ---- photo slideshow (photos.py: programs on the device post the images; upstream PR #3) ---- */
const slides = { show: null, i: 0, front: 0, timer: null, barTimer: null, down: null };
const slideUrl = n => `/photos/${slides.show.id}/${n}`;

function renderSlides(p) {
  if (p && slides.show && p.id === slides.show.id) return;   // the same show (a snapshot after reconnecting)
  clearTimeout(slides.timer);
  slides.show = p || null;
  document.body.classList.toggle('showing-slides', !!p);
  for (const id of ['slideA', 'slideB']) { $(id).classList.remove('on'); $(id).removeAttribute('src'); }
  if (!p) { $('slides').classList.remove('bar'); return; }
  $('slideTitle').textContent = p.title || '照片';
  showSlide(0);
  showSlideBar();
}

function showSlide(n) {
  const s = slides.show;
  if (!s) return;
  const i = slides.i = ((n % s.count) + s.count) % s.count;
  const next = $(slides.front ? 'slideA' : 'slideB'), cur = $(slides.front ? 'slideB' : 'slideA');
  const advance = () => {
    clearTimeout(slides.timer);
    if (s.count > 1) slides.timer = setTimeout(() => showSlide(slides.i + 1), s.interval * 1000);
  };
  next.onload = () => {
    if (slides.show !== s || slides.i !== i) return;   // moved on while it loaded
    next.classList.add('on'); cur.classList.remove('on');
    slides.front = 1 - slides.front;
    if (s.count > 1) new Image().src = slideUrl((i + 1) % s.count);   // ready for the next turn
    advance();
  };
  next.onerror = () => { if (slides.show === s && slides.i === i) advance(); };   // skip it, keep going
  next.src = slideUrl(i);
  $('slideNum').textContent = s.count > 1 ? `${i + 1} / ${s.count}` : '';
}

function showSlideBar() {
  $('slides').classList.add('bar');
  clearTimeout(slides.barTimer);
  slides.barTimer = setTimeout(() => $('slides').classList.remove('bar'), 4000);
}

// one finger: swipe left/right to turn, tap to show or hide the bar (the close button is on it)
$('slides').addEventListener('pointerdown', e => { slides.down = { x: e.clientX, y: e.clientY }; });
$('slides').addEventListener('pointercancel', () => { slides.down = null; });
$('slides').addEventListener('pointerup', e => {
  const d = slides.down;
  slides.down = null;
  if (!d || !slides.show || e.target.closest('.slide-close')) return;
  const dx = e.clientX - d.x, dy = e.clientY - d.y;
  if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) {
    if (slides.show.count > 1) showSlide(slides.i + (dx < 0 ? 1 : -1));
    showSlideBar();
  } else if ($('slides').classList.contains('bar')) {
    clearTimeout(slides.barTimer);
    $('slides').classList.remove('bar');
  } else {
    showSlideBar();
  }
});
$('slideClose').addEventListener('click', () => post('/api/photos/close'));

/* ---- events from the client ---- */
const handlers = {
  snapshot(s) {
    if (ui.build && s.build && s.build !== ui.build) return location.reload();   // client restarted (deployed)
    if (!ui.build && !DEMO) setTimeout(() => {   // 第一份状态画完后，把屏幕上实际显示的记到设备日志（排查屏幕不动用）
      try {
        fetch('/api/page-log', { method: 'POST', body: JSON.stringify({ kind: 'rendered', model: $('pillLabel').textContent, time: $('time').textContent,
          hidden: document.hidden, visibility: document.visibilityState, size: `${innerWidth}x${innerHeight}`, state: ui.status && ui.status.state }) }).catch(() => {});
      } catch (e) { /* ignore */ }
    }, 1500);
    ui.build = s.build;
    ui.status = s.status; ui.models = server.models = s.models; ui.thread = server.thread = s.thread;
    switching = null;
    for (const k in seenThreads) delete seenThreads[k];
    seenThreads[s.thread.entry] = s.thread.messages;
    if (s.update) { ui.version = s.version; ui.update = s.update; }   // the demo's snapshots have none
    if (s.alarms) ui.alarms = s.alarms;
    if (s.bluetooth) ui.bluetooth = s.bluetooth;
    if (s.wake_words) ui.wake = s.wake_words;
    if (s.broadcast) ui.broadcast = s.broadcast;
    if (s.looks) ui.looks = s.looks;
    if ('slides' in s) renderSlides(s.slides);   // the demo's snapshots have none
    if (s.kids) { ui.kids = s.kids; renderKids(); }
    if (s.standby) { ui.standby = s.standby; renderStandbyCard(); }
    if (s.cook) { ui.cook = s.cook; renderCook(); }
    if (s.media) { ui.media = s.media; renderMedia(); }
    if (s.scenes) { ui.scenes = s.scenes; renderScenes(); }
    if (s.notes) { ui.notes = s.notes; renderNotes(); }
    ui.lists = s.lists || ui.lists; renderLists();
    if (s.tasks) { ui.tasks = s.tasks; renderTasks(); }
    if (s.alerts) { ui.alerts = s.alerts; renderAlertsUi(); }
    if (s.translate) { ui.translate = s.translate; renderTranslate(); }
    ui.today = s.today || ui.today; renderLooks(); renderToday();
    ui.photo = s.photo || null; renderPhoto();
    renderModels(); renderThread(); renderStatus(); renderAlarms(); renderBluetooth(); renderWake(); renderBroadcast();
    updateFinished((s.update || {}).job);
  },
  power(p) {
    $('powerOff').classList.toggle('on', p.state === 'off');
  },
  update(u) {
    ui.update = u;
    renderUpdate();
    updateFinished(u.job);
  },
  state(s) {
    ui.status = s; renderStatus(); markLive(); renderHead();
  },
  model(m) {
    server.models = m;
    if (switching && (!m.current || m.current.entry !== switching)) return;
    ui.models = m; renderModels(); renderStatus();
  },
  thread(t) {
    server.thread = t;
    if (t.reset) for (const k in seenThreads) delete seenThreads[k];   // cleared, or the hub's history loaded
    seenThreads[t.entry] = t.messages;
    if (switching && t.entry !== switching) return;
    ui.thread = t; renderThread();
  },
  message: m => upsertMessage(m),
  toast: t => toast(t.text, t.kind),
  volume(v) {
    // a ring's own volume (keys while it rings) is shown, but isn't the device volume
    if (!v.ring) { ui.status.volume = v.volume; renderVol(); }
    if (v.source !== 'page') showVolume(v.volume);
  },
  alarms(a) {
    ui.alarms = a; renderAlarms();
  },
  bluetooth(b) {
    ui.bluetooth = b; renderBluetooth();
  },
  wake_words(w) {
    ui.wake = w; renderWake(); renderStatus(); renderBright();
  },
  broadcast(b) {
    ui.broadcast = b; renderBroadcast();
  },
  voices(v) {
    ui.voices = v; renderVoices();
  },
  tasks(t) {
    ui.tasks = t; renderTasks();
  },
  alerts(a) {
    ui.alerts = a; renderAlertsUi();
  },
  translate(t) {
    ui.translate = t; renderTranslate();
  },
  media(m) {
    ui.media = m; renderMedia();
  },
  scenes(x) {
    ui.scenes = x; renderScenes();
  },
  notes(n) {
    ui.notes = n; renderNotes();
  },
  lists(l) {
    ui.lists = l; renderLists();
  },
  standby(st) {
    ui.standby = st; renderStandbyCard();
  },
  cook(c) {
    ui.cook = c; renderCook();
  },
  kids(k) {
    ui.kids = k; renderKids();
  },
  looks(l) {
    ui.looks = l; renderLooks();
  },
  today(t) {
    ui.today = t; renderToday();
  },
  photo(p) {
    ui.photo = p.state === 'closed' ? null : p; renderPhoto();
  },
  slides: p => renderSlides(p),
  level(l) {
    level.target = ui.status.state === 'speaking' ? Math.min(1, l.out * 6) : micLevel(l.mic);
    if (ww.holding) wakeMeter(micLevel(l.mic));
    if (vc.holding) holdMeter($('voicesHold'), micLevel(l.mic));
    if (bc.holding) holdMeter($('bcHold'), micLevel(l.mic));
  },
};

// 没连上网络：连续两次（约 30 秒）读不到地址，首页顶部出提示条，点一下打开选 Wi‑Fi（上游 image issue #5）
const net = { misses: 0 };
async function watchNetwork() {
  if (DEMO !== null) return;
  let ip = null;
  try { ip = (await (await fetch('/api/network')).json()).ip; } catch (e) { return; }   // 客户端自己没响应：不算断网
  net.misses = ip ? 0 : net.misses + 1;
  const show = net.misses >= 2;
  if ($('netBanner').hidden === show) {
    $('netBanner').hidden = !show;
    document.body.classList.toggle('net-off', show);
    if (typeof fitHome === 'function') fitHome();
  }
}
$('netBanner').addEventListener('click', () => { if (shellWifi) openWifi(); else { $('settings').classList.add('on'); renderWifiCard(); } });
setInterval(watchNetwork, 15000);
setTimeout(watchNetwork, 3000);

// The stream must survive the client restarting (updates, crashes). EventSource reconnects by itself after a
// dropped connection; in case it gives up (readyState CLOSED) or a half-open connection never delivers again,
// reconnect ourselves when it closes, and when nothing (not even the client's 15 s ping) has arrived for 40 s.
let events = null, eventsSeen = 0, eventsRetry = null;
function connectEvents() {
  clearTimeout(eventsRetry); eventsRetry = null;
  if (events) events.close();
  const es = events = new EventSource('/events');
  eventsSeen = Date.now();
  for (const [name, fn] of Object.entries(handlers)) es.addEventListener(name, e => { eventsSeen = Date.now(); fn(JSON.parse(e.data)); });
  es.addEventListener('ping', () => { eventsSeen = Date.now(); });
  es.onerror = () => {
    if (es === events && es.readyState === EventSource.CLOSED && !eventsRetry) eventsRetry = setTimeout(connectEvents, 3000);
  };
}
setInterval(() => { if (!DEMO && events && Date.now() - eventsSeen > 40000) connectEvents(); }, 10000);

/* ---- buttons ---- */
function post(path, body) {
  if (DEMO) return demo.action(path, body);
  const opts = { method: 'POST' };
  if (body) Object.assign(opts, { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return fetch(path, opts).catch(() => toast('客户端没有响应', 'error'));
}

// Mic: a tap wakes (or interrupts a reply and listens); holding it is push-to-talk
let holdTimer = null;
function pttStart() {
  ui.ptt = true;
  $('btnMic').classList.add('hold');
  renderStatus();
}
function pttEnd() {
  ui.ptt = false;
  $('btnMic').classList.remove('hold');
  renderStatus();
}
$('btnMic').addEventListener('pointerdown', e => {
  e.preventDefault();
  holdTimer = setTimeout(() => {
    holdTimer = null;
    pttStart();
    post('/api/ptt/start');
  }, 350);
});
function release() {
  if (holdTimer) {
    clearTimeout(holdTimer);
    holdTimer = null;
    post('/api/wake');
  } else if (ui.ptt) {
    pttEnd();
    post('/api/ptt/stop');
  }
}
$('btnMic').addEventListener('pointerup', release);
$('btnMic').addEventListener('pointercancel', release);
$('btnMic').addEventListener('pointerleave', () => { if (ui.ptt) release(); });
$('btnAbort').addEventListener('click', () => post('/api/abort'));
$('btnEnd').addEventListener('click', () => { post('/api/end'); toast('对话已结束，聊天记录已保存'); });
$('modelPill').addEventListener('click', () => openSheet(true));
$('sheetClose').addEventListener('click', () => openSheet(false));
document.addEventListener('contextmenu', e => e.preventDefault());

for (const [q, bg, fg, icon] of SUGGEST) {
  const b = document.createElement('button');
  b.className = 'chip';
  b.innerHTML = `<i style="background:${bg};color:${fg}"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${icon}</svg></i>${esc(q)}`;
  b.addEventListener('click', () => post('/api/text', { text: q }));
  $('chips').appendChild(b);
}

/* ---- demo: fake client for previewing the page on a desktop browser ---- */
const demo = DEMO === null ? null : (() => {
  const M = (label, model, logo, company, vendor) => ({ kind: 'llm', vendor, label, model, logo, company, entry: 'llm:' + vendor });
  const options = [
    M('Claude', 'claude-sonnet-4-5', 'claude-color', 'Anthropic', 'claude'),
    M('DeepSeek', 'deepseek-chat', 'deepseek-color', 'DeepSeek', 'deepseek'),
    M('通义千问', 'qwen-max', 'qwen-color', '阿里通义', 'qwen'),
    M('豆包', 'doubao-1.5-pro', 'doubao-color', '字节豆包', 'doubao'),
    M('Gemini', 'gemini-2.5-flash', 'gemini-color', 'Google', 'gemini'),
    M('Kimi', 'kimi-k2', 'kimi-color', '月之暗面', 'kimi'),
    M('GPT', 'gpt-4.1', 'openai', 'OpenAI', 'openai'),
    M('智谱 GLM', 'glm-4.6', 'zhipu-color', '智谱', 'glm'),
  ];
  let seq = 0, bot = null, timer = null, stopped = false;
  const threads = {};
  const entry = () => ui.models.current.entry;
  const now = () => Date.now() / 1000;
  const state = (s, connected = true) => handlers.state({ state: s, connected, uploading: s === 'listening' });
  const say = (role, text) => {
    const m = { id: ++seq, role, text, time: now(), entry: entry() };
    (threads[m.entry] = threads[m.entry] || []).push(m);
    handlers.message({ ...m });
    return m;
  };
  const sentence = text => {
    if (!bot) { state('speaking'); bot = say('bot', text); return; }
    bot.text += text;
    handlers.message({ ...bot });
  };
  const done = (cut) => {
    if (bot && cut) { bot.interrupted = true; handlers.message({ ...bot }); }
    bot = null;
  };

  // fake microphone / speaker levels: syllable-like bursts
  setInterval(() => {
    const t = performance.now() / 1000;
    const v = Math.max(0, Math.abs(Math.sin(t * 6.3)) * (.55 + .45 * Math.sin(t * 1.7)) + (Math.random() - .5) * .25);
    handlers.level({ mic: v * .07, out: v * .17 });
  }, 100);

  const tones = { coffee: '咖啡时光', morning: '晨曦', bright: '元气早晨', hop: '阳光小跳跃', marimba: '清晨木琴' };
  const alarmsDemo = () => ({
    synced: true, volume: 70, tone: 'coffee', tones, ringing: null,
    items: [
      { id: 3, kind: 'timer', type: '倒计时', label: '煮鸡蛋', at: now() + 271, repeat: '', snoozed: false },
      { id: 1, kind: 'alarm', type: '闹钟', label: '起床', at: now() + 9 * 3600, repeat: '每个工作日（周一到周五）', snoozed: false },
      { id: 2, kind: 'reminder', type: '提醒', label: '晾衣服', at: now() + 26 * 3600, repeat: '', snoozed: false, spoken: true },
    ],
    missed: [{ kind: 'reminder', label: '吃药', at: now() - 3 * 3600, why: 'unanswered', noted: now() - 3 * 3600 },
             { kind: 'alarm', label: '起床', at: now() - 20 * 3600, why: 'off', noted: now() - 20 * 3600 }],
  });
  const steps = [
    ['idle', 4000, () => {
      handlers.snapshot({ status: { state: 'idle', connected: false, uploading: false, volume: 60 }, alarms: alarmsDemo(),
                          wake_words: { primary: '小麦小麦', custom: true, recording: false, max: 6, words: [
                            { label: '小麦小麦', pinyin: 'xiǎo mài xiǎo mài' }, { label: '哈喽小麦', pinyin: 'hā lóu xiǎo mài' },
                            { label: '小麦同学', pinyin: 'xiǎo mài tóng xué' }] },
                          bluetooth: { available: true, enabled: true, powered: true, window: 0, name: 'ThinkSmart View', direct: false,
                                       devices: [{ address: 'AA:BB:CC:DD:EE:01', name: '小张的 iPhone', paired: true, connected: true, streaming: true,
                                                   title: '晴天', artist: '周杰伦' },
                                                 { address: 'AA:BB:CC:DD:EE:02', name: 'Redmi K70', paired: true, connected: false, streaming: false }] },
                          models: { current: options[0], options }, thread: { entry: options[0].entry, messages: [] } });
    }],
    ['settings', 3000, () => $('settings').classList.add('on')],
    [null, 300, () => $('settings').classList.remove('on')],
    ['ring', 4000, () => handlers.alarms(Object.assign(alarmsDemo(), {
      ringing: { items: [alarmsDemo().items[1]], notes: [], started: now() } }))],
    [null, 300, () => handlers.alarms(alarmsDemo())],
    ['connecting', 1600, () => state('connecting', false)],
    ['listening', 3200, () => state('listening')],
    [null, 500, () => say('user', '明天北京天气怎么样？适合去爬山吗？')],
    ['thinking', 2600, () => state('thinking')],
    [null, 1700, () => sentence('明天北京晴转多云，气温 9 到 19 度，')],
    [null, 1700, () => sentence('北风二三级，空气质量良。')],
    ['speaking', 2200, () => sentence('这样的天气很适合去爬山，香山的红叶正是最好看的时候。')],
    [null, 2400, () => sentence('早晚温差比较大，记得带一件薄外套。')],
    [null, 2600, () => { done(); state('listening'); }],
    [null, 500, () => say('user', '那推荐一条人少一点的路线吧')],
    [null, 2000, () => state('thinking')],
    [null, 1800, () => sentence('可以试试从北京植物园进山，走樱桃沟到鬼笑石，')],
    ['chat', 2400, () => sentence('人比东门少很多，沿途还有溪水和水杉林。')],
    ['cut', 1400, () => { done(true); state('idle'); }],
    ['models', 3200, () => openSheet(true)],
    [null, 600, () => switchTo(options[1])],
    ['volume', 2400, () => showVolume(60)],
    ['ptt', 2600, () => { pttStart(); state('listening'); }],
    [null, 400, () => { pttEnd(); say('user', '用一句话介绍一下你自己'); state('thinking'); }],
    [null, 1800, () => sentence('我是小麦，住在这块屏幕里的语音助手，有问题随时叫我。')],
    [null, 3200, () => { done(); state('idle'); }],
    [null, 3000, () => { state('idle', false); toast('对话已结束，聊天记录已保存'); }],
  ];

  function switchTo(m) {
    openSheet(false);
    handlers.model({ current: m, options });
    handlers.thread({ entry: m.entry, messages: (threads[m.entry] || []).slice() });
    toast(`已切换到 ${m.label}`);
  }

  function run(i, jump) {
    if (stopped) return;
    const [name, wait, fn] = steps[i % steps.length];
    if (i % steps.length === 0) { seq = 0; for (const k in threads) delete threads[k]; }
    fn();
    if (jump && name === jump) return;          // ?demo=<scene>: stop on that scene
    if (jump) return run(i + 1, jump);
    timer = setTimeout(() => run(i + 1), wait);
  }

  // tapping around stops the script and lets the buttons do something plausible
  function action(path, body) {
    stopped = true;
    clearTimeout(timer);
    const answer = q => {
      say('user', q);
      state('thinking');
      timer = setTimeout(() => {
        sentence('这是演示模式，');
        timer = setTimeout(() => { sentence('真正的回答要连上客户端才有哦。'); timer = setTimeout(() => { done(); state('idle'); }, 2600); }, 1500);
      }, 1600);
    };
    if (path === '/api/wake') state('listening');
    else if (path === '/api/ptt/start') state('listening');
    else if (path === '/api/ptt/stop') answer('（按住说话的内容）');
    else if (path === '/api/text') answer(body.text);
    else if (path === '/api/abort') { done(true); state('idle'); }
    else if (path === '/api/end') { done(); state('idle', false); }
    else if (path.startsWith('/api/alarms/')) handlers.alarms(Object.assign({}, ui.alarms, { ringing: null }));
    return Promise.resolve();
  }

  return { start: () => run(0, DEMO || null), action, switchTo };
})();

tick();
setInterval(tick, 1000);
renderStatus();
renderThread();
// after the other scripts (home.js) have added their handlers
document.addEventListener('DOMContentLoaded', () => { if (demo) demo.start(); else connectEvents(); });
