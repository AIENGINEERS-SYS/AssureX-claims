"""Pure rules and decisions. Explanations use exactly the evidence used for decisions."""
from datetime import date, datetime, timezone
from .warranty_calculations import calculate_warranty_expiry

DECISIONS = {"likely_valid": "Likely Valid", "likely_invalid": "Likely Invalid",
             "manual_review_required": "Manual Review Required"}


def calendar_date(value):
    if isinstance(value, datetime):
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return calendar_date(datetime.fromisoformat(value.replace("Z", "+00:00")))
    raise ValueError("Missing date")


def rule(code, result, message, **details):
    return {"code": code, "result": result, "message": message, "details": details}


def check_rules(evidence, policy):
    results, contradictions = [], []
    try:
        purchase, claim, fault = (calendar_date(evidence.get(k)) for k in
                                  ("purchase_date", "claim_date", "fault_date"))
    except (ValueError, TypeError, OverflowError):
        return [rule("INVALID_DATES", "manual_review", "Claim dates are missing or malformed.")]
    if purchase > claim:
        contradictions.append({"code": "PURCHASE_AFTER_CLAIM", "sources": ["product", "claim"]})
    if fault > claim:
        contradictions.append({"code": "FAULT_AFTER_CLAIM", "sources": ["fault", "claim"]})
    if fault < purchase:
        contradictions.append({"code": "FAULT_BEFORE_PURCHASE", "sources": ["fault", "product"]})
    for repair in evidence.get("repairs", []):
        try:
            when = calendar_date(repair.get("repair_date"))
            if when < purchase or when > claim:
                contradictions.append({"code": "REPAIR_DATE_CONFLICT", "sources": [repair["source"], "claim", "product"]})
        except (ValueError, TypeError):
            contradictions.append({"code": "INVALID_REPAIR_DATE", "sources": [repair["source"]]})
    for source in evidence.get("purchase_dates", []):
        try:
            if calendar_date(source["value"]) != purchase:
                contradictions.append({"code": "PURCHASE_DATE_CONFLICT", "sources": ["product", source["source"]]})
        except (ValueError, TypeError):
            contradictions.append({"code": "INVALID_DOCUMENT_DATE", "sources": [source["source"]]})
    results.append(rule("DATE_CONTRADICTIONS", "manual_review" if contradictions else "passed",
                        "Dates conflict across evidence." if contradictions else "Available dates are consistent.",
                        contradictions=contradictions))
    try:
        expiry = calculate_warranty_expiry(purchase, policy.months)
        covered = claim <= expiry if policy.final_day_inclusive else claim < expiry
        results.append(rule("WARRANTY_ACTIVE" if covered else "WARRANTY_EXPIRED", "passed" if covered else "failed",
            "Claim is within the policy warranty period." if covered else "The policy warranty period has expired.",
            expiry=expiry.isoformat(), final_day_inclusive=policy.final_day_inclusive))
    except (ValueError, OverflowError):
        results.append(rule("INVALID_WARRANTY_DATE", "manual_review", "Warranty expiry cannot be calculated."))
    serials = []
    for item in evidence.get("serials", []):
        raw = item.get("value")
        if isinstance(raw, str) and raw.strip():
            value = raw.strip() if policy.serial_case_sensitive else raw.strip().casefold()
            serials.append({**item, "normalized": value})
    mismatch = len({s["normalized"] for s in serials}) > 1
    corroborated = len({s["source"] for s in serials}) >= 2
    results.append(rule("SERIAL_CONFLICT" if mismatch else "SERIAL_VERIFICATION",
        "manual_review" if mismatch or not corroborated else "passed",
        "Serial numbers disagree." if mismatch else "Serial evidence agrees." if corroborated else "Serial evidence needs corroboration.",
        sources=serials))
    uploaded = {d["document_type"] for d in evidence.get("documents", [])}
    missing = sorted(set(policy.required_documents) - uploaded)
    results.append(rule("REQUIRED_DOCUMENTS", "manual_review" if missing else "passed",
        "Required documents are missing." if missing else "Required documents are present.", missing=missing))
    unusable = [d["source"] for d in evidence.get("documents", [])
        if d["ocr_status"] not in {"completed", "completed_with_warnings", "review_required"}
        or d["review_status"] == "pending" or not d.get("integrity_verified", False)]
    results.append(rule("DOCUMENT_PROCESSING", "manual_review" if unusable else "passed",
        "Some evidence could not be verified." if unusable else "Document processing is complete.", sources=unusable))
    unauthorized = [r["source"] for r in evidence.get("repairs", []) if r.get("authorized") is not True]
    repair_unknown = evidence.get("unstructured_repair_history", False)
    results.append(rule("REPAIR_AUTHORIZATION", "manual_review" if policy.authorized_repair_required and (unauthorized or repair_unknown) else "passed",
        "Repair authorization needs verification." if policy.authorized_repair_required and (unauthorized or repair_unknown)
        else "No repair authorization conflicts were found.", sources=unauthorized))
    excluded = evidence.get("damage_type") in policy.exclusions or evidence.get("fault_type") in policy.exclusions
    results.append(rule("POLICY_EXCLUSION", "failed" if excluded else "passed",
        "The reported fault or damage is explicitly excluded." if excluded else "No configured exclusion matches the reported fault."))
    return results


def decide(rules, comparison, python, gtm, duplicates, failures):
    problems = [r["message"] for r in rules if r["result"] != "passed"]
    supporting = [r["message"] for r in rules if r["result"] == "passed"]
    if duplicates:
        problems.append("Evidence or product matches another claim; a reviewer must assess reuse.")
    if failures:
        problems.append("One or more required evaluation services are unavailable or returned invalid evidence.")
    if comparison["status"] not in {"Strong Match", "Acceptable Match"}:
        problems.append("Model comparison: " + comparison["status"] + ".")
    # Infrastructure failures and contradictory facts cannot become an approval or denial.
    if failures or duplicates or any(r["result"] == "manual_review" for r in rules):
        decision, reason = "manual_review_required", "Evidence requires human verification."
    elif any(r["result"] == "failed" for r in rules):
        decision, reason = "likely_invalid", "A deterministic policy rule failed."
    elif python is None or gtm is None or comparison["status"] not in {"Strong Match", "Acceptable Match"}:
        decision, reason = "manual_review_required", "Both models must provide sufficiently confident, agreeing predictions."
    elif python["predicted_class"] == gtm["predicted_class"] == "valid":
        decision, reason = "likely_valid", "Policy checks passed and both models support validity."
    elif python["predicted_class"] == gtm["predicted_class"] == "invalid":
        decision, reason = "likely_invalid", "Both models support invalidity after policy checks."
    else:
        decision, reason = "manual_review_required", "The evidence does not support an automated recommendation."
    return {"decision": decision, "label": DECISIONS[decision], "reason": reason,
            "supporting_evidence": supporting, "problems": problems,
            "required_action": "Human adjudication required." if decision == "manual_review_required" else "Review recommendation and evidence before adjudication."}
