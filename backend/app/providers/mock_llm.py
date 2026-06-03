from app.providers.llm import LLMProvider


class MockLLMProvider(LLMProvider):
    provider_name = "MockLLMProvider"

    def complete_structured(self, purpose: str, payload: dict) -> dict:
        if purpose == "claim_enrichment":
            positioning_evidence = [
                item
                for item in payload.get("evidence", [])
                if item.get("evidence_type") == "positioning"
            ]
            if len(positioning_evidence) < 2:
                return {"claims": []}
            products = sorted({item.get("product", "") for item in positioning_evidence if item.get("product")})
            return {
                "claims": [
                    {
                        "product": "Cross-product",
                        "claim_type": "llm_synthesis",
                        "claim": f"Provider-assisted synthesis should compare {', '.join(products[:4])} only through bound evidence.",
                        "supporting_evidence": [item["evidence_id"] for item in positioning_evidence[:4] if item.get("evidence_id")],
                        "confidence": "medium",
                    }
                ]
            }
        if purpose == "review_ticket_suggestions":
            return {"review_tickets": []}
        if purpose == "report_enhancement":
            target = payload.get("task", {}).get("target_product", "target product")
            competitors = payload.get("task", {}).get("competitors", [])
            passed_claims = payload.get("trust_summary", {}).get("passed_claim_count", 0)
            total_claims = payload.get("trust_summary", {}).get("total_claim_count", 0)
            return {
                "executive_summary": [
                    f"{target} is compared against {', '.join(competitors)} with {passed_claims}/{total_claims} claims passing evidence review.",
                    "Use unresolved or downgraded claims as follow-up research work rather than final conclusions.",
                ],
                "strategic_recommendations": [
                    "Prioritize official pricing and product documentation before publishing externally.",
                    "Keep the comparison matrix evidence-bound so product and GTM teams can audit every claim.",
                ],
                "caveats": [
                    "Mock LLM synthesis is deterministic and intended for no-key demo validation.",
                ],
            }
        return {"purpose": purpose, "mode": "mock", "payload_keys": sorted(payload.keys())}
