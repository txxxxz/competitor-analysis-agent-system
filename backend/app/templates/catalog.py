from app.models.schemas import AnalysisTemplate, Domain


TEMPLATES: dict[Domain, AnalysisTemplate] = {
    "general_product": AnalysisTemplate(
        template_id="tpl_general_product",
        name="通用产品竞品分析模板",
        sections=["定位", "目标用户", "核心功能", "商业模式", "SWOT", "机会点"],
        evidence_rules=[
            "事实性判断必须绑定 Evidence",
            "定价和功能信息优先使用官方来源",
            "用户反馈必须标注来源类型和样本局限",
        ],
        claim_types=["positioning", "feature", "pricing", "feedback", "opportunity"],
        review_gates=["coverage", "evidence_binding", "uncertainty"],
    ),
    "saas": AnalysisTemplate(
        template_id="tpl_saas",
        name="SaaS / 企业服务竞品分析模板",
        sections=["定位", "团队协作", "集成能力", "企业能力", "定价", "风险"],
        evidence_rules=[
            "企业能力需要官方文档或定价页支持",
            "集成能力需要产品文档或 marketplace 来源支持",
        ],
        claim_types=["positioning", "enterprise", "integration", "pricing", "risk"],
        review_gates=["coverage", "source_quality", "evidence_binding"],
    ),
    "ai_tools": AnalysisTemplate(
        template_id="tpl_ai_tools",
        name="AI 工具增强模板",
        sections=["定位", "核心 AI 能力", "Agent 能力", "开发者工作流", "上下文管理", "定价", "技术风险"],
        evidence_rules=[
            "AI 能力必须区分官方能力、用户反馈和推断",
            "Agent 能力需要文档、发布说明或产品页面支持",
            "用户旅程必须优先绑定浏览器实测路径；仅有文档时只能标为推断",
            "定价必须优先使用官方定价页",
            "无 Evidence 的事实性 Claim 必须被降级或阻断",
        ],
        claim_types=["positioning", "ai_capability", "agent_capability", "browser_interaction", "workflow", "context", "pricing", "risk"],
        review_gates=["coverage", "agent_capability", "browser_interaction", "pricing_evidence", "evidence_binding"],
    ),
}


def select_template(domain: Domain) -> AnalysisTemplate:
    return TEMPLATES.get(domain, TEMPLATES["general_product"])
