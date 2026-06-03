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
13. `.env` 驱动的 provider factory 已实现，可切换 Mock / AnySearch / Seed。
14. AnySearchProvider 已接入真实 API；空结果和请求失败可回退 fixture，并在 trace/tool call 中记录。
15. SeedLLMProvider adapter 已实现，后续可接入复杂生成节点。
16. Review Ticket 用户操作、Evidence exclude / restore、报告 stale draft 导出已实现。
17. `evidence_strictness` 已接入 Evidence Reviewer：low / standard / high 会影响 claim inclusion、downgrade 和 Trust Summary。
18. SSE 实时 Agent Trace 已实现：`/api/v1/tasks/{task_id}/run/stream` 输出 workflow / trace / state / result 事件，前端运行时显示 live stream 摘要。
19. Review Ticket 真正局部重跑已实现：rerun 会执行 Research / Source / Evidence / Analyst / Reviewer / Writer 子流程并返回新的 workflow_result。
20. Review Ticket 更多处置动作已实现：`mark-unavailable` 会标记相关 claim 为 unsupported，`downgrade` 会标记相关 claim 为 downgraded，并重写报告。
21. Evidence & Claims 已支持搜索、状态筛选、产品筛选、claim type 筛选、排序、展开/收起和空筛选状态。
22. Recent Runs 已支持展示最近任务、刷新列表和从已保存任务恢复完整 WorkflowResult。
23. New Analysis 的竞品输入已升级为 chip 输入：支持 Enter / 逗号 / 粘贴列表添加、移除、最多 5 个、目标产品冲突提示和 domain quick-add 候选。
24. Analyst 已深度接入 LLMProvider：Claim 生成后会调用 `complete_structured("claim_enrichment", payload)`，只接受绑定已有 active evidence_id 的补充 claim。
25. Writer 已深度接入 LLMProvider：报告生成会调用 `complete_structured("report_enhancement", payload)`，Seed 可返回结构化摘要 / 建议 / caveats；请求失败或 mock 模式保留 deterministic fallback。
26. Critic 已深度接入 LLMProvider：确定性 coverage gate 之后会调用 `complete_structured("review_ticket_suggestions", payload)`，只接受 scope 内产品和去重后的结构化 ReviewTicket。

尚未完成：

1. 暂无 V1.2 MVP 阻断项；后续可继续增强成本控制、更多真实 LLM prompt 和外部部署硬化。

## 当前工程判断

1. `.agents/` 不是业务 Agent 目录；业务 Agent 目前在 `backend/app/core/nodes.py` 和 `backend/app/core/graph.py`。
2. AnySearch / Seed API Key 后续只放根目录 `.env`，不放前端。
3. 真实 provider 只能增强当前 no-key demo，不能破坏无密钥可运行性。
4. 完成版项目应只保留面向运行、部署、架构和演示的文档；PRD、课程资料、阶段性规划草稿和问题审计稿不进入最终展示材料。

## 下一步优先级

P2：

1. 接入 SeedLLMProvider。
2. 为 Planner / Analyst / Critic / Writer 设计结构化 JSON prompt。
3. 增加 JSON 修复、失败降级和成本控制。
4. 增加更完整的成本控制和 tracing metadata。

## 验证记录

最近一次验证：

```bash
cd backend
conda run -n dev python -m pytest tests -q
```

结果：21 passed，1 warning。

```bash
cd frontend
npm run build
```

结果：构建通过。

Browser smoke：

1. `http://127.0.0.1:5173/` 页面加载正常。
2. 点击 `Run analysis` 后，前端显示 `Live stream captured 16 trace event(s).`
3. 最终结果页、Agent Trace 和 Review Ticket 区正常渲染，console 无 error / warn。
4. Review Ticket `Downgrade` 动作会刷新 workflow_result，ticket、claim 和 report 显示降级原因。
5. Recent Runs 可加载最近完成任务，恢复后显示 9 sources / 9 evidence / 11 claims / 1 ticket，并高亮当前任务。
6. Evidence & Claims 可按 `Downgraded` 筛选到 `1 of 11 claims`，展开后显示 TRAE pricing 证据详情。
7. Evidence & Claims 空搜索显示 `0 of 11 claims` 和 `No claims match the current filters.`
8. 刷新后 console 只有 React DevTools info；favicon 404 已通过 `frontend/public/favicon.svg` 消除。
9. Chip 输入可移除 TRAE、quick-add Codeium；运行后 Recent Runs 顶部显示 `Cursor / GitHub Copilot, Windsurf, Codeium`，Trace 出现 `Codeium lacks official pricing evidence`。
10. Final Report 显示 `结构化综合摘要`、`结构化建议`、`结构化 Caveats`，验证 Writer 已应用 LLMProvider 结构化增强。
11. API workflow 返回 `claims=12`，ToolCall 包含 `MockLLMProvider:claim_enrichment` 和 `MockLLMProvider:report_enhancement`。
12. Browser 恢复该 Recent Run 后显示 12 claims，Trace 出现 `llm_claim_enrichment_applied` 与 `llm_enhancement_applied`。
13. 单元测试覆盖 Seed-style Critic review ticket suggestions：有效 ticket 加入，无效 scope 外产品被过滤。
14. Demo 截图已生成：`output/playwright/demo-final-report.png`、`output/playwright/demo-evidence-claims.png`。

Playwright / Chromium：

1. WSL Ubuntu 中已安装 Playwright Chromium。
2. 运行 `npx playwright screenshot --browser=chromium 'data:text/html,<title>ok</title><h1>ok</h1>' /tmp/playwright-ok.png` 已成功生成截图。

## 文件整理记录

2026-06-01：

1. 将不应上传的资料加入 `.gitignore`：`cheatsheet/`、`项目要求.pdf`、V1.0/V1.2 PRD PDF、V1.2 PRD Markdown、`V1.2-PRD修订建议.md`、`V1.2-MVP闭环实施规划.md`。
2. 将 `task_plan.md`、`findings.md`、`demo-product-audit.md` 的核心内容合并进本文件。
3. 删除 `task_plan.md`、`findings.md`、`demo-product-audit.md`，减少过程性 Markdown 数量。
