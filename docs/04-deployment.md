# 04 · 部署

把 WebUI 以 systemd 服务部署到你的服务器（与 AstrBot / EverOS 同机），绑 `127.0.0.1`、经 SSH 隧道访问。下文用 `youruser` 代指你的服务器用户名、`/path/to/...` 代指你的实际路径，请替换为你自己的值。

## 前置

- 服务器（Linux）已跑着 AstrBot（含 `astrbot_plugin_readingsteiner` 插件）与 EverOS。
- Python 3.12（建议与 EverOS 同版本）；需 `python3.12-venv` 包（缺则 `sudo apt install -y python3.12-venv`）。
- Node（构建前端用）。

## 1. 拉代码 + 建 venv

```bash
git clone <this-repo-url> ~/readingsteiner-webui
cd ~/readingsteiner-webui/backend
python3.12 -m venv .venv
.venv/bin/pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
```

## 2. 构建前端静态产物

```bash
cd ~/readingsteiner-webui/frontend
npm ci
npm run build          # 产物在 frontend/build/，由 FastAPI 同源托管，无需另起 web 服务器
```

## 3. 写 .env

```bash
cd ~/readingsteiner-webui
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
# 生成强随机密钥（绝不进 git）：
python3 -c "import secrets;print('WEBUI_AUTH_SECRET='+secrets.token_urlsafe(48))"
```

编辑 `deploy/.env`，关键项：

- `WEBUI_AUTH_SECRET=` —— 填上一步生成的强随机值。
- `EVEROS_GATEWAY=http`、`EVEROS_BASE_URL=http://127.0.0.1:8596` —— 连真 EverOS。
- `PLUGIN_CONFIG_PATH=` —— 你插件配置文件（`astrbot_plugin_readingsteiner_config.json`）的真实路径。
- `WEBUI_FRONTEND_DIR=/path/to/readingsteiner-webui/frontend/build` —— 第 2 步产物的绝对路径。
- **联调期 `WEBUI_COVENANT_READONLY=1`** —— 锁住写接口（一律 502），防误改生产铭契；验收无误再置 0。

## 4. 写权限（重要）

铭契写是**原子写**（同目录建临时文件 + rename），需要**插件配置文件所在目录**对运行用户可写。若该目录是 root 属主（容器挂载常见）：

```bash
sudo chown youruser:youruser /path/to/astrbot/data/config
```

（实测此修改可扛过 `docker restart astrbot`，不会被容器接管回 root。）

## 5. systemd

编辑 `deploy/readingsteiner-webui.service`，把 `User=` 与各 `/home/youruser/...` 路径换成你的实际值，然后：

```bash
sudo cp deploy/readingsteiner-webui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now readingsteiner-webui
systemctl status readingsteiner-webui
ss -tlnp | grep 8761       # 确认监听 127.0.0.1:8761，绝非 0.0.0.0
```

## 6. 访问（SSH 隧道）

EverOS 应用层裸奔，WebUI 绝不公网——只在你本机经隧道访问：

```bash
ssh -L 8761:127.0.0.1:8761 -p <ssh-port> youruser@<your-server>
# 浏览器：http://127.0.0.1:8761
```

## 验收要点

- `ss -tlnp` 显示监听 `127.0.0.1:8761`，**绝非 `0.0.0.0`**。
- 未登录访问写接口 → 401；未配 `WEBUI_AUTH_SECRET` → 503；`pytest` 全过。
- 读层：登录后看某用户画像 / episode / 检索，与 QQ 内 `/epk` 行为一致。
- **改铭契后 astrbot 不热重载插件配置**，需 `docker restart astrbot` 才生效（前端已有提示）。
