// 屏幕现在显示的是哪一页（一个地方说了算）
//
// 页面上能盖住首页的有十来个全屏层（.sheet：设置、工作台、家居、天气、选模型……）、待机页，加上仪表盘的页签；
// 各自用 class / hidden 开关，散在三十来处代码里。这里不改它们，只看结果：哪一层在最上面，算出一个名字，
//   main/chat、main/home（仪表盘页签）、settings/system、work/rack、standby、home（米家）……
// 变了就发 screenchange 事件（detail: {view, from}），并告诉设备客户端（/api/page-state）：
// 设备日志里记一行「page view: …」，GET /api/page-state 能查到现在和最近几次（排查「屏幕上是什么」不用拍照）。
(() => {
const S = { view: '', since: 0, timer: null };

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
  if (view === S.view) return;
  const from = S.view;
  S.view = view; S.since = Date.now();
  document.dispatchEvent(new CustomEvent('screenchange', { detail: { view, from } }));
  if (typeof DEMO !== 'undefined' && DEMO) return;
  try { fetch('/api/page-state', { method: 'POST', body: JSON.stringify({ view, from }) }).catch(() => {}); } catch (e) { /* ignore */ }
}
// 一次点击里常常连着改好几处（关这个、开那个）：攒一会儿再算，只报最后的样子（不用 requestAnimationFrame：熄屏、页面藏起来时它不跑）
function soon() { if (!S.timer) S.timer = setTimeout(report, 60); }

const mo = new MutationObserver(soon);
mo.observe(document.body, { attributes: true, attributeFilter: ['class', 'data-dash'] });
document.querySelectorAll('.sheet, .set-pane, #standby').forEach(el => mo.observe(el, { attributes: true, attributeFilter: ['class', 'hidden'] }));
const tabs = $('wkTabs');
if (tabs) mo.observe(tabs, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
soon();

window.screenNow = () => ({ view: S.view || compute(), since: S.since });
})();
