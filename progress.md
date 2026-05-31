# 项目进度与自审

> 本文件是当前阶段唯一保留的过程记录入口。PRD、课程资料、阶段性规划草稿和详细审计稿均作为本地过程材料，不作为完成版项目展示内容。

## 当前目标

把 V1.2 竞品分析 Agent 协作系统从 no-key demo 推进到更完整的 MVP：保持本地可运行和可复现，同时持续补齐真实 provider、证据链可信度、用户可审查工作流和更有价值的竞品分析输出。

## 当前状态

已完成：

1. FastAPI 后端、React/Vite 前端、SQLite 持久化和 Docker Compose。
2. LangGraph `StateGraph` 编排。
3. 以节点函数实现 Planner、Template、Research、Source Normalizer、Evidence Extractor、Analyst、Critic、Writer、Evidence Consistency Reviewer。
4. MockSearchProvider / MockLLMProvider 和 demo fixtures。
5. AI 工具 demo：Cursor vs GitHub Copilot vs Windsurf vs TRAE。
6. 通用产品 demo：Notion vs Coda vs Airtable。
7. ReviewTicket 结构化字段：`product`、`missing_evidence_type`、`preferred_source_type`、`source_query_hint`、`resolution_note`。
8. Research Agent 根据 ReviewTicket 动态生成补采 query，补采 query 会进入 Search Plan。
9. Evidence / Claim 绑定、无证据 Claim 降级、Trust Summary。
10. 前端展示 New Analysis 基础表单、Provider Mode、模板规则、Search Plan、Comparison Matrix、Evidence source detail、Final Report。
11. README、架构文档、部署文档和演示脚本。
12. 后端测试和前端生产构建已通过。

尚未完成：

1. AnySearchSkillProvider。
2. SeedLLMProvider。
3. `.env` 驱动的 provider factory：mock / real / fallback 运行时切换。
4. SSE 实时 Agent Trace。
5. Review Ticket 用户操作：接受补采、标记不可得、降级结论、局部重跑。
6. Evidence & Claims 过滤、排序、用户 exclude 操作。
7. Recent Runs 任务历史入口。
8. 竞品 chip 输入、推荐候选和更强校验。
9. `evidence_strictness` 对 claim inclusion / review 阈值的真实影响。

## 当前工程判断

1. `.agents/` 不是业务 Agent 目录；业务 Agent 目前在 `backend/app/core/nodes.py` 和 `backend/app/core/graph.py`。
2. AnySearch / Seed API Key 后续只放根目录 `.env`，不放前端。
3. 真实 provider 只能增强当前 no-key demo，不能破坏无密钥可运行性。
4. 完成版项目应只保留面向运行、部署、架构和演示的文档；PRD、课程资料、阶段性规划草稿和问题审计稿不进入最终展示材料。

## 下一步优先级

P0：

1. 实现 provider factory，读取 `.env` 并选择 Mock / AnySearch / Seed。
2. 接入 AnySearchSkillProvider，并保留 fixture fallback。
3. 让 ToolCall 记录 provider、status、fallback reason。
4. 让 `evidence_strictness` 真正影响 Claim 和 Evidence Reviewer。

P1：

1. Evidence & Claims 增加筛选、排序、展开/收起和用户排除操作。
2. 增加 Recent Runs 历史任务入口。
3. 增加 Review Ticket 用户操作和局部重跑。
4. 将 New Analysis 的竞品输入升级为 chip 输入。

P2：

1. 接入 SeedLLMProvider。
2. 为 Planner / Analyst / Critic / Writer 设计结构化 JSON prompt。
3. 增加 JSON 修复、失败降级和成本控制。
4. 增加 SSE 实时执行过程。

## 验证记录

最近一次验证：

```bash
cd backend
PYTHONPATH=. pytest
```

结果：2 passed。

```bash
cd frontend
npm run build
```

结果：构建通过。

Playwright / Chromium：

1. WSL Ubuntu 中已安装 Playwright Chromium。
2. 运行 `npx playwright screenshot --browser=chromium 'data:text/html,<title>ok</title><h1>ok</h1>' /tmp/playwright-ok.png` 已成功生成截图。

## 文件整理记录

2026-06-01：

1. 将不应上传的资料加入 `.gitignore`：`cheatsheet/`、`项目要求.pdf`、V1.0/V1.2 PRD PDF、V1.2 PRD Markdown、`V1.2-PRD修订建议.md`、`V1.2-MVP闭环实施规划.md`。
2. 将 `task_plan.md`、`findings.md`、`demo-product-audit.md` 的核心内容合并进本文件。
3. 删除 `task_plan.md`、`findings.md`、`demo-product-audit.md`，减少过程性 Markdown 数量。
