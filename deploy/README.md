# 部署说明

> 完整步骤见 [docs/04-deployment.md](../docs/04-deployment.md)；本目录为配置样例（`.env.example`、systemd 单元）与快速参考。

## 运行时硬约束

- **Python >=3.12**（`requires-python = ">=3.12"`；不用 3.11，从 3.12 起稳定支持，不锁上限）。服务器现状（2026-06-23 验证）：
  `python3.12` = 3.12.3、`python3` 默认即 3.12.3、EverOS 自身 venv 也是 3.12.3 → 已就绪。
  仅需补 venv 包（建 venv 用，未确认是否预装）：
  ```bash
  sudo apt install -y python3.12-venv   # 若已装会提示已是最新
  ```
- 服务绑 `127.0.0.1`（绝不 0.0.0.0），访问经 SSH 隧道。

## 后端部署（systemd）

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
# 填好 ../deploy/.env（尤其 WEBUI_AUTH_SECRET）
sudo cp ../deploy/readingsteiner-webui.service /etc/systemd/system/
sudo systemctl enable --now readingsteiner-webui   # ⚠️ sudo 需密码，非交互须手动执行
```

## 前端构建与访问

前端构建产物由后端 FastAPI 静态托管（细化阶段在 main.py 挂 StaticFiles），或独立走 SSH 隧道访问。
现阶段：开发用 Vite dev server（127.0.0.1:5173，代理 /api → 后端）。

```bash
cd frontend && npm run build        # 产物在 frontend/build/
```

## 经 SSH 隧道访问

把 WebUI 的 8761 加进 SSH 端口转发：
```bash
ssh -L 8761:127.0.0.1:8761 \
    -L 8596:127.0.0.1:8596 \
    -p <ssh-port> youruser@<your-server>
# 浏览器：http://localhost:8761
# 8596 一并转发方便本机直连 EverOS /health 做联通性核对
```