# package.codecin.org.cn —— pic 中央注册/分发服务器

这是 `pic` 的独立中央服务（当前版本 `6.4.2`），负责：

* 包标识名 / Git 获取地址；
* 版本列表、标签/提交、发布者、时间、latest 指针；
* 展示与搜索元数据（描述、关键词、许可证、主页、入口、模块清单）；
* `.cinpkg` 制品上传与下载（含下载计数）；
* 源码树 / 规范化清单 / 制品的 SHA-256；
* 搜索（名称/仓库/描述/关键词、排序、分页）、yank、版本删除；
* 浏览器后台管理页 `/admin`。

**Cdifit (`https://www.cdifit.cn`) 只用于登录和 AccessToken 校验，不存储包数据。**

服务端使用 Python 标准库的 `http.server`（不依赖 Flask），终端输出与日志由
[rich](https://github.com/Textualize/rich) 渲染。

## 环境要求

* Python 3.10+
* `rich`（见 `requirements.txt`）

## 安装与运行

```bash
cd registry
python -m venv .venv
# Linux / macOS
.venv/bin/pip install -r requirements.txt
# Windows
# .venv\Scripts\pip install -r requirements.txt

PIC_SERVER_ADDR=:18080 \
PIC_SERVER_DATA=/var/lib/pic-registry \
PIC_PUBLIC_URL=https://package.codecin.org.cn \
CDIFIT_API_BASE=https://www.cdifit.cn \
.venv/bin/python server.py
```

启动后前端随服务一起提供，无需单独启动前端进程：后台界面在
`http://127.0.0.1:18080/`（或 `/admin`），API 在
`http://127.0.0.1:18080/api/v1`，二者共用同一端口。

## 环境变量

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `PIC_SERVER_ADDR` | `:18080` | 监听地址 |
| `PIC_SERVER_DATA` | `./data` | 数据目录 |
| `PIC_PUBLIC_URL` | `https://package.codecin.org.cn` | 对外地址（用于 artifact_url） |
| `CDIFIT_API_BASE` | `https://www.cdifit.cn` | Cdifit 认证地址 |
| `PIC_ADMIN_EMAILS` | 空 | 管理员邮箱，逗号/空白分隔 |
| `PIC_ADMIN_IDS` | 空 | 管理员 Cdifit `sub`，逗号/空白分隔 |

管理员按邮箱或 `sub` 任一匹配；未配置管理员时，登录用户仍可管理自己发布的版本。

## 数据目录

```text
data/
├── packages/<name>.json                 # 包记录/版本列表/展示字段/latest/downloads
├── artifacts/<name>/<version>.cinpkg    # 分发制品
└── tmp/                                 # 上传临时文件（可随时清理）
```

包记录使用「临时文件 + rename」原子写入，读取时旧记录缺少新字段会按默认值处理；
制品上传成功后才从 `tmp/` 移动到 `artifacts/`，失败不会留下半成品。

## 测试

```bash
cd registry
python -m pytest test_server.py -q
```

测试使用假的认证器，不访问 Cdifit，也不需要网络。

## API

Base: `/api/v1`

### 通用约定

* 成功响应：`{"ok": true, "data": ...}`；失败响应：
  `{"ok": false, "error": {"code": "...", "message": "..."}}`，HTTP 状态码与 `code` 对应。
* 认证：`Authorization: Bearer <Cdifit AccessToken>`；只读接口无需认证。
* 名称规则 `^[A-Za-z][A-Za-z0-9_-]{0,63}$`；版本规则
  `^\d+\.\d+\.\d+([-+][0-9A-Za-z.+-]+)?$`；SHA-256 为 64 位小写十六进制。
* 请求体上限：元数据 1 MiB、制品 256 MiB，超限返回 `400`。
* `HEAD` 与 `GET` 使用同一套路由且不返回响应体；未实现的方法返回 `405`，
  并在 `Allow` 头列出允许的方法。
* JSON 接口统一 `Cache-Control: no-store`。
* 版本不可变：同版本同内容重复提交是幂等的，不同内容返回 `409`。

### 端点

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/health` | 否 | 健康检查（`status`/`version`/`time`/`uptime`） |
| GET | `/stats` | 否 | 统计（`/api/v1/admin/stats` 为同义别名） |
| GET | `/me` | Bearer | 当前用户（`sub`/`name`/`email`/`nickname`/`admin`） |
| GET | `/packages?q=&keyword=&sort=&order=&limit=&offset=` | 否 | 搜索/列表 |
| GET | `/packages/{name}` | 否 | 包记录（含全部版本） |
| GET | `/packages/{name}/{version}` | 否 | 单版本 |
| PUT | `/packages/{name}/{version}` | Bearer | 提交版本元数据（201 新建 / 200 幂等） |
| POST | `/packages/{name}/{version}/artifact?sha256=` | Bearer | 上传 `.cinpkg`（`sha256` 可省略） |
| GET | `/packages/{name}/{version}/artifact` | 否 | 下载 `.cinpkg`（支持 Range/ETag） |
| POST | `/packages/{name}/{version}/yank` | Bearer | 撤回/恢复版本 |
| POST | `/packages/{name}/{version}/latest` | Bearer | 设置/清除 latest 指针 |
| DELETE | `/packages/{name}/{version}` | Bearer | 删除版本 |
| DELETE | `/packages/{name}` | Bearer | 删除整个包 |

### 搜索与分页

| 参数 | 默认 | 说明 |
|------|------|------|
| `q` | 空 | 关键词；按 名称精确 > 名称前缀 > 名称包含 > keywords > 描述 > 仓库 的相关度匹配 |
| `keyword` | 空 | 只匹配 `keywords` 字段（包含即命中） |
| `sort` | 空 | `name` / `latest` / `updated` / `downloads` / `versions`；空 = 有 `q` 按相关度、无 `q` 按名称 |
| `order` | 空 | `asc` / `desc`；`name` 默认升序，其余排序默认降序 |
| `limit` | `0` | 每页条数；`0` 表示返回全部，非 0 时上限 200 |
| `offset` | `0` | 起始偏移 |

返回 `packages`（摘要数组）、`count`、`total`、`offset`、`limit`。摘要字段：
`name`、`repo`、`latest`、`versions`、`updated_at`、`description`、`keywords`、`downloads`。

### 发布元数据

`PUT /api/v1/packages/{name}/{version}`，`Content-Type: application/json`：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 否 | 与 URL 一致；给出时不一致返回 `400` |
| `version` | string | 否 | 与 URL 一致；给出时不一致返回 `400` |
| `repo` | string | 是 | 包绑定的 Git 仓库；首次发布后不可更改（`409 repo_mismatch`） |
| `tag` | string | 否 | 标签 |
| `rev` | string | 否 | 提交哈希 |
| `source_sha256` | string | 是 | 源码树摘要 |
| `manifest_sha256` | string | 是 | 规范化清单摘要 |
| `archive_sha256` | string | 否 | 制品摘要；上传制品时用于校验 |
| `archive_size` | int | 否 | 制品字节数；上传时校验 |
| `description` | string | 否 | 包描述，≤ 4000 字符 |
| `keywords` | string[] | 否 | 最多 32 个，单个 ≤ 40 字符 |
| `license` | string | 否 | 许可证标识，≤ 200 字符 |
| `homepage` | string | 否 | 项目主页，≤ 500 字符 |
| `entry` | string | 否 | 包入口文件，≤ 500 字符 |
| `modules` | string[] | 否 | 公开模块清单，最多保留 200 个 |

未知字段直接返回 `400`（`json: unknown field "..."`）。示例：

```http
PUT /api/v1/packages/hello/1.0.0
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "hello",
  "version": "1.0.0",
  "repo": "https://github.com/you/cin-hello",
  "tag": "v1.0.0",
  "rev": "<commit>",
  "source_sha256": "<64 hex>",
  "manifest_sha256": "<64 hex>",
  "archive_sha256": "<64 hex>",
  "archive_size": 12345,
  "description": "Hello world example package",
  "keywords": ["hello", "example"],
  "license": "MIT",
  "homepage": "https://github.com/you/cin-hello",
  "entry": "src/hello.cin",
  "modules": ["hello", "hello/util"]
}
```

响应 `data`：`name`、`version`、`created`、`publisher`。

### 上传制品

```http
POST /api/v1/packages/hello/1.0.0/artifact?sha256=<archive_sha256>
Authorization: Bearer <token>
Content-Type: application/octet-stream

<binary .cinpkg>
```

* `sha256` 查询参数可省略：缺省时使用发布元数据里的 `archive_sha256`；两者都没有返回
  `400 missing_sha256`。
* 仅该版本的发布者可以上传（管理员也不能代替；老数据没有发布者时除外）。
* 服务端重新计算 SHA-256，不一致返回 `400 checksum_mismatch`；声明了
  `archive_size` 时大小不一致返回 `400 size_mismatch`。
* 最大 256 MiB。成功后返回 `sha256`、`size` 与 `url`。

### 下载制品

`GET /api/v1/packages/{name}/{version}/artifact`（无需认证）：

* 响应头包含 `ETag`、`Last-Modified`、`X-PIC-SHA256`、`Content-Disposition`；
* 带 `If-None-Match` 且匹配时返回 `304`；
* 带 `Range: bytes=...` 时返回 `206` 与 `Content-Range`，支持断点续传；
* 制品尚未上传时返回 `404 artifact_not_found`；
* `yank` 不删除制品，按版本号仍可下载。

### latest、yank 与删除

* `POST .../latest`，JSON `{"latest": true}`（或省略 body）把该版本设为 latest；
  `{"latest": false}` 清空指针。latest 版本不能是已撤回版本。
* 显式 latest 优先；没有显式指针（或指针已失效）时，`latest` 取最高的未撤回版本。
* `POST .../yank`，JSON `{"yanked": true|false}`（省略 body 视为 `true`）。yank 只影响
  latest 与展示，不删除制品。
* `DELETE /packages/{name}/{version}` 删除单版本及其制品；删掉最后一个版本时包记录和
  制品目录一并删除。
* `DELETE /packages/{name}` 删除整个包：要求该包所有已记录发布者的版本都属于当前用户
  （或管理员）；没有发布者信息的老包允许任意登录用户删除。

### 错误码

| HTTP | code | 含义 |
|------|------|------|
| 400 | `invalid_name` / `invalid_version` | 名称/版本不符合规则 |
| 400 | `invalid_json` | JSON 非法、字段类型不对或包含未知字段 |
| 400 | `invalid_query` | 查询参数非法（如 `limit` 为负） |
| 400 | `missing_sha256` / `checksum_mismatch` / `size_mismatch` | 制品摘要或大小不匹配 |
| 400 | `publish_failed` / `delete_failed` / `latest_failed` | 存储层拒绝该操作 |
| 401 | `unauthorized` | 缺少、无效或过期的 Token |
| 403 | `forbidden` | 不是发布者/管理员 |
| 404 | `not_found` / `artifact_not_found` | 包、版本或制品不存在 |
| 405 | `method_not_allowed` | 方法不支持（响应带 `Allow`） |
| 409 | `version_exists` | 同版本内容不同（版本不可变） |
| 409 | `repo_mismatch` | 包已绑定到另一个 Git 仓库 |
| 502 | `auth_unavailable` | Cdifit 认证服务不可用 |
| 500 | `store_error` / `internal` | 服务端错误 |

## 认证

`Authorization: Bearer <Cdifit AccessToken>`。服务端把 Token 转发到
`GET https://www.cdifit.cn/api/v4/session/oauth/userinfo` 校验：

* 成功结果缓存 5 分钟；失败结果（未授权/上游故障）只缓存 30 秒，避免长时间误伤。
* Cdifit 对无效 Token 可能返回 HTTP 200 + `code=401`，服务端同样按 `401` 处理。
* 上游不可用或返回异常时返回 `502 auth_unavailable`，不会把用户当成未登录。
* 发布者由返回的 `sub` 决定；`PIC_ADMIN_EMAILS` / `PIC_ADMIN_IDS` 命中的用户是管理员。

## 后台管理

打开 `https://package.codecin.org.cn/` 或 `/admin`，或在客户端运行 `pic admin`
（自动打开并填入令牌）。后台页面无需登录即可打开，但所有操作都需要在页面里填入
`pic admin --print-token` 打印的 AccessToken。

后台界面是 [admin-ui/](admin-ui/README.md) 下的标准 Node.js 静态前端项目：HTML / CSS /
JS 分离，`config.json` 在运行时动态加载。`server.py` 启动时把前端和 API 一起提供在同一
端口，不需要额外运行前端服务。页面启用了 CSP（禁止内联脚本，样式允许内联），静态资源
统一 `Cache-Control: no-store`。

`admin-ui/config.json` 字段：

| 字段 | 说明 |
|------|------|
| `apiBase` | 浏览器请求前缀，可写相对路径（同源部署）或完整 URL |
| `apiServer` | 本地开发时 `/api/*` 的代理目标 |
| `devPort` | 本地开发服务器端口（默认 5173） |

本地开发：`cd admin-ui && npm start`，打开 `http://127.0.0.1:5173/admin/`。

后台支持：统计概览（包/版本/制品/已撤回/下载/存储）；按名称/最新版本/更新时间/
下载量/版本数排序；查看版本、标签、提交、制品大小/SHA-256、发布者、下载量；
撤回/恢复版本、设置 latest、删除版本、删除整个包。

## Docker

```bash
docker build -t pic-registry .
docker run -d --name pic-registry \
  -p 0.0.0.0:18080:18080 \
  -v /var/lib/pic-registry:/data \
  -e PIC_PUBLIC_URL=https://package.codecin.org.cn \
  -e CDIFIT_API_BASE=https://www.cdifit.cn \
  pic-registry
```

镜像内置 `HEALTHCHECK`，探测 `GET /api/v1/health`；也可手动检查：

```bash
curl -fsS http://127.0.0.1:18080/api/v1/health
```

## 反向代理（nginx）

完整示例见 [deploy/nginx.conf](deploy/nginx.conf)（含 80→443 跳转、HTTP/1.1 与超时设置）：

```nginx
server {
    listen 443 ssl http2;
    server_name package.codecin.org.cn;

    ssl_certificate     /etc/letsencrypt/live/package.codecin.org.cn/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/package.codecin.org.cn/privkey.pem;

    client_max_body_size 300m;

    location / {
        proxy_pass http://127.0.0.1:18080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}

server {
    listen 80;
    server_name package.codecin.org.cn;
    return 301 https://$host$request_uri;
}
```

## systemd

见 `deploy/pic-registry.service`：

```bash
sudo useradd --system --home /var/lib/pic-registry --shell /usr/sbin/nologin pic
sudo install -d -o pic -g pic /var/lib/pic-registry
sudo install -d /opt/pic-registry
sudo cp -r server.py store.py auth.py version.py requirements.txt admin-ui /opt/pic-registry/
sudo python3 -m venv /opt/pic-registry/venv
sudo /opt/pic-registry/venv/bin/pip install -r /opt/pic-registry/requirements.txt
sudo install -m 0644 deploy/pic-registry.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pic-registry
curl https://package.codecin.org.cn/api/v1/health
```

## 备份

包记录和制品都是普通文件，直接备份 `PIC_SERVER_DATA` 即可。`tmp/` 只是上传临时文件，
可以排除：

```bash
tar czf pic-registry-$(date +%F).tar.gz \
  --exclude='pic-registry/tmp' -C /var/lib pic-registry
```

包记录是原子写入的，但制品上传是「临时文件 → rename」。若要求严格一致的快照，建议在
低峰期执行，或短暂停服后再打包。

## 安全

- 只允许 HTTPS 对外（TLS 在 nginx/负载均衡终止）；
- 写操作必须携带 Cdifit Token，发布者由 Token 的 `sub` 绑定；
- 版本不可变；yank/删除只能由发布者或管理员操作；
- 制品上传/下载双向校验 SHA-256；
- 请求体限制：元数据 1 MiB、制品 256 MiB；
- 名称/版本/摘要均做正则校验，防止路径穿越；
- 后台页面使用 CSP、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`，
  接口与页面均 `no-store`，不缓存敏感响应。
