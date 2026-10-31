# pic-admin-ui

pic registry 后台管理界面。标准 Node.js 静态前端项目，零运行时依赖，
由 `registry/server.py` 挂在 `/admin` 下提供，也可独立启动本地开发服务器。

## 目录结构

```text
admin-ui/
├── index.html          # 页面骨架（不含内联样式/脚本）
├── css/
│   └── styles.css      # 全部样式，rem / clamp / % 相对布局，移动端适配
├── js/
│   ├── app.js          # 页面入口：状态与事件
│   ├── api.js          # API 客户端（Token、错误处理）
│   ├── config.js       # 运行时读取 config.json
│   └── render.js       # 纯展示层，生成 HTML 字符串
├── config.json         # 服务器地址与开发端口（运行时动态加载）
├── server.js           # 本地开发服务器（静态文件 + /api 代理）
└── package.json
```

## config.json

```json
{
  "apiBase": "/api/v1",
  "apiServer": "https://package.codecin.org.cn",
  "devPort": 5173
}
```

| 字段 | 说明 |
|------|------|
| `apiBase` | 浏览器请求前缀，可写相对路径（同源部署）或完整 URL（跨域部署） |
| `apiServer` | 本地开发时 `/api/*` 的代理目标；本地联调可改成 `http://127.0.0.1:18080` |
| `devPort` | 本地开发服务器端口 |

浏览器在启动时请求 `config.json`，修改后刷新页面即可生效；
开发服务器（`server.js`）每次请求重新读取，修改后无需重启。

## 本地开发

```bash
cd registry/admin-ui
npm start          # 或 node server.js
# 打开 http://127.0.0.1:5173/admin/
```

本地开发服务器会把 `/api/*` 代理到 `config.json` 的 `apiServer`，
因此无需在 registry 上额外配置 CORS。

## 生产部署

由 `registry/server.py` 提供：`/admin` 返回 `index.html`（注入
`<base href="/admin/">`），`/admin/css/*`、`/admin/js/*`、`/admin/config.json`
由服务端直接读取本目录下的文件；只需把整个 `admin-ui/` 目录随服务端一起部署。
