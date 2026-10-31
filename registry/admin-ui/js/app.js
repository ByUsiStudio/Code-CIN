/**
 * 后台管理页面入口：负责状态管理与事件绑定，展示逻辑在 render.js，网络层在 api.js。
 */
import { getToken, restoreToken, saveToken, request } from './api.js';
import {
  stateMarkup,
  statsMarkup,
  countText,
  packagesMarkup,
  detailMarkup,
  placeholderMarkup
} from './render.js';

const MOBILE_QUERY = '(max-width: 59.99rem)';

const $ = (id) => document.getElementById(id);
const isMobile = () => matchMedia(MOBILE_QUERY).matches;

let packages = [];
let current = null;
let searchTimer = null;

function toast(message, ok = true) {
  const el = $('toast');
  el.textContent = message;
  el.className = 'toast show ' + (ok ? 'ok' : 'err');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    el.className = 'toast';
  }, 3200);
}

async function updateWho() {
  const who = $('who');
  const state = $('token-state');
  if (!getToken()) {
    who.textContent = '未登录 · 只读';
    who.className = 'chip';
    state.textContent = '未保存';
    return;
  }
  who.textContent = '校验中…';
  who.className = 'chip';
  try {
    const me = await request('/me');
    const label = me.nickname || me.name || me.email || me.sub || '已登录';
    who.textContent = label + (me.admin ? ' · 管理员' : '');
    who.className = 'chip ok';
    state.textContent = '已保存';
  } catch (err) {
    who.textContent = '令牌无效或已过期';
    who.className = 'chip bad';
    state.textContent = '无效';
  }
}

async function loadStats() {
  try {
    const stats = await request('/stats');
    $('stats').innerHTML = statsMarkup(stats);
  } catch (err) {
    $('stats').innerHTML = '<div class="stat"><div class="k">统计</div><div class="v">加载失败</div></div>';
  }
}

async function loadPackages() {
  const query = $('q').value.trim();
  const sort = $('sort').value;
  const order = $('order').value;
  const params = new URLSearchParams();
  if (query) params.set('q', query);
  if (sort) {
    params.set('sort', sort);
    params.set('order', order);
  }
  params.set('limit', '100');

  $('package-count').textContent = '加载中…';
  $('packages').innerHTML = stateMarkup('加载中…');
  try {
    const data = await request('/packages?' + params.toString());
    packages = data.packages || [];
    $('package-count').textContent = countText(data.total, packages.length);
    $('packages').innerHTML = packagesMarkup(packages, current && current.name);
  } catch (err) {
    $('package-count').textContent = '';
    $('packages').innerHTML = stateMarkup('加载失败', err.message);
  }
}

async function loadDetail(name) {
  $('detail').innerHTML = stateMarkup(name, '加载中…');
  try {
    current = await request('/packages/' + encodeURIComponent(name));
    $('detail').innerHTML = detailMarkup(current);
  } catch (err) {
    $('detail').innerHTML = stateMarkup(name, '加载失败：' + err.message);
  }
}

function selectPackage(name) {
  if (isMobile()) {
    document.body.classList.add('detail-open');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  loadDetail(name);
}

function closeDetailView() {
  current = null;
  document.body.classList.remove('detail-open');
  document.querySelectorAll('#packages .pkg').forEach((el) => el.classList.remove('is-active'));
  $('detail').innerHTML = placeholderMarkup();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function versionAction(action, version, yanked) {
  if (!getToken()) {
    toast('先保存 Cdifit AccessToken', false);
    return;
  }
  const name = current && current.name;
  if (!name) return;
  try {
    if (action === 'yank') {
      if (!confirm((yanked ? '撤回 ' : '恢复 ') + name + '@' + version + ' ?')) return;
      await request('/packages/' + encodeURIComponent(name) + '/' + encodeURIComponent(version) + '/yank', {
        method: 'POST',
        body: JSON.stringify({ yanked })
      });
      toast((yanked ? '已撤回 ' : '已恢复 ') + version);
    } else if (action === 'latest') {
      await request('/packages/' + encodeURIComponent(name) + '/' + encodeURIComponent(version) + '/latest', {
        method: 'POST',
        body: JSON.stringify({ latest: true })
      });
      toast('latest 已指向 ' + version);
    } else if (action === 'delver') {
      if (!confirm('删除 ' + name + '@' + version + ' 及其制品？此操作不可恢复。')) return;
      await request('/packages/' + encodeURIComponent(name) + '/' + encodeURIComponent(version), {
        method: 'DELETE'
      });
      toast('已删除 ' + version);
    }
    await Promise.all([loadDetail(name), loadPackages(), loadStats()]);
  } catch (err) {
    toast(err.message, false);
  }
}

async function deletePackage() {
  if (!getToken()) {
    toast('先保存 Cdifit AccessToken', false);
    return;
  }
  const name = current && current.name;
  if (!name) return;
  if (!confirm('删除整个包 ' + name + '（全部版本与制品）？此操作不可恢复。')) return;
  try {
    await request('/packages/' + encodeURIComponent(name), { method: 'DELETE' });
    toast('已删除 ' + name);
    closeDetailView();
    await Promise.all([loadPackages(), loadStats()]);
  } catch (err) {
    toast(err.message, false);
  }
}

/* ---------- 事件绑定 ---------- */
$('auth-form').addEventListener('submit', (event) => {
  event.preventDefault();
  saveToken($('token').value);
  toast(getToken() ? '令牌已保存' : '令牌已清除');
  updateWho();
  loadStats();
  loadPackages();
  if (current) loadDetail(current.name);
});

$('clear-token').addEventListener('click', () => {
  saveToken('');
  toast('令牌已清除');
  updateWho();
});

$('search').addEventListener('click', loadPackages);
$('sort').addEventListener('change', loadPackages);
$('order').addEventListener('change', loadPackages);

$('q').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    loadPackages();
  }
});

$('q').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadPackages, 350);
});

$('packages').addEventListener('click', (event) => {
  const target = event.target.closest('.pkg');
  if (target) selectPackage(target.dataset.name);
});

$('detail').addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.hasAttribute('data-back')) {
    closeDetailView();
    return;
  }
  if (button.hasAttribute('data-delpkg')) {
    deletePackage();
    return;
  }
  if (button.dataset.act) {
    versionAction(button.dataset.act, button.dataset.v, button.dataset.y === '1');
  }
});

$('refresh').addEventListener('click', async () => {
  updateWho();
  await Promise.all([
    loadStats(),
    loadPackages(),
    current ? loadDetail(current.name) : Promise.resolve()
  ]);
  toast('已刷新');
});

/* ---------- 初始化 ---------- */
(function init() {
  try {
    const hash = new URLSearchParams(location.hash.slice(1));
    const query = new URLSearchParams(location.search);
    const incoming = hash.get('token') || query.get('token');
    if (incoming) {
      saveToken(incoming);
      history.replaceState(null, '', location.pathname);
    }
  } catch (err) {
    /* 忽略 file:// 等场景下的 history 限制 */
  }
  restoreToken();
  $('token').value = getToken();
  if (getToken() && isMobile()) $('auth').open = false;
  updateWho();
  loadStats();
  loadPackages();
})();
