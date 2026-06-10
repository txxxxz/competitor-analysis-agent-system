# PM Acceptance Test Cases

这三组测试用于让产品经理直接按照最终报告判断系统是否满足真实竞品分析需求。每组都要求报告同时给出结构化结果、证据绑定、风险状态和下一步动作。

## Case 1: 可进入 PM 内部评审的 AI 工具竞品报告

- 输入：Cursor 对比 GitHub Copilot、Windsurf。
- 目标：评估定位、定价、用户旅程、目标用户、安全风险和团队采购建议。
- 必看报告章节：可信度摘要、结构化综合摘要、PM 决策页、决策摘要、关键差异洞察、核心结论、产品定位与能力矩阵、用户旅程 User Journey、定价模型 PricingModel、用户画像 UserPersona、SWOT、PM 验收检查、下一步行动清单、数据来源。
- PM 验收点：
  - 报告状态为 `passed`，没有未解决 Review Ticket；若第三方来源占比不足 35%，`PM 验收检查` 必须标为“可进入内部评审”，并提示正式发布前补外部样本。
  - 每个进入报告的核心结论都绑定 Evidence ID。
  - 三个产品都有定价证据和结构化交互路径覆盖；真实浏览器实测覆盖必须单独计数，不能把 fixture path 冒充为 live browser observation。
  - 报告不能只复述“某产品是 AI 工具 / 有团队套餐”这类显而易见信息，必须给出适用采购场景、不能下结论的边界和下一步动作。
  - `PM 决策页` 必须一屏展示建议动作、优先级、风险等级、证据等级、定价缺口和是否可对外。
  - 未拿到金额、额度和计费单位时，PricingModel 必须把缺口列入 `data_gaps`，并明确不能做价格高低排序。
  - `结构化综合摘要` 必须出现在 `PM 决策页` 前，不能被追加到数据来源或 Agent 记录之后。
  - `PM 验收检查` 量化展示产品数、来源数、证据数、结论数、第三方来源占比、真实浏览器覆盖和结构化路径数量。
  - 结论能回答“下一步产品差异化机会是什么”和“哪些风险可以进入采购/路线评审”。
- 失败判定：报告缺少 User Journey / PricingModel / UserPersona / SWOT 任一结构，或出现无证据事实性结论。

## Case 2: Review Ticket 补采闭环

- 输入：Cursor 对比 TRAE，刻意覆盖 TRAE 定价证据缺口。
- 目标：验证 Critic Agent 能发现缺口、打回 Research Agent、补采后展示改善。
- 必看报告/轨迹：Review Tickets、Agent Trace、PM 验收检查、TRAE PricingModel。
- PM 验收点：
  - Critic 为 TRAE pricing 创建 Review Ticket。
  - 重跑后 ticket 记录 `before_evidence_ids`、`added_evidence_ids`、`improved_claim_ids`。
  - ticket 只有在新增证据绑定到定价结论后才 `resolved`。
  - Agent Trace 包含 `supplemental_search` 和 `review_ticket_improvement_verified`。
  - 报告不能把未补证项写成确定事实。
- 失败判定：只要执行了补采就直接 resolved，或没有展示新增证据和改善结论。

## Case 3: 社媒阻断与人工复核

- 输入：飞书对比钉钉，启用小红书舆情，并提供点点 AI 人工摘要；MCP 登录状态模拟为未登录。
- 目标：验证社媒证据受阻时，系统不会编造用户反馈，并能给 PM 明确下一步动作。
- 必看报告章节：社媒舆情洞察、PM 验收检查、不确定性与被阻断结论、Review Tickets。
- PM 验收点：
  - 报告状态为 `reviewing`，不是 `passed`。
  - 报告保留人工摘要来源，同时明确小红书登录阻断。
  - Review Ticket 包含 `XHS_LOGIN_REQUIRED`。
  - `PM 验收检查` 显示“需人工复核”和未解决工单数量。
  - 用户画像和推荐判断必须贴合协同 SaaS 场景，例如跨团队协同、开放接口、配置成本和权限治理；不能套用 AI 编程工具画像。
  - 没有无证据绑定的社媒事实性结论进入最终报告。
- 失败判定：未登录时仍假装完成舆情采集，或把人工摘要当作全量真实采集结论。

## Case 4: 陌生产品泛化质量

- 输入：Notion Calendar 对比 Google Calendar，使用通用产品域。
- 目标：验证报告质量不是为 Cursor / Copilot / Windsurf 写死。
- 必看报告章节：PM 决策页、PM 验收检查、UserPersona、不确定性与被阻断结论。
- PM 验收点：
  - 没有可用来源时报告状态为 `reviewing`，并明确“不可外发，先处理阻断项”。
  - 报告仍生成通用 PM 决策页和质量 rubric，但所有无证据综合都进入 caveats 或不确定性。
  - 用户画像必须使用通用产品画像，如核心场景使用者、采购与增长决策者；不能泄漏 AI 编程工具画像。
  - 所有最终 claims 不得在无证据时进入 report。
- 失败判定：陌生产品仍出现编辑器、代码上下文、AI 开发者等模板化表达，或无证据事实性结论进入正文。
