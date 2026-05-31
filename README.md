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
-> Markdown 报告
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
- 证据/结论绑定，以及无支撑结论降级。
- 本地启动和 Docker Compose 启动路径。

## 快速开始

### 后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
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
3. 点击 `Run no-key demo`。
4. 查看 `Agent Trace`。
5. 找到 Critic Agent 针对缺失 TRAE 价格证据创建的 Review Ticket。
6. 确认 Research Agent 执行了补充搜索。
7. 打开 `Evidence & Claims`。
8. 确认被纳入的结论都有证据支撑，无支撑结论已被降级。
9. 打开 `Final Report`。
10. 确认报告列出了 evidence id、来源和不确定性说明。

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
- Provider 抽象接口：已支持基础接口。
- AnySearch Skill Provider：计划中。
- Seed LLM Provider：计划中。
- SSE 实时流式输出：计划中。
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

当前只完整支持 Critic Agent 打回 Research Agent 的补采回环。打回 Template / Analyst / Writer、用户操作 Review Ticket、局部重跑和 human-in-the-loop 仍属于下一阶段。

`.agents/` 和 `.codex/` 是本地工具/Agent 工作区状态目录，当前为空是正常的，已加入 `.gitignore`，不作为业务代码提交。

## API Key 配置

首个演示版不需要真实 API Key。后续接入 AnySearch / Seed 时，把密钥放在根目录 `.env`，不要放在前端代码或 `frontend/.env` 中：

```bash
cp .env.example .env
```

```env
USE_MOCK_SEARCH=false
USE_MOCK_LLM=false
ANYSEARCH_API_KEY=你的_anysearch_key
SEED_API_KEY=你的_seed_key
SEED_BASE_URL=你的_seed_base_url
SEED_MODEL=你的_seed_model
DATABASE_URL=sqlite:///./data/app.db
```

注意：当前真实 AnySearch / Seed provider 还未实现，所以填写 Key 后仍不会自动调用真实服务。下一阶段需要实现 provider factory 和真实 provider。

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
