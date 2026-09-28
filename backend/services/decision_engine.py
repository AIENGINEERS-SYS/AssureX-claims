"""Explicit, auditable automated decision and explanation rules."""

LABELS = {"likely_valid": "Likely Valid", "likely_invalid": "Likely Invalid",
          "manual_review_required": "Manual Review Required"}


class DecisionEngine:
    INVALIDATING_RULES = {"warranty_expiry", "excluded_damage", "repair_authorization"}

    def decide(self, python_result, gtm_result, comparison, rules, contradictions, duplicate):
        problems, support = [], []
        by_code = {rule["rule_code"]: rule for rule in rules}
        for rule in rules:
            item = {"code": rule["rule_code"], "message": rule["message"], "severity": rule["severity"]}
            (support if rule["result"] == "passed" else problems).append(item)
        if comparison["status"] in {"Model Disagreement", "Uncertain Result", "Weak Match"}:
            problems.append({"code": "model_consistency", "message":
                f"Model comparison is {comparison['status'].lower()}.", "severity": "medium"})
        if duplicate["duplicate_risk"] in {"medium", "high"}:
            problems.append({"code": "duplicate_claim_risk", "message":
                f"Duplicate-claim risk is {duplicate['duplicate_risk']}.",
                "severity": "high" if duplicate["duplicate_risk"] == "high" else "medium"})

        deterministic_invalid = [rule for rule in rules if rule["rule_code"] in self.INVALIDATING_RULES
                                 and rule["result"] == "failed" and rule["severity"] == "high"]
        ambiguous = (not python_result or not gtm_result or comparison["status"] in
                     {"Model Disagreement", "Uncertain Result", "Weak Match"} or
                     any(rule["result"] == "manual_review" for rule in rules) or
                     any(item["severity"] == "high" for item in contradictions) or
                     duplicate["duplicate_risk"] in {"medium", "high"} or
                     by_code.get("serial_number_verification", {}).get("result") == "failed")
        both_invalid = bool(python_result and gtm_result and
            python_result["prediction_class"] == gtm_result["prediction_class"] == "invalid" and
            comparison["status"] in {"Strong Match", "Acceptable Match"})
        both_valid = bool(python_result and gtm_result and
            python_result["prediction_class"] == gtm_result["prediction_class"] == "valid" and
            comparison["status"] in {"Strong Match", "Acceptable Match"})
        if deterministic_invalid:
            decision = "likely_invalid"
        elif ambiguous:
            decision = "manual_review_required"
        elif both_invalid:
            decision = "likely_invalid"
        elif both_valid and not problems:
            decision = "likely_valid"
        else:
            decision = "manual_review_required"
        action = ("Reviewer verification required." if decision == "manual_review_required" else
                  "Proceed with rejection workflow and reviewer confirmation." if decision == "likely_invalid" else
                  "Proceed with approval workflow.")
        explanation = {"decision": LABELS[decision], "decision_class": decision,
                       "supporting_evidence": support, "problems": problems, "required_action": action}
        return decision, explanation
