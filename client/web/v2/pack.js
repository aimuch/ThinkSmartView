// 形象库（设计画布第 41 块）：光球之外的形象，来自 Virtual-Assistant-Avatar，装在设备本地（looks.PACK_DIR）。
// 左边（竖屏在上面）按角色分组，右边网格；点一下在上面看待机、唤醒、睡觉三个样子，点「用这个」才换。
// 在 work.js 之后加载；用 app.js 的 $、esc、toast、ui、setLooks。
(() => {

const pk = { data: null, loading: null, group: '全部', sel: null, open: false, sig: '' };

async function load() {
  if (pk.data && pk.data.version === ((ui.looks && ui.looks.pack) || {}).version) return pk.data;
  if (!pk.loading) {
    pk.loading = fetch('/api/avatar-pack').then(r => r.json()).catch(() => null).then(d => { pk.data = d; pk.loading = null; return d; });
  }
  return pk.loading;
}
const all = () => (pk.data && pk.data.avatars) || [];
const byId = id => all().find(a => a.id === id);
const current = () => (ui.looks && ui.looks.avatar) || '';

function renderPrev() {
  const a = pk.sel && byId(pk.sel);
  $('pkPrev').hidden = !a;
  $('avPack').classList.toggle('has-prev', !!a);
  if (!a) return;
  const using = a.id === current();
  const st = (url, name) => `<div class="pk-st"><img src="${esc(url)}" alt="">${name}</div>`;
  $('pkPrev').innerHTML = st(a.poses[0], '待机') + st(a.poses[1], '唤醒') + st(a.poses[4], '睡觉') +
    `<div class="pk-nm"><b>${esc(a.name)}</b><small>${a.alias ? esc(a.alias) + ' · ' : ''}${esc(a.series || a.group)}</small>${a.tag ? `<em>${esc(a.tag)}</em>` : ''}</div>` +
    `<button class="pk-use ${using ? 'done' : ''}" id="pkUse" ${using ? 'disabled' : ''}>${using ? '在用' : '用这个'}</button>`;
}

function render() {
  if (!pk.data) return;
  const groups = [{ name: '全部', count: all().length }].concat(pk.data.groups || []);
  const sig = pk.group + '|' + current() + '|' + pk.sel + '|' + pk.data.version;
  if (sig === pk.sig) return;
  const groupChanged = !pk.sig.startsWith(pk.group + '|');
  pk.sig = sig;
  $('pkGroups').innerHTML = groups.map(g => `<button data-g="${esc(g.name)}" class="${g.name === pk.group ? 'on' : ''}">` +
    `<span>${esc(g.name)}</span><i>${g.count}</i></button>`).join('');
  const list = pk.group === '全部' ? all() : all().filter(a => a.group === pk.group);
  $('pkGrid').innerHTML = list.map(a => `<button class="pk-t ${a.id === pk.sel ? 'on' : ''} ${a.id === current() ? 'cur' : ''}" data-pk="${esc(a.id)}">` +
    `<img src="${esc(a.thumb)}" alt="" loading="lazy" decoding="async"><b>${esc(pk.group === '全部' ? a.name : a.short)}</b>` +
    `${a.tag ? `<small>${esc(a.tag)}</small>` : ''}</button>`).join('');
  if (groupChanged) $('pkGrid').scrollTop = 0;
  renderPrev();
}

window.packRender = () => { if (pk.open) render(); };

window.packOpen = async () => {
  const d = await load();
  if (!d || !d.available) return toast('形象库还没装到这台设备上', 'error');
  const cur = byId(current());
  pk.group = cur ? cur.group : '全部';
  pk.sel = cur ? cur.id : null;
  pk.sig = '';
  pk.open = true;
  render();
  $('avPack').classList.add('on');
  const on = $('pkGrid').querySelector('.pk-t.cur');
  if (on) on.scrollIntoView({ block: 'center' });
};

function close() {
  pk.open = false;
  $('avPack').classList.remove('on');
}

$('pkClose').addEventListener('click', close);
$('pkGroups').addEventListener('click', e => {
  const b = e.target.closest('[data-g]');
  if (!b || b.dataset.g === pk.group) return;
  pk.group = b.dataset.g;
  render();
  b.scrollIntoView({ block: 'nearest', inline: 'nearest' });
});
$('pkGrid').addEventListener('click', e => {
  const b = e.target.closest('[data-pk]');
  if (!b) return;
  pk.sel = b.dataset.pk;
  render();
});
$('pkPrev').addEventListener('click', async e => {
  if (!e.target.closest('#pkUse') || !pk.sel) return;
  await setLooks({ avatar: pk.sel });
  render();
});

})();
