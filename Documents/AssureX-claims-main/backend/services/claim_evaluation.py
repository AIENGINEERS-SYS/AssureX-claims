"""Transactional evaluation, immutable evidence, scoped serialization and safe retries."""
from copy import deepcopy
from datetime import timezone
import hashlib
import json
from time import monotonic
from flask import current_app
from sqlalchemy import select
from werkzeug.exceptions import Conflict
from backend.db.models import ClaimEvaluation, Document, ModelVersion, RepairHistory, RuleResult
from backend.db.services import record_prediction
from backend.extensions import db
from .claim_storage import storage
from .decision_engine import calendar_date, check_rules, decide, rule
from .predictions import ComparisonThresholds, compare_predictions, probabilities
from .warranty_policy import load_policy, PolicyError


def latest_evaluation(claim_id):
    return db.session.scalar(select(ClaimEvaluation).where(ClaimEvaluation.claim_id == claim_id)
                             .order_by(ClaimEvaluation.id.desc()).limit(1))


def snapshot(claim):
    product = claim.product
    evidence = {"claim_date": claim.submission_date.isoformat() if claim.submission_date else None,
        "fault_date": claim.fault_date.isoformat() if claim.fault_date else None,
        "purchase_date": product.purchase_date.isoformat() if product else None,
        "fault_type": claim.fault_type, "damage_type": claim.damage_type,
        "unstructured_repair_history": bool(claim.repair_history),
        "serials": [], "purchase_dates": [], "documents": [], "repairs": []}
    failures = []
    if product:
        evidence["serials"].append({"source": "claim_product", "value": product.serial_number})
    warranty = claim.warranty
    evidence["registered_warranty"] = ({"warranty_id": warranty.warranty_id,
        "start_date": warranty.start_date.isoformat(), "expiry_date": warranty.expiry_date.isoformat(),
        "duration_months": warranty.coverage_duration_months, "warranty_type": warranty.warranty_type}
        if warranty else None)
    store = storage()
    for document in claim.documents:
        item = {"source": document.document_id, "document_type": document.document_type,
                "file_hash": document.file_hash, "ocr_status": document.ocr_status,
                "review_status": document.review_status, "integrity_verified": False}
        try:
            item["integrity_verified"] = hashlib.sha256(store.read(document.storage_path)).hexdigest() == document.file_hash
            if not item["integrity_verified"]:
                failures.append("DOCUMENT_INTEGRITY_FAILED")
        except Exception:
            failures.append("DOCUMENT_STORAGE_UNAVAILABLE")
        evidence["documents"].append(item)
        # Preserve machine and confirmed values separately, even when a correction
        # agrees with the claim: a correction must not erase conflicting OCR evidence.
        for key, destination in (("serial_number", "serials"), ("purchase_date", "purchase_dates")):
            for suffix, data, value_key in (("ocr", document.extracted_data, "value"),
                                            ("confirmed", document.verified_data, "confirmed_value")):
                value = (data or {}).get(key, {})
                if isinstance(value, dict) and value.get(value_key) is not None:
                    evidence[destination].append({"source": f"{document.document_id}:{suffix}",
                        "document_type": document.document_type, "value": value[value_key]})
    if product:
        repairs = db.session.scalars(select(RepairHistory).where(RepairHistory.product_id == product.id)
                                    .order_by(RepairHistory.id).limit(1001)).all()
        if len(repairs) > 1000:
            failures.append("REPAIR_HISTORY_LIMIT_EXCEEDED")
        evidence["repairs"] = [{"source": r.repair_id, "repair_date": r.repair_date.isoformat(),
                                "authorized": r.authorized_service_center} for r in repairs[:1000]]
    return evidence, failures


def model_features(claim, evidence):
    """Allowlist facts available at submission; no decisions, identity, OCR prose or risk flags."""
    product = claim.product
    features = {"product_category": product.category if product else None,
        "brand": product.brand if product else None,
        "purchase_price_ngn": float(product.purchase_price) if product else None,
        # Live UI enums differ from training categories. Do not mislabel severity
        # as cause or silently map "Electrical Failure" to a training fault code.
        "fault_type": claim.fault_type, "damage_severity": claim.damage_type}
    try:
        purchase, fault, submitted = (calendar_date(evidence[k]) for k in
                                      ("purchase_date", "fault_date", "claim_date"))
        if purchase > fault or fault > submitted:
            raise ValueError("Temporal contradiction")
        features.update(purchase_to_fault_days=(fault - purchase).days,
            fault_to_claim_days=(submitted - fault).days, days_from_purchase_to_claim=(submitted - purchase).days,
            claim_month=submitted.month)
    except (ValueError, TypeError):
        raise ValueError("Model dates are missing or contradictory") from None
    # Enforce finite serialization at the inference boundary too.
    json.dumps(features, allow_nan=False)
    return features


def predict_one(kind, claim, features):
    versions = db.session.scalars(select(ModelVersion).where(ModelVersion.model_type == kind,
        ModelVersion.is_active.is_(True)).limit(2)).all()
    providers = current_app.extensions.get("prediction_providers", {})
    provider = providers.get(kind)
    if len(versions) != 1 or provider is None:
        return None, None, f"{kind.upper()}_MODEL_UNAVAILABLE"
    version = versions[0]
    # Deployment code must bind the provider to the exact database release.
    if getattr(provider, "model_version_id", None) != version.id:
        return None, None, f"{kind.upper()}_MODEL_VERSION_MISMATCH"
    start = monotonic()
    try:
        output = provider.predict(deepcopy(features))
        prediction = probabilities(output["probabilities"], output.get("predicted_class"))
        if "top_confidence" in output and float(output["top_confidence"]) != prediction["top_confidence"]:
            raise ValueError("Incorrect top confidence")
        card = output.get("claim_summary_card_path") if kind == "gtm" else None
        if kind == "gtm":
            if not isinstance(card, str) or not card:
                raise ValueError("GTM summary card is missing")
            prediction["summary_card_sha256"] = hashlib.sha256(storage().read(card)).hexdigest()
    except Exception:
        # Do not persist provider exception messages: they may contain claim data or secrets.
        return None, None, f"{kind.upper()}_MODEL_FAILED"
    row = record_prediction(db.session, model_version_id=version.id, claim_id=claim.id,
        predicted_class=prediction["predicted_class"],
        **{"confidence_" + k: v for k, v in prediction["probabilities"].items()},
        claim_summary_card_path=card)
    # Duration is known before flush because prediction rows are append-only.
    prediction.update(prediction_id=row.prediction_id, model_version_id=version.model_version_id,
        model_name=version.model_name, model_version=version.version,
        duration_ms=max(0, round((monotonic() - start) * 1000)))
    return row.id, prediction, None


def find_duplicates(claim):
    from backend.db.models import Claim
    findings = []
    hashes = [d.file_hash for d in claim.documents]
    if hashes:
        matches = db.session.execute(select(Document.claim_id, Document.file_hash).where(
            Document.file_hash.in_(hashes), Document.claim_id.is_not(None), Document.claim_id != claim.id)
            .distinct().order_by(Document.claim_id).limit(101)).all()
        findings.extend({"kind": "document_sha256", "claim_id": r.claim_id, "file_hash": r.file_hash} for r in matches[:100])
        if len(matches) > 100:
            findings.append({"kind": "additional_document_matches"})
    if claim.product_id:
        # Same registered product is a review indicator, never a fraud verdict.
        matches = db.session.scalars(select(Claim.id).where(Claim.product_id == claim.product_id,
            Claim.id != claim.id, Claim.status != "draft").order_by(Claim.id).limit(100)).all()
        findings.extend({"kind": "same_product", "claim_id": cid} for cid in matches)
    return findings


def evaluate_claim(claim, version, idempotency_key):
    existing = db.session.scalar(select(ClaimEvaluation).where(ClaimEvaluation.claim_id == claim.id,
        ClaimEvaluation.idempotency_key == idempotency_key))
    if existing:
        if existing.input_version != version:
            raise Conflict("The idempotency key was already used for a different claim version.")
        return existing, False
    if claim.version != version:
        raise Conflict("The claim changed. Refresh before evaluating.")
    if claim.status not in {"submitted", "under_evaluation", "manual_review"}:
        raise Conflict("This claim cannot be evaluated in its current state.")
    evidence, failures = snapshot(claim)
    rules, policy_snapshot = [], None
    code = current_app.config["WARRANTY_POLICY_BINDINGS"].get(claim.warranty.warranty_id) if claim.warranty else None
    try:
        policy, digest = load_policy(current_app.config["WARRANTY_POLICY_PATH"], code)
        policy_snapshot = {**policy.model_dump(), "source_sha256": digest}
        rules = check_rules(evidence, policy)
        warranty = evidence["registered_warranty"]
        from .warranty_calculations import calculate_warranty_expiry
        try:
            expected = calculate_warranty_expiry(calendar_date(evidence["purchase_date"]), policy.months).isoformat()
            consistent = warranty and warranty["start_date"] == evidence["purchase_date"] and (
                warranty["duration_months"] == policy.months and warranty["expiry_date"] == expected)
        except (ValueError, TypeError):
            consistent = False
        if not consistent:
            rules.append(rule("WARRANTY_POLICY_CONFLICT", "manual_review",
                "Registered warranty terms differ from the configured policy and need verification."))
    except PolicyError as exc:
        failures.append(exc.code)
        rules.append(rule(exc.code, "manual_review", "An applicable, validated warranty policy is unavailable."))
    try:
        features = model_features(claim, evidence)
    except (ValueError, TypeError):
        features = None
        failures.append("MODEL_INPUT_INVALID")
    predictions, ids = {}, {}
    for kind in ("python", "gtm"):
        if features is None:
            row_id, result, error = None, None, f"{kind.upper()}_MODEL_INPUT_INVALID"
        else:
            row_id, result, error = predict_one(kind, claim, features)
        predictions[kind], ids[kind] = result, row_id
        if error:
            failures.append(error)
    thresholds = ComparisonThresholds(current_app.config["COMPARISON_STRONG_GAP"],
        current_app.config["COMPARISON_ACCEPTABLE_GAP"], current_app.config["MODEL_MINIMUM_CONFIDENCE"])
    comparison = compare_predictions(predictions["python"], predictions["gtm"], thresholds)
    duplicates = find_duplicates(claim)  # A DB failure aborts the transaction, never means no duplicates.
    explanation = decide(rules, comparison, predictions["python"], predictions["gtm"], duplicates, failures)
    evidence.update(policy=policy_snapshot, model_inputs=features, predictions=predictions,
                    rules=rules, duplicates=duplicates, failures=sorted(set(failures)), engine_version="1")
    json.dumps(evidence, allow_nan=False)
    evaluation = ClaimEvaluation(claim_id=claim.id, input_version=version, idempotency_key=idempotency_key,
        python_prediction_id=ids["python"], gtm_prediction_id=ids["gtm"], evidence=evidence,
        comparison=comparison, decision=explanation["decision"], explanation=explanation)
    db.session.add(evaluation)
    for item in rules:
        db.session.add(RuleResult(claim_id=claim.id, rule_name=item["code"], rule_code=item["code"],
            rule_category="warranty_evaluation", result=item["result"],
            severity="info" if item["result"] == "passed" else "warning", details=item,
            policy_version=policy_snapshot["version"] if policy_snapshot else "unavailable"))
    # Recommendations are advisory. Every evaluated claim is available for adjudication.
    claim.final_decision = evaluation.decision
    claim.status, claim.manual_review_required = "manual_review", True
    claim.version += 1
    db.session.flush()
    return evaluation, True


def evaluation_json(evaluation, *, internal=False):
    result = {"id": evaluation.id, "evaluation_id": evaluation.evaluation_id,
        "decision": evaluation.decision, "created_at": (
            evaluation.created_at.replace(tzinfo=timezone.utc) if evaluation.created_at.tzinfo is None
            else evaluation.created_at.astimezone(timezone.utc)).isoformat(),
        "explanation": evaluation.explanation, "comparison": evaluation.comparison,
        "predictions": evaluation.evidence["predictions"]}
    if internal:
        result["evidence"] = evaluation.evidence
    # Customer output excludes hashes, source serials, candidates and model features.
    return result
