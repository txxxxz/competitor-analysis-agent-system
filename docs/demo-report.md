# Demo Report

Generated: 2026-06-10

## Demo Scope

This demo validates the V1.2 MVP workflow for an evidence-first competitor analysis agent system.

Sample task:

- Target product: Cursor
- Competitors: GitHub Copilot, Windsurf, TRAE
- Domain: AI tools
- Goals: positioning, pricing, feature, target users, security, agent capability
- Evidence strictness: high
- Provider mode: this demo intentionally runs in `Demo fixture run`; live provider delivery is enabled through `.env` only when search and LLM providers are configured and mock / fallback switches are disabled

## Sample Data And Users

Sample user:

- Product manager reviewing AI coding tool competitors
- Needs evidence-backed positioning, pricing, User Journey, user persona, security risk, and workflow comparison
- Must be able to inspect claims, evidence, review tickets, and final report before export

Sample data:

- Demo task templates from `/api/demo-tasks`
- Mock provider fixtures for Cursor, GitHub Copilot, Windsurf, TRAE, Codeium
- Persisted SQLite tasks and workflow results in `backend/data/app.db`

## Normal Path

1. User selects the AI tools demo template.
2. User edits competitors through chip input.
3. User runs analysis.
4. Backend creates a task, streams workflow events, executes LangGraph nodes, and saves the final result.
5. Frontend displays Trust Summary, Search Plan, Matrix, Evidence & Claims, Review Tickets, and Final Report.
6. Recent Runs can restore the completed workflow result.

Evidence:

- Workflow report contains PM 决策页, User Journey, PricingModel, UserPersona, and SWOT structured objects.
- Demo interaction paths are labeled as `fixture_walkthrough`; they support journey hypotheses but are not counted as live browser observations.
- PricingModel surfaces amount, billing unit, usage limit, free/trial, enterprise terms, and data gaps instead of only naming pricing tiers.
- Trust Summary explicitly labels `Demo fixture run` versus `Live provider run` and shows search/LLM modes.
- Trace events include prompt/input/output summaries plus provider, token, latency, and request-id fields where available.
- Trace included `llm_claim_enrichment_applied`.
- Trace included `llm_enhancement_applied`.
- Final Report showed `PM 决策页`, `用户旅程 User Journey`, `定价模型 PricingModel`, `用户画像 UserPersona`, `SWOT`, `结构化综合摘要`, `结构化建议`, and `结构化 Caveats`.

## Error Path

Validated error and fallback behavior:

- V1 task validation rejects target product duplicated in competitors.
- Evidence exclusion marks linked claims and report sections stale.
- Report export blocks stale reports unless draft export is explicitly allowed.
- Provider factory can fall back to mock providers only when fallback is explicitly enabled for demo continuity.
- Seed / DeepSeek request failure paths record failed ToolCalls; deterministic mock enhancement is allowed only in explicit demo mode.
- Review Ticket rerun resolves only when added evidence improves a bound claim; otherwise the report remains in reviewer attention instead of turning gaps into final facts.

## Edge Cases

Validated edge cases:

- Competitor chips prevent duplicate normalized competitors.
- Competitor chips cap input at 5 competitors.
- Empty Evidence & Claims filters show `0 of N claims` with an explicit empty state.
- Review Ticket rerun cap blocks excessive reruns.
- LLM-generated Analyst claims are accepted only when every supporting evidence id exists and is active.
- LLM-generated Critic tickets are accepted only for in-scope products and deduplicated by product/evidence/target node.
- Missing feature, target-user, security, and contradiction coverage creates Review Tickets instead of being hidden by the final report.

## Screenshots

- Final report: `output/playwright/demo-final-report.png`
- Evidence & Claims: `output/playwright/demo-evidence-claims.png`

## Validation Commands

```bash
cd backend
conda run -n dev python -m pytest tests -q
conda run -n dev python -m compileall app -q
```

Result: 66 passed, 1 warning.

```bash
cd frontend
npm run build
```

Result: Vite production build succeeded.

Browser smoke:

- Console contained only React DevTools info.
- No local dev server remained after shutdown checks.
