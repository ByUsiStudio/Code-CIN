/**
 * 注册表 API 客户端：统一处理 Token、错误与 config.json 中的 apiBase。
 */
import { loadConfig } from './config.js';

const TOKEN_KEY = 'pic_admin_token';

let token = '';

export function getToken() {
  return token;
}

export function restoreToken() {
  token = localStorage.getItem(TOKEN_KEY) || '';
  return token;
}

export function saveToken(value) {
  token = String(value || '').trim();
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
  return token;
}

export async function request(path, options = {}) {
  const { apiBase } = await loadConfig();
  const headers = new Headers(options.headers || {});
  headers.set('Accept', 'application/json');
  if (token) headers.set('Authorization', 'Bearer ' + token);
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const resp = await fetch(apiBase + path, { ...options, headers, cache: 'no-store' });
  let data = {};
  try {
    data = await resp.json();
  } catch (err) {
    data = {};
  }
  if (!resp.ok || data.ok === false) {
    const message = (data.error && data.error.message) || ('HTTP ' + resp.status);
    throw new Error(message);
  }
  return data.data;
}
