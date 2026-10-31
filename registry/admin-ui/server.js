/**
 * 本地开发服务器：零依赖，负责静态文件与 /api 代理。
 * 每次请求都会重新读取 config.json，修改服务器地址无需重启。
 */
import http from 'node:http';
import https from 'node:https';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const CONFIG_PATH = path.join(ROOT, 'config.json');
const ADMIN_PREFIX = '/admin/';
const ASSET_RE = /^(?:css\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+\.css|js\/(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_.-]+\.js|config\.json)$/;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2'
};

function readConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  } catch (err) {
    return {};
  }
}

function send(res, status, body, type = 'text/plain; charset=utf-8', headers = {}) {
  const raw = Buffer.isBuffer(body) ? body : Buffer.from(String(body));
  res.writeHead(status, {
    'Content-Type': type,
    'Content-Length': raw.length,
    'Cache-Control': 'no-store',
    ...headers
  });
  res.end(raw);
}

function serveFile(res, relative) {
  if (!ASSET_RE.test(relative)) return send(res, 404, 'not found');
  const target = path.join(ROOT, relative);
  if (!target.startsWith(ROOT + path.sep) && target !== ROOT) {
    return send(res, 404, 'not found');
  }
  const ext = path.extname(target).toLowerCase();
  if (!MIME[ext]) return send(res, 404, 'not found');
  fs.readFile(target, (err, data) => {
    if (err) return send(res, 404, 'not found');
    send(res, 200, data, MIME[ext]);
  });
}

function proxy(req, res, config) {
  const server = String(config.apiServer || '');
  if (!server) return send(res, 502, 'config.json 未配置 apiServer', 'text/plain; charset=utf-8');
  let upstream;
  try {
    upstream = new URL(server.replace(/\/+$/, '') + req.url);
  } catch (err) {
    return send(res, 502, 'config.json 中的 apiServer 无效');
  }
  const client = upstream.protocol === 'https:' ? https : http;
  const headers = { ...req.headers, host: upstream.host };
  const request = client.request({
    protocol: upstream.protocol,
    hostname: upstream.hostname,
    port: upstream.port,
    path: upstream.pathname + upstream.search,
    method: req.method,
    headers
  }, (upstreamRes) => {
    res.writeHead(upstreamRes.statusCode || 502, upstreamRes.headers);
    upstreamRes.pipe(res);
  });
  request.on('error', (err) => {
    send(res, 502, '代理请求失败：' + err.message);
  });
  req.pipe(request);
}

const server = http.createServer((req, res) => {
  const config = readConfig();
  const url = new URL(req.url, 'http://localhost');

  if (url.pathname.startsWith('/api/')) {
    proxy(req, res, config);
    return;
  }

  if (!['GET', 'HEAD'].includes(req.method)) {
    send(res, 405, 'method not allowed', 'text/plain; charset=utf-8', { Allow: 'GET, HEAD' });
    return;
  }

  if (url.pathname === '/' || url.pathname === '/admin' || url.pathname === '/admin/index.html') {
    res.writeHead(302, { Location: ADMIN_PREFIX, 'Cache-Control': 'no-store' });
    res.end();
    return;
  }

  if (url.pathname === ADMIN_PREFIX) {
    res.writeHead(200, { 'Content-Type': MIME['.html'], 'Cache-Control': 'no-store' });
    fs.createReadStream(path.join(ROOT, 'index.html')).pipe(res);
    return;
  }

  if (url.pathname.startsWith(ADMIN_PREFIX)) {
    serveFile(res, url.pathname.slice(ADMIN_PREFIX.length));
    return;
  }

  send(res, 404, 'not found');
});

const { devPort } = readConfig();
const port = Number(devPort) || Number(process.env.PORT) || 5173;
server.listen(port, () => {
  const config = readConfig();
  console.log(`pic admin ui: http://127.0.0.1:${port}/admin/`);
  console.log(`api proxy   : ${config.apiServer || '(未配置 apiServer)'}`);
});
