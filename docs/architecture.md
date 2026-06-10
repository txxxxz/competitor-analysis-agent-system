# 架构说明

## 运行模式边界

当前版本是一个 Provider 驱动的 LangGraph 竞品分析工作台。真实业务流程要求搜索与 LLM Provider 都就绪，并且关闭 mock / fixture fallback；`/api/v1/provider-status` 会在 `USE_MOCK_SEARCH=false`、`USE_MOCK_LLM=false`、`ALLOW_PROVIDER_FALLBACK=false`、`ALLOW_EMPTY_SEARCH_FALLBACK=false` 且必需密钥完整时才返回 workflow ready。

离线 demo mode 仍可通过显式 mock provider 运行，用来验证 V1.2 PRD 的核心流程：结构化任务、模板选择、搜索计划、证据链、Review Ticket、补采回环、Agent Trace、结构化报告和 Markdown 报告。demo mode 的输出会在 trace / trust summary 中标注为 `Demo fixture run`，不能冒充 live provider 结果。

## 后端

- FastAPI 提供任务与结果 API。
- LangGraph 执行 Agent workflow。
- SQLite 保存任务和完整 workflow result。
- Mock providers 只用于显式 demo mode 或测试。
- DuckDuckGo / AnySearchProvider 与 DeepSeek / SeedLLMProvider 通过 provider factory 接入 live path。
- Report schema 输出 PM 决策页、User Journey（兼容 FeatureTree schema）、PricingModel、UserPersona、SWOT。
- Demo / fixture 中的结构化交互路径会标记为 `fixture_walkthrough`，只用于旅程假设；只有真实 Browser / Playwright 采集来源才计入 `browser_walkthrough` 和 Trust Summary 的浏览器实测覆盖。
- PricingModel 不只展示套餐层级，还列出金额、计费单位、额度限制、试用/免费、企业条款和 `data_gaps`；缺少金额/额度/计费单位时，报告只能比较商业化包装，不能做价格高低排序。
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
-> social_listening_node
-> source_normalizer_node
-> evidence_extractor_node
-> interaction_node
-> analyst_node
-> critic_node
-> review_router
   -> research_node
-> evidence_reviewer_node
-> trust_summary_node
-> writer_node
-> finalize_node
```

当前回环能力：

```text
Critic Agent
-> Review Ticket
-> Research Agent 补采
-> Source / Evidence 更新
-> Interaction Agent 补足用户旅程结构化路径，并明确区分 fixture_walkthrough 与真实 browser_walkthrough
-> Analyst Agent 重算
-> Evidence Reviewer 重新门禁
-> Trust Summary 更新
-> Writer Agent 生成报告
```

ReviewTicket 已包含 `product`、`missing_evidence_type`、`preferred_source_type`、`source_query_hint`、`before_evidence_ids`、`added_evidence_ids`、`before_claim_statuses`、`after_claim_statuses` 和 `improved_claim_ids`。Research / Interaction rerun 都会记录 before/after 快照；ticket 只有在新增证据绑定到目标 product/type 的新通过结论后才自动 resolved。`resolved` / `dismissed` / `blocked` 关闭态需要先重新打开，不能被普通 rerun 静默改写。当前触发面包括 pricing、feature、target_user、security、contradiction；真实浏览器交互缺口会在报告中显式降级，不用 fixture path 冒充 live observation。
