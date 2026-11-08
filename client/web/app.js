// ThinkSmart voice client page: renders what the client streams on /events and sends the buttons back.
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
const WEEK = '日一二三四五六';

const ui = {
  status: { state: 'idle', connected: false, uploading: false },
  models: { current: null, options: [] },
  thread: { entry: null, messages: [] },
  ptt: false,
};

/* ---- clock ---- */
function tick() {
  const now = new Date();
  $('time').textContent = String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
  $('date').textContent = `${now.getMonth() + 1}月${now.getDate()}日 星期${WEEK[now.getDay()]}`;
}

/* ---- status: orb colour, title, hint, buttons ---- */
function modelName() {
  const cur = ui.models.current;
  return cur ? cur.label : '模型';
}

function renderStatus() {
  const { state, connected } = ui.status;
  const shown = ui.ptt ? 'listening' : state;
  document.body.dataset.state = shown;
  const text = {
    idle: ['说<em>「小麦小麦」</em>', connected ? '对话还保留着，唤醒后可以接着聊' : '或者点下面的麦克风；按住可以直接说'],
    connecting: ['正在连接…', '连上小智网关后就开始听'],
    listening: ui.ptt ? ['松开发送', '说完松开手指'] : ['我在听', '说完停一下我就开始回答'],
    thinking: [`${esc(modelName())} 正在思考…`, '复杂的问题可能要想一会儿'],
    speaking: ['正在回答', '随时开口就能打断我'],
  }[shown] || ['', ''];
  $('title').innerHTML = text[0];
  $('hint').textContent = text[1];
  $('btnAbort').disabled = state !== 'speaking';
  $('btnEnd').disabled = !connected;
  const link = $('link');
  link.classList.toggle('on', connected);
  link.querySelector('span').textContent = connected ? '对话中' : '待机';
  if (!['listening', 'speaking'].includes(shown)) document.body.style.setProperty('--lvl', 0);
}

/* ---- models ---- */
function renderModels() {
  const box = $('models');
  box.textContent = '';
  const cur = ui.models.current;
  for (const m of ui.models.options) {
    const b = document.createElement('button');
    b.className = 'model' + (cur && cur.entry === m.entry ? ' on' : '');
    b.innerHTML = (m.logo ? `<img src="/static/logos/${esc(m.logo)}.svg" alt="">` : '') +
      `<span><div class="ml">${esc(m.label)}</div><div class="mm">${esc(m.model || m.company || '')}</div></span>`;
    b.addEventListener('click', () => switchModel(m));
    box.appendChild(b);
  }
  renderChatHead();
}

async function switchModel(m) {
  if (ui.models.current && ui.models.current.entry === m.entry) return;
  try {
    const r = await (await fetch('/api/model', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                 body: JSON.stringify({ kind: m.kind, vendor: m.vendor, entry: m.entry }) })).json();
    if (r.ok === false) toast(r.error || '切换失败', 'error');
    else toast(`已切换到 ${m.label}`);
  } catch (e) {
    toast('切换失败：客户端没有响应', 'error');
  }
}

/* ---- conversation ---- */
function entryModel(entry) {
  return ui.models.options.find(o => o.entry === entry) || ui.models.current;
}

function renderChatHead() {
  const m = entryModel(ui.thread.entry);
  $('chatLogo').src = m && m.logo ? `/static/logos/${m.logo}.svg` : '';
  $('chatTitle').textContent = m ? `与 ${m.label} 的对话` : '对话';
  const n = ui.thread.messages.length;
  $('chatSub').textContent = n ? `${n} 条消息 · ${ui.status.connected ? '上下文保持中' : '已结束'}` : '新对话';
}

function fmtTime(t) {
  const d = new Date(t * 1000);
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

function msgHtml(m) {
  return esc(m.text) + (m.interrupted ? '<span class="cut">已打断</span>' : '') + `<span class="ts">${fmtTime(m.time)}</span>`;
}

function renderThread() {
  const box = $('messages');
  box.textContent = '';
  if (!ui.thread.messages.length) {
    box.innerHTML = `<div class="empty"><div class="big">说「小麦小麦」开始聊天</div><div class="tips">
      <div class="tip">「今天北京天气怎么样？」</div>
      <div class="tip">「嗨 Claude，帮我想个周末计划」</div>
      <div class="tip">「把音量调小一点」</div></div></div>`;
  } else {
    for (const m of ui.thread.messages) box.appendChild(msgEl(m));
  }
  renderChatHead();
  box.scrollTop = box.scrollHeight;
}

function msgEl(m) {
  const el = document.createElement('div');
  el.className = 'msg ' + m.role;
  el.dataset.id = m.id;
  el.innerHTML = msgHtml(m);
  return el;
}

function upsertMessage(m) {
  if (m.entry !== ui.thread.entry) return;
  const i = ui.thread.messages.findIndex(x => x.id === m.id);
  const box = $('messages');
  if (i >= 0) {
    ui.thread.messages[i] = m;
    const el = box.querySelector(`[data-id="${m.id}"]`);
    if (el) el.innerHTML = msgHtml(m);
  } else {
    if (!ui.thread.messages.length) box.textContent = '';
    ui.thread.messages.push(m);
    box.appendChild(msgEl(m));
  }
  markLive();
  renderChatHead();
  box.scrollTop = box.scrollHeight;
}

function markLive() {
  // the reply being spoken right now gets a highlighted border
  const box = $('messages');
  box.querySelectorAll('.live').forEach(el => el.classList.remove('live'));
  const last = ui.thread.messages[ui.thread.messages.length - 1];
  if (ui.status.state === 'speaking' && last && last.role === 'bot') {
    const el = box.querySelector(`[data-id="${last.id}"]`);
    if (el) el.classList.add('live');
  }
}

/* ---- overlays ---- */
let toastTimer = null;
function toast(text, kind) {
  const t = $('toast');
  t.textContent = text;
  t.className = 'toast on' + (kind === 'error' ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = 'toast'; }, kind === 'error' ? 4500 : 2500);
}

let hudTimer = null;
function showVolume(v) {
  $('hudFill').style.width = v + '%';
  $('hudNum').textContent = v;
  $('hud').classList.add('on');
  clearTimeout(hudTimer);
  hudTimer = setTimeout(() => $('hud').classList.remove('on'), 1600);
}

/* ---- events from the client ---- */
function connectEvents() {
  const es = new EventSource('/events');
  const on = (name, fn) => es.addEventListener(name, e => fn(JSON.parse(e.data)));
  on('snapshot', s => {
    ui.status = s.status; ui.models = s.models; ui.thread = s.thread;
    renderModels(); renderThread(); renderStatus();
  });
  on('state', s => { ui.status = s; renderStatus(); markLive(); renderChatHead(); });
  on('model', m => { ui.models = m; renderModels(); renderStatus(); });
  on('thread', t => { ui.thread = t; renderThread(); });
  on('message', m => upsertMessage(m));
  on('toast', t => toast(t.text, t.kind));
  on('volume', v => showVolume(v.volume));
  on('level', l => {
    const v = ui.status.state === 'speaking' ? l.out * 6 : l.mic * 14;
    document.body.style.setProperty('--lvl', Math.min(1, v).toFixed(3));
  });
}

/* ---- buttons ---- */
const post = path => fetch(path, { method: 'POST' }).catch(() => toast('客户端没有响应', 'error'));

// Mic: a tap wakes (or interrupts a reply and listens); holding it is push-to-talk
let holdTimer = null;
$('btnMic').addEventListener('pointerdown', e => {
  e.preventDefault();
  holdTimer = setTimeout(() => {
    holdTimer = null;
    ui.ptt = true;
    $('btnMic').classList.add('hold');
    renderStatus();
    post('/api/ptt/start');
  }, 350);
});
function release() {
  if (holdTimer) {
    clearTimeout(holdTimer);
    holdTimer = null;
    post('/api/wake');
  } else if (ui.ptt) {
    ui.ptt = false;
    $('btnMic').classList.remove('hold');
    renderStatus();
    post('/api/ptt/stop');
  }
}
$('btnMic').addEventListener('pointerup', release);
$('btnMic').addEventListener('pointercancel', release);
$('btnMic').addEventListener('pointerleave', () => { if (ui.ptt) release(); });
$('btnAbort').addEventListener('click', () => post('/api/abort'));
$('btnEnd').addEventListener('click', () => { post('/api/end'); toast('对话已结束，聊天记录已保存'); });
document.addEventListener('contextmenu', e => e.preventDefault());

tick();
setInterval(tick, 1000);
renderStatus();
renderThread();
connectEvents();
