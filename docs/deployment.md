# 部署说明

## 本地开发

后端：

推荐直接使用本机 `dev` conda 环境；它已经包含后端依赖，避免重新创建 `.venv` 时因为 PyPI SSL/网络失败卡住。

```bash
cd backend
conda activate dev
uvicorn app.main:app --reload --port 8000
```

如果新终端已经自动进入 `(dev)`，可以省略 `conda activate dev`。依赖检查：

```bash
python -c "import fastapi, langgraph; print('backend deps ok')"
```

前端：

```bash
cd frontend
npm install
npm run dev
```

打开：

```text
http://localhost:5173
```

健康检查：

```bash
curl http://localhost:8000/health
```

## Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

服务地址：

- 前端：`http://localhost:5173`
- 后端：`http://localhost:8000`

## API Key 与密钥文件

当前首个 demo 使用 mock providers，不需要真实 API Key。

后续接入真实 provider 时，在根目录 `.env` 中配置：

```env
USE_MOCK_SEARCH=false
USE_MOCK_LLM=false
ANYSEARCH_API_KEY=
SEED_API_KEY=
SEED_BASE_URL=
SEED_MODEL=
DATABASE_URL=sqlite:///./data/app.db
```

不要把 AnySearch 或 Seed API Key 放到前端。前端只需要：

```env
VITE_API_BASE=http://localhost:8000
```

已忽略的本地密钥/工具状态文件包括：

- `.env`
- `.env.local`
- `backend/.env`
- `frontend/.env.local`
- `*.key`
- `*.pem`
- `*.secret`
- `secrets/`
- `.agents/`
- `.codex/`

## 真实 Provider 状态

当前状态：

- `MockSearchProvider`：已实现。
- `MockLLMProvider`：已实现。
- `SearchProvider` / `LLMProvider` 基础接口：已实现。
- `AnySearchSkillProvider`：未实现。
- `SeedLLMProvider`：未实现。
- 根据 `.env` 自动选择 mock / real provider：未实现。

因此现在填写 `ANYSEARCH_API_KEY` 或 `SEED_API_KEY` 不会自动启用真实服务。下一阶段需要实现 provider factory、真实 provider 和失败 fallback。

## 服务器部署

Linux 服务器已安装 Docker 时：

```bash
git clone <repo-url>
cd competitor-analysis-agent-system
cp .env.example .env
docker compose up -d --build
```

如需公网访问，建议在前面放 Nginx 或 Caddy：

- 前端容器端口：`5173`
- 后端容器端口：`8000`

生产部署仍需额外补充 HTTPS、认证、日志、监控和 secret 管理。首个 demo 未包含生产加固。
