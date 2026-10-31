/**
 * 运行时配置：页面加载后动态请求 config.json，修改服务器地址无需重新打包页面。
 */
const DEFAULT_CONFIG = Object.freeze({
  apiBase: '/api/v1',
  apiServer: '',
  devPort: 0
});

let config = null;
let pending = null;

function normalize(data) {
  const raw = data && typeof data === 'object' ? data : {};
  return {
    apiBase: String(raw.apiBase || DEFAULT_CONFIG.apiBase).replace(/\/+$/, ''),
    apiServer: String(raw.apiServer || ''),
    devPort: Number(raw.devPort) || 0
  };
}

async function fetchConfig() {
  try {
    const res = await fetch('config.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return normalize(await res.json());
  } catch (err) {
    return { ...DEFAULT_CONFIG };
  }
}

export async function loadConfig() {
  if (config) return config;
  if (!pending) pending = fetchConfig();
  config = await pending;
  return config;
}

export async function getApiBase() {
  return (await loadConfig()).apiBase;
}
