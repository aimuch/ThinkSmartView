// 屏幕现在显示的是哪一页（一个地方说了算）
//
// 页面上能盖住首页的有十来个全屏层（.sheet：设置、工作台、家居、天气、选模型……）、待机页，加上仪表盘的页签；
// 各自用 class / hidden 开关，散在三十来处代码里。这里不改它们，只看结果：哪一层在最上面，算出一个名字，
//   main/chat、main/home（仪表盘页签）、settings/system、work/rack、standby、home（米家）……
// 变了就发 screenchange 事件（detail: {view, from}），并告诉设备客户端（/api/page-state）：
// 设备日志里记一行「page view: …」，GET /api/page-state 能查到现在和最近几次（排查「屏幕上是什么」不用拍照）。
(() => {
const S = { view: '', since: 0, timer: null, still: null, active: Date.now() };

// 形象和光球的待机动画（上下晃、呼吸）看不见、熄屏、或者 30 秒没事干时停下（body.calm，设计画布第 80 块）。
// 这台机器的页面是 CPU 画的：一个一直在动的小动画就让浏览器每秒合成 60 帧，空闲时白占一个核三成多
// （2026-11-19 实测：客厅待机页 34%，停了以后接近 0）。碰一下、叫它、换了状态就接着动
const CALM_AFTER = 30000;
const SEEN = /^main\//;   // 没被全屏层盖着（仪表盘别的页签上，首页的形象本来就 display: none，不出帧）
function applyCalm() {
  const b = document.body, idle = (b.dataset.state || 'idle') === 'idle' && Date.now() - S.active > CALM_AFTER;
  b.classList.toggle('calm', !SEEN.test(S.view || compute()) || b.classList.contains('screen-dark') || idle);
}
function stir() {
  S.active = Date.now();
  applyCalm();
  clearTimeout(S.still);
  S.still = setTimeout(applyCalm, CALM_AFTER + 50);
}

function sheetName(el) {
  const id = el.id;
  if (id === 'settings') {
    const p = document.querySelector('.set-pane.on');
    return 'settings' + (p && p.dataset.pane ? '/' + p.dataset.pane : '');
  }
  if (id === 'work') {
    const t = document.querySelector('#wkTabs [data-wtab].on');
    return 'work' + (t ? '/' + t.dataset.wtab : '');
  }
  return id || 'sheet';
}

function compute() {
  const sb = $('standby');
  if (sb && !sb.hidden) return 'standby';
  const open = [...document.querySelectorAll('.sheet.on')];   // 后面的盖在前面的上面
  if (open.length) return sheetName(open[open.length - 1]);
  const dash = document.body.dataset.dash;
  return 'main/' + (dash || (document.body.classList.contains('fresh') ? 'welcome' : 'convo'));
}

function report() {
  S.timer = null;
  const view = compute();
  if (view !== S.view && SEEN.test(view)) S.active = Date.now();   // 刚切回来：先动一会儿
  applyCalm();
  if (view === S.view) return;
  const from = S.view;
  S.view = view; S.since = Date.now();
  applyCalm();
  document.dispatchEvent(new CustomEvent('screenchange', { detail: { view, from } }));
  if (typeof DEMO !== 'undefined' && DEMO) return;
  try { fetch('/api/page-state', { method: 'POST', body: JSON.stringify({ view, from }) }).catch(() => {}); } catch (e) { /* ignore */ }
}
// 一次点击里常常连着改好几处（关这个、开那个）：攒一会儿再算，只报最后的样子（不用 requestAnimationFrame：熄屏、页面藏起来时它不跑）
function soon() { if (!S.timer) S.timer = setTimeout(report, 60); }

const mo = new MutationObserver(records => {
  if (records.some(r => r.attributeName === 'data-state')) stir();   // 叫醒、在想、在说：动起来
  soon();
});
mo.observe(document.body, { attributes: true, attributeFilter: ['class', 'data-dash', 'data-state'] });
addEventListener('pointerdown', stir, { passive: true });
document.querySelectorAll('.sheet, .set-pane, #standby').forEach(el => mo.observe(el, { attributes: true, attributeFilter: ['class', 'hidden'] }));
const tabs = $('wkTabs');
if (tabs) mo.observe(tabs, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
soon();
stir();

window.screenNow = () => ({ view: S.view || compute(), since: S.since });
})();
