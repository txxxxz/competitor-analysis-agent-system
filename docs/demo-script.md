# Demo Script

## Goal

Show that the system is not only a report generator. It performs evidence-first analysis with visible Agent collaboration and Review Ticket routing.

## Flow

1. Start backend and frontend.
2. Open `http://localhost:5173`.
3. Select `AI 工具增强 Demo`.
4. Run the no-key demo.
5. Explain the task:
   - Target: Cursor.
   - Competitors: GitHub Copilot, Windsurf, TRAE.
   - Domain: AI tools.
   - Strictness: high.
6. Open `Agent Trace`.
7. Point out:
   - Planner creates the brief.
   - Template Agent selects AI tools template.
   - Research Agent creates search/tool calls.
   - Critic Agent creates a Review Ticket because TRAE pricing evidence is missing.
   - Research Agent performs supplemental search.
   - Analyst regenerates claims.
   - Evidence Reviewer completes the evidence gate.
8. Open `Evidence & Claims`.
9. Show evidence-backed claims and unsupported/uncertain claims.
10. Open `Final Report`.
11. Show the report includes evidence ids, source list, and uncertainty notes.

## Backup Demo

Run `通用产品 Demo` to show the system is not hard-coded to AI tools.
