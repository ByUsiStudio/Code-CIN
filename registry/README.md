# package.codecin.org.cn —— pic 中央注册/分发服务器

这是 `pic` 的独立中央服务，负责：

* 包标识名 / Git 获取地址；
* 版本列表、标签/提交、发布者、时间；
* `.cinpkg` 制品上传与下载；
* 源码树 / 规范化清单 / 制品的 SHA-256；
* yank（撤回版本）、搜索、健康检查。

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

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `PIC_SERVER_ADDR` | `:18080` | 监听地址 |
| `PIC_SERVER_DATA` | `./data` | 数据目录 |
| `PIC_PUBLIC_URL` | `https://package.codecin.org.cn` | 对外地址（用于 artifact_url） |
| `CDIFIT_API_BASE` | `https://www.cdifit.cn` | Cdifit 认证地址 |

数据目录：

```text
data/
├── packages/<name>.json                 # 包记录/版本列表
├── artifacts/<name>/<version>.cinpkg    # 分发制品
└── tmp/                                 # 上传临时文件
```

## 测试

```bash
cd registry
python -m pytest test_server.py -q
```

## API

Base: `/api/v1`

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/health` | 否 | 健康检查 |
| GET | `/packages?q=` | 否 | 搜索/列表 |
| GET | `/packages/{name}` | 否 | 包记录 |
| GET | `/packages/{name}/{version}` | 否 | 单版本 |
| PUT | `/packages/{name}/{version}` | Bearer | 提交元数据 |
| POST | `/packages/{name}/{version}/artifact?sha256=` | Bearer | 上传 `.cinpkg` |
| GET | `/packages/{name}/{version}/artifact` | 否 | 下载 `.cinpkg` |
| POST | `/packages/{name}/{version}/yank` | Bearer | 撤回/恢复版本 |

认证：`Authorization: Bearer <Cdifit AccessToken>`。服务端把 Token 转发到
`POST https://www.cdifit.cn/api/v4/session/oauth/userinfo` 校验，并缓存
5 分钟；发布者由返回的 `sub` 决定。

### 发布元数据示例

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
  "archive_size": 12345
}
```

同版本同内容重复提交是幂等的；同版本不同内容返回 `409`（版本不可变）。

### 上传制品

```http
POST /api/v1/packages/hello/1.0.0/artifact?sha256=<archive_sha256>
Authorization: Bearer <token>
Content-Type: application/octet-stream

<binary .cinpkg>
```

服务端重新计算 SHA-256 并比对，最大 256 MiB。

## Docker

```bash
docker build -t pic-registry .
docker run -d --name pic-registry \
  -p 0.0.0.0:28080:18080 \
  -v /var/lib/pic-registry:/data \
  -e PIC_PUBLIC_URL=https://package.codecin.org.cn \
  -e CDIFIT_API_BASE=https://www.cdifit.cn \
  pic-registry
```

## 反向代理（nginx）

```nginx
server {
    listen 443 ssl http2;
    server_name package.codecin.org.cn;

    ssl_certificate     /etc/letsencrypt/live/package.codecin.org.cn/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/package.codecin.org.cn/privkey.pem;

    client_max_body_size 300m;

    location / {
        proxy_pass http://127.0.0.1:18080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}
```

## systemd

见 `deploy/pic-registry.service`：

```bash
sudo useradd --system --home /var/lib/pic-registry --shell /usr/sbin/nologin pic
sudo install -d -o pic -g pic /var/lib/pic-registry
sudo install -d /opt/pic-registry
sudo cp server.py store.py auth.py version.py requirements.txt /opt/pic-registry/
sudo python3 -m venv /opt/pic-registry/venv
sudo /opt/pic-registry/venv/bin/pip install -r /opt/pic-registry/requirements.txt
sudo install -m 0644 deploy/pic-registry.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pic-registry
curl https://package.codecin.org.cn/api/v1/health
```

## 备份

包记录和制品都是普通文件，直接备份 `PIC_SERVER_DATA` 即可：

```bash
tar czf pic-registry-$(date +%F).tar.gz -C /var/lib pic-registry
```

## 安全

- 只允许 HTTPS 对外（TLS 在 nginx/负载均衡终止）；
- 写操作必须携带 Cdifit Token，发布者由 Token 的 `sub` 绑定；
- 版本不可变；yank 只能由发布者操作；
- 制品上传/下载双向校验 SHA-256；
- 最大请求体限制：元数据 1 MiB、制品 256 MiB；
- 文件名/版本/摘要均做正则校验，防止路径穿越。
