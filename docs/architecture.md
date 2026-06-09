# 架构说明

## 首个 Demo 范围

当前版本是一个本地可运行、无需 API Key 的 LangGraph demo。它用于验证 V1.2 PRD 的核心流程：结构化任务、模板选择、搜索计划、证据链、Review Ticket、补采回环、Agent Trace、结构化报告和 Markdown 报告。

它不是生产版；默认运行 Demo fixtures，但已提供 AnySearch 和 Seed 的 live provider adapter，可通过 `.env` 切换并在 trace / trust summary 中显示 demo/live/fallback 边界。

## 后端

- FastAPI 提供任务与结果 API。
- LangGraph 执行 Agent workflow。
- SQLite 保存任务和完整 workflow result。
- Mock providers 返回确定性的 demo fixtures。
- AnySearchProvider / SeedLLMProvider 通过 provider factory 接入 live path。
- Report schema 输出 User Journey（兼容 FeatureTree schema）、PricingModel、UserPersona、SWOT。
- AgentTraceEvent 记录 prompt、input、output、token、latency、provider、request id 等审计字段。

主要文件：

- `backend/app/core/graph.py`：LangGraph 状态图和条件路由。
- `backend/app/core/nodes.py`：各 Agent / workflow 节点函数。
- `backend/app/models/schemas.py`：Task、Source、Evidence、Claim、ReviewTicket、Trace、Report 等数据模型。
- `backend/app/providers/search.py`：SearchProvider 接口。
- `backend/app/providers/llm.py`：LLMProvider 接口。
- `backend/app/providers/mock_search.py`：mock 搜索 provider。
- `backend/app/providers/mock_llm.py`：mock LLM provider。

## 前端

- React/Vite 单页工作台。
- 当前视图：
  - Demo task launcher。
  - Task Config。
  - Agent Trace。
  - Search Plan。
  - Comparison Matrix。
  - Evidence & Claims。
  - Review Tickets。
  - Trust Summary。
  - Final Report。
  - Recent Runs。

## Provider 边界

目标结构：

```text
SearchProvider
  -> MockSearchProvider
  -> AnySearchProvider

LLMProvider
  -> MockLLMProvider
  -> SeedLLMProvider
```

当前实现：

- `SearchProvider` 接口已存在。
- `LLMProvider` 接口已存在。
- `MockSearchProvider` 已接入 workflow。
- `MockLLMProvider` 已接入 Analyst、Critic、Writer 的结构化补强路径。
- `AnySearchProvider` 已实现，读取 `.env` 中的 API Key / base URL / max results。
- `SeedLLMProvider` 已实现 adapter，并可通过 `.env` 切换到 live LLM path。
- provider factory 已实现 mock / real / fallback 运行时切换。
- AnySearch 空结果或请求失败会按配置回退到 fixtures，并写入 trace / tool call。
- Trust Summary 暴露 `provider_mode_label`、`search_mode`、`llm_mode`，避免 Demo fixture run 与 Live provider run 混淆。

## Agent 实现方式

PRD 中的 Agent 当前以 LangGraph 节点函数实现，不是独立进程，也不在 `.agents/` 目录中。

已实现 demo 版节点：

```text
planner_node
-> template_node
-> research_node
-> source_normalizer_node
-> evidence_extractor_node
-> analyst_node
-> critic_node
-> review_router
   -> research_node
   -> writer_node
-> evidence_reviewer_node
-> finalize_node
```

当前回环能力：

```text
Critic Agent
-> Review Ticket
-> Research Agent 补采
-> Source / Evidence 更新
-> Analyst Agent 重算
-> Evidence Reviewer 重新门禁
-> Trust Summary 更新
-> Writer Agent 生成报告
```

ReviewTicket 已包含 `product`、`missing_evidence_type`、`preferred_source_type` 和 `source_query_hint`，Research Agent 会基于这些字段生成补采 query。当前触发面包括 pricing、feature、target_user、security、contradiction；后续可继续扩展到更多垂直行业证据类型。
