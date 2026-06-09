from app.models.schemas import PMSkill
from app.skills.registry import SkillPromptComposer, parse_github_skill_url
from app.storage.sqlite import SQLiteStore


def _skill(skill_id: str = "skill_test", license_name: str = "MIT") -> PMSkill:
    return PMSkill(
        skill_id=skill_id,
        name="competitor-analysis",
        description="Competitive analysis framework.",
        intent="competitor_analysis",
        repo_url="https://github.com/phuryn/pm-skills",
        path="pm-market-research/skills/competitor-analysis/SKILL.md",
        ref="main",
        license=license_name,
        content_hash="abc123",
        markdown="# Competitor Analysis\n\nUse a PM lens.",
        source="default",
        requires_license_ack=license_name == "CC BY-NC-SA 4.0",
    )


def test_parse_github_skill_urls():
    raw = parse_github_skill_url(
        "https://raw.githubusercontent.com/phuryn/pm-skills/main/pm-market-research/skills/competitor-analysis/SKILL.md"
    )
    assert raw.path == "pm-market-research/skills/competitor-analysis/SKILL.md"

    blob = parse_github_skill_url(
        "https://github.com/phuryn/pm-skills/blob/main/pm-market-research/skills/competitor-analysis/SKILL.md"
    )
    assert blob.raw_url == raw.raw_url

    tree = parse_github_skill_url(
        "https://github.com/phuryn/pm-skills/tree/main/pm-market-research/skills/competitor-analysis"
    )
    assert tree.path == "pm-market-research/skills/competitor-analysis/SKILL.md"


def test_skill_assignment_and_prompt_composer(tmp_path):
    store = SQLiteStore(str(tmp_path / "skills.db"))
    skill = store.upsert_pm_skill(_skill())
    store.save_pm_skill_assignment("competitor_analysis", skill.skill_id, True, True)

    context = SkillPromptComposer(store).context_for_slot("competitor_analysis")

    assert context is not None
    assert context.skill_name == "competitor-analysis"
    assert "cannot override the JSON schema" in context.prompt
    assert "# Competitor Analysis" in context.prompt
