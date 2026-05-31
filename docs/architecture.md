# 架构说明

## 首个 Demo 范围

当前版本是一个本地可运行、无需 API Key 的 LangGraph demo。它用于验证 V1.2 PRD 的核心流程：结构化任务、模板选择、搜索计划、证据链、Review Ticket、补采回环、Agent Trace 和 Markdown 报告。

它不是生产版，也没有接入真实 AnySearch 或 Seed 大模型。

## 后端

- FastAPI 提供任务与结果 API。
- LangGraph 执行 Agent workflow。
- SQLite 保存任务和完整 workflow result。
- Mock providers 返回确定性的 demo fixtures。

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
  - Evidence & Claims。
  - Final Report。

下一阶段建议补充：

- New Analysis 结构化表单。
- Search Plan 审查视图。
- Comparison Matrix。
- Trust Summary。
- Recent Runs。

## Provider 边界

目标结构：

```text
SearchProvider
  -> MockSearchProvider
  -> AnySearchSkillProvider

LLMProvider
  -> MockLLMProvider
  -> SeedLLMProvider
```

当前实现：

- `SearchProvider` 接口已存在。
- `LLMProvider` 接口已存在。
- `MockSearchProvider` 已接入 workflow。
- `MockLLMProvider` 已存在，但尚未深度参与复杂生成。
- `AnySearchSkillProvider` 尚未实现。
- `SeedLLMProvider` 尚未实现。
- provider factory 和 `.env` 切换尚未实现。

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
-> Writer Agent 生成报告
```

下一阶段应把 ReviewTicket 从文本原因升级为结构化协议，使 Research Agent 能基于 `product`、`missing_evidence_type` 和 `preferred_source_type` 动态生成补采 query。
