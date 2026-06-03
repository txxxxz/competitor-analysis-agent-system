# 竞品分析 Agent 系统

基于 V1.2 PRD 的证据优先竞品分析 Agent 协作系统。

当前首个可运行版本是一个 **无需密钥的 LangGraph 演示版**。它不需要真实的 AnySearch 或 Seed API Key，而是使用确定性的 fixtures 和 mock providers 来验证核心工作流：

```text
任务配置
-> 模板选择
-> 搜索计划
-> 来源
-> 证据
-> 结论
-> 审查工单
-> 补充研究
-> 证据门禁
-> 结构化报告 + Markdown 报告
-> Agent 轨迹
```

## 当前可用能力

- FastAPI 后端。
- LangGraph `StateGraph` 编排。
- React/Vite 前端。
- SQLite 任务结果持久化。
- AI 工具演示：Cursor vs GitHub Copilot vs Windsurf vs TRAE。
- 通用产品演示：Notion vs Coda vs Airtable。
- Critic Agent 到 Research Agent 的 Review Ticket 闭环。
- Review Ticket 覆盖 pricing、feature、target_user、security、contradiction 缺口。
- 证据/结论绑定，以及无支撑结论降级。
- 报告结构化输出：FeatureTree、PricingModel、UserPersona、SWOT。
- Agent Trace 记录 prompt/input/output/token/latency/provider/request id 字段。
- Demo fixture run 与 Live provider run 在 Trust Summary、ToolCall、Trace 中明确标注。
- 本地启动和 Docker Compose 启动路径。

## 快速开始

### 后端

推荐直接使用本机 `dev` conda 环境；它已经包含 FastAPI、LangGraph、Uvicorn 等依赖，避免重新创建 `.venv` 时遇到 PyPI SSL/网络问题。

```bash
cd backend
conda activate dev
uvicorn app.main:app --reload --port 8000
```

如果新终端已经自动进入 `(dev)`，可以省略 `conda activate dev`。依赖检查：

```bash
python -c "import fastapi, langgraph; print('backend deps ok')"
```

健康检查：

```bash
curl http://localhost:8000/health
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

打开：

```text
http://localhost:5173
```

### Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

打开：

```text
http://localhost:5173
```

## 演示脚本

1. 打开前端页面。
2. 选择 `AI 工具增强 Demo`。
3. 点击 `Run analysis`。
4. 查看 `Agent Trace`。
5. 找到 Critic Agent 针对 pricing / feature / target_user / security / contradiction 缺口创建的 Review Ticket。
6. 确认 Research Agent 执行补充搜索，并观察 ticket 从 open 进入 resolved / dismissed 等状态。
7. 打开 `Evidence & Claims`。
8. 确认被纳入的结论都有证据支撑，无支撑结论已被降级。
9. 打开 `Final Report`。
10. 确认报告列出了 FeatureTree、PricingModel、UserPersona、SWOT、evidence id、来源和不确定性说明。

## 架构

```text
frontend/
  React + Vite 工作台 UI

backend/
  FastAPI
  LangGraph StateGraph
  SQLite 存储
  MockSearchProvider
  MockLLMProvider
  Demo fixtures
```

核心图：

```text
planner
-> template
-> research
-> source_normalizer
-> evidence_extractor
-> analyst
-> critic
-> review_router
   -> research
   -> writer
-> evidence_reviewer
-> finalize
```

## 真实状态

- 本地无密钥演示：已支持。
- LangGraph 编排：已支持。
- SQLite 持久化：已支持。
- Mock SearchProvider：已支持。
- Mock LLMProvider：已支持。
- Provider 抽象接口：已支持基础接口和 `.env` 驱动 factory。
- AnySearch API Provider：已支持真实 API 调用、空结果 fallback 和请求失败 fallback。
- Seed LLM Provider：已支持 adapter 和 `.env` 切换；Analyst、Critic、Writer 已通过 provider 接口调用结构化补强。
- SSE 实时流式输出：已支持 `/api/v1/tasks/{task_id}/run/stream`。
- 生产服务器加固：首个演示版未包含。

## Agent 实现状态

PRD 中的主要 Agent 当前以 LangGraph 节点函数实现，位置在 `backend/app/core/nodes.py` 和 `backend/app/core/graph.py`，不是放在 `.agents/` 目录中。

已实现 no-key demo 版：

- Planner Agent
- Template / Schema Agent
- Research Agent
- Analyst Agent
- Critic Agent
- Writer Agent
- Source Normalizer
- Evidence Extractor
- Evidence Consistency Reviewer

当前支持 Critic Agent 打回 Research Agent 的补采回环、用户 evidence exclude / restore、Review Ticket accept / rerun / resolve / dismiss / mark unavailable / downgrade 和报告 stale draft 导出。Review Ticket rerun 会执行 Research -> Source -> Evidence -> Analyst -> Reviewer -> Trust Summary -> Writer 的局部重跑子流程。

`.agents/` 和 `.codex/` 是本地工具/Agent 工作区状态目录，当前为空是正常的，已加入 `.gitignore`，不作为业务代码提交。

## API Key 配置

演示版不需要真实 API Key。接入 AnySearch / Seed 时，把密钥放在根目录 `.env`，不要放在前端代码或 `frontend/.env` 中：

```bash
cp .env.example .env
```

```env
USE_MOCK_SEARCH=false
USE_MOCK_LLM=false
ANYSEARCH_API_KEY=你的_anysearch_key
ANYSEARCH_BASE_URL=https://api.anysearch.com/v1/search
ANYSEARCH_MAX_RESULTS=5
SEED_API_KEY=你的_seed_key
SEED_BASE_URL=你的_seed_base_url
SEED_MODEL=你的_seed_model
ALLOW_PROVIDER_FALLBACK=true
ALLOW_EMPTY_SEARCH_FALLBACK=true
DATABASE_URL=sqlite:///./data/app.db
```

注意：真实 AnySearch 请求如果返回空结果或请求失败，在 `ALLOW_PROVIDER_FALLBACK=true` 时会回退到 fixtures，并在 Agent Trace / ToolCall 中记录 fallback 原因，保证 no-key demo 和外部服务异常时仍可运行。

## API 接口

```text
GET  /health
GET  /api/demo-tasks
GET  /api/tasks
POST /api/tasks
GET  /api/tasks/{task_id}
POST /api/tasks/{task_id}/run
GET  /api/tasks/{task_id}/trace
GET  /api/tasks/{task_id}/evidence
GET  /api/tasks/{task_id}/claims
GET  /api/tasks/{task_id}/report
```
