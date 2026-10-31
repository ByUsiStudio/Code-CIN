/**
 * 纯展示层：只生成 HTML 字符串，不直接操作 DOM，便于复用与测试。
 */

const ESCAPES = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
};

export function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

export function safeUrl(value) {
  if (!value) return '';
  try {
    const url = new URL(String(value), location.href);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : '';
  } catch (err) {
    return '';
  }
}

export function bytes(size) {
  let value = Number(size || 0);
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return (index ? value.toFixed(1) : String(value)) + ' ' + units[index];
}

export function formatDate(value) {
  if (!value || String(value).startsWith('0001')) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function statsMarkup(stats) {
  const items = [
    ['包', stats.packages],
    ['版本', stats.versions],
    ['制品', stats.artifacts],
    ['已撤回', stats.yanked],
    ['下载', stats.downloads],
    ['存储', bytes(stats.storage_bytes)]
  ];
  return items.map(([label, value]) => (
    `<div class="stat"><div class="k">${esc(label)}</div><div class="v">${esc(value)}</div></div>`
  )).join('');
}

export function countText(total, shown) {
  const suffix = total > shown ? ` · 显示前 ${shown} 个` : '';
  return `共 ${total} 个包${suffix}`;
}

export function stateMarkup(title, message) {
  return '<div class="state">'
    + `<span class="big">${esc(title)}</span>`
    + (message ? `<span>${esc(message)}</span>` : '')
    + '</div>';
}

export function packagesMarkup(packages, activeName) {
  if (!packages.length) {
    return stateMarkup('没有匹配的包', '换个关键字试试');
  }
  return packages.map((pkg) => {
    const badge = pkg.latest
      ? `<span class="pill ok">${esc(pkg.latest)}</span>`
      : '<span class="pill warn">无可用版本</span>';
    const active = pkg.name === activeName ? ' is-active' : '';
    return `<button type="button" class="pkg${active}" data-name="${esc(pkg.name)}" aria-label="查看 ${esc(pkg.name)}">`
      + '<span class="pkg-main">'
      +   `<span class="pkg-name">${esc(pkg.name)}</span>`
      +   (pkg.description ? `<span class="pkg-desc">${esc(pkg.description)}</span>` : '')
      + '</span>'
      + '<span class="pkg-meta">'
      +   badge
      +   `<span class="meta-item"><b>${esc(pkg.versions)}</b> 版本</span>`
      +   `<span class="meta-item"><b>${esc(pkg.downloads || 0)}</b> 下载</span>`
      +   `<span class="meta-item">${esc(formatDate(pkg.updated_at))}</span>`
      + '</span>'
      + '</button>';
  }).join('');
}

function versionMarkup(version, entry, latest) {
  const yanked = !!entry.yanked;
  const isLatest = latest === version;
  return '<article class="ver">'
    + '<div class="ver-head">'
    +   `<b>${esc(version)}</b>`
    +   (isLatest ? '<span class="pill ok">latest</span>' : '')
    +   (yanked ? '<span class="pill bad">已撤回</span>' : '')
    + '</div>'
    + '<div class="ver-grid">'
    +   '<div class="cell"><span class="k">标签 / 提交</span>'
    +     `<span class="v">${esc(entry.tag || '-')}</span>`
    +     `<span class="sub">${esc((entry.rev || '').slice(0, 12) || '-')}</span></div>`
    +   '<div class="cell"><span class="k">制品</span>'
    +     `<span class="v">${esc(bytes(entry.archive_size))}</span>`
    +     `<span class="sub">${esc((entry.archive_sha256 || '').slice(0, 12) || '无制品')}</span></div>`
    +   '<div class="cell"><span class="k">下载</span>'
    +     `<span class="v">${esc(entry.downloads || 0)}</span></div>`
    +   '<div class="cell"><span class="k">发布</span>'
    +     `<span class="v">${esc(formatDate(entry.published_at))}</span>`
    +     `<span class="sub">${esc((entry.publisher && (entry.publisher.email || entry.publisher.nickname)) || '-')}</span></div>`
    + '</div>'
    + '<div class="ver-actions">'
    +   `<button type="button" data-act="yank" data-v="${esc(version)}" data-y="${yanked ? '0' : '1'}">`
    +     `${yanked ? '恢复' : '撤回'}</button>`
    +   `<button type="button" data-act="latest" data-v="${esc(version)}"${isLatest ? ' disabled' : ''}>`
    +     '设为 latest</button>'
    +   `<button type="button" class="danger" data-act="delver" data-v="${esc(version)}">删除</button>`
    + '</div>'
    + '</article>';
}

export function detailMarkup(pkg) {
  const versions = Object.keys(pkg.versions || {})
    .sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
  const repoUrl = safeUrl(pkg.repo);
  const homeUrl = safeUrl(pkg.homepage);
  const rows = versions
    .map((version) => versionMarkup(version, pkg.versions[version] || {}, pkg.latest))
    .join('');

  const latest = pkg.latest
    ? `<span class="pill ok">${esc(pkg.latest)}</span>`
    : '<span class="pill warn">无</span>';
  const repo = repoUrl
    ? `<a href="${esc(repoUrl)}" target="_blank" rel="noreferrer">${esc(pkg.repo)}</a>`
    : '-';
  const homepage = homeUrl
    ? `<a href="${esc(homeUrl)}" target="_blank" rel="noreferrer">${esc(pkg.homepage)}</a>`
    : esc(pkg.homepage || '-');

  return '<div class="detail-top">'
    +   '<button type="button" class="ghost back" data-back>← 返回</button>'
    +   '<span class="grow"></span>'
    +   '<button type="button" class="danger" data-delpkg>删除整个包</button>'
    + '</div>'
    + '<div>'
    +   `<div class="detail-title">${esc(pkg.name)}</div>`
    +   `<div class="detail-sub">${esc(pkg.downloads || 0)} 下载 · ${esc(versions.length)} 个版本`
    +     ` · 更新于 ${esc(formatDate(pkg.updated_at))}</div>`
    + '</div>'
    + '<dl class="kv">'
    +   `<dt>最新</dt><dd>${latest}</dd>`
    +   `<dt>Git 仓库</dt><dd>${repo}</dd>`
    +   `<dt>描述</dt><dd>${esc(pkg.description || '-')}</dd>`
    +   `<dt>关键词</dt><dd>${esc((pkg.keywords || []).join(', ') || '-')}</dd>`
    +   `<dt>许可证</dt><dd>${esc(pkg.license || '-')}</dd>`
    +   `<dt>主页</dt><dd>${homepage}</dd>`
    +   `<dt>入口</dt><dd>${esc(pkg.entry || '-')}</dd>`
    +   `<dt>模块</dt><dd>${esc((pkg.modules || []).join(', ') || '-')}</dd>`
    +   `<dt>更新时间</dt><dd>${esc(formatDate(pkg.updated_at))}</dd>`
    + '</dl>'
    + `<h4>版本 <span class="muted small">(${esc(versions.length)})</span></h4>`
    + (rows ? `<div class="versions">${rows}</div>` : stateMarkup('暂无版本'));
}

export function placeholderMarkup() {
  return '<div class="state">'
    + '<span class="big">选择一个包</span>'
    + '<span>点击列表中的包名，查看版本、发布者与制品信息</span>'
    + '</div>';
}
