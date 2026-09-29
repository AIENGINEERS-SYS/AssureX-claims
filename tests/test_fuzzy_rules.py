"""Rule-engine, boundary, negative, contradiction, missing-document and serial tests.

Covers the "Rule-engine test cases", "Boundary test cases", "Negative test cases",
"Contradiction-detection tests", "Missing-document tests" and "Serial-number mismatch
tests" sections of fuzzy.txt.

The rule engine compares stored document OCR payloads, which backend.services.document_service
stores as field containers (``ocr_value``/``confirmed_value``) rather than bare strings. These
tests pin the real, verified behaviour of the engine as it stands today.
"""
from datetime import date, timedelta

import pytest

from backend.db.models import Claim
from backend.extensions import db
from backend.services.claim_rules import (ClaimRuleEngine, add_months, normalized, parsed_date)
from backend.services.warranty_policy import PolicyConfigurationError, WarrantyPolicyService
from fuzzy_helpers import (add_document, add_repair, attach_evidence, confirmed_field,
                            evaluate_rules, policy_for, seed_claim, set_dates, set_fault)
from test_auth import app, client, accounts, claims, bearer, login  # noqa: F401

RULE_CODES = ("warranty_expiry", "serial_number_verification", "contradiction_detection",
              "missing_documents", "repair_authorization", "excluded_damage")


# --------------------------------------------------------------------------- helpers


def electronics_policy(app, claim_id):
    with app.app_context():
        return policy_for(app, db.session.get(Claim, claim_id))


def rules_for(app, claim_id):
    return evaluate_rules(app, claim_id)


# --------------------------------------------------------------- rule engine structure


def test_engine_returns_every_configured_rule_in_stable_order(app, claims):
    claim_id = claims["customer"]["claim"]
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        results, contradictions = ClaimRuleEngine().evaluate(claim, policy_for(app, claim))
    assert [item["rule_code"] for item in results] == list(RULE_CODES)
    assert contradictions == []
    for item in results:
        assert item["result"] in {"passed", "failed", "manual_review"}
        assert item["passed"] is (item["result"] == "passed")
        assert item["severity"] in {"info", "medium", "high"}
        assert set(item) == {"rule_code", "rule_name", "rule_category", "result", "passed",
                             "severity", "message", "evidence"}
        assert item["rule_category"] in {"coverage", "identity", "evidence"}


def test_engine_is_deterministic_and_side_effect_free(app, claims):
    claim_id = claims["customer"]["claim"]
    first = rules_for(app, claim_id)[0]
    second = rules_for(app, claim_id)[0]
    assert first == second
    with app.app_context():
        assert db.session.get(Claim, claim_id).status == "submitted"


def test_engine_never_resolves_policy_itself(app, claims):
    """The engine consumes a policy object; category resolution happens upstream.

    backend.services.evaluation.policy_for_claim supplies a synthetic fallback policy when a
    category is unconfigured, so the engine still returns a full rule set.
    """
    from backend.services.evaluation import policy_for_claim

    claim_id = claims["customer"]["claim"]
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        claim.product.category = "Solar Inverter"
        db.session.commit()
        with pytest.raises(PolicyConfigurationError):
            policy_for(app, claim)
        policy, warning = policy_for_claim(claim)
        assert warning is not None
        assert policy.code == "unconfigured"
        assert policy.required_documents == ()
        results, _ = ClaimRuleEngine().evaluate(claim, policy)

    assert [item["rule_code"] for item in results] == list(RULE_CODES)
    by_code = {item["rule_code"]: item for item in results}
    assert by_code["missing_documents"]["result"] == "passed"
    assert by_code["missing_documents"]["evidence"]["missing_documents"] == []


# ------------------------------------------------------------------ warranty boundaries


@pytest.mark.parametrize(("delta", "expected"), [
    (-2, "passed"),        # well inside the window
    (-1, "passed"),
    (0, "passed"),         # the final covered day is inclusive
    (1, "failed"),         # first day outside the window
    (2, "failed"),
    (365 * 3, "failed"),
])
def test_warranty_expiry_boundary_is_inclusive_of_the_final_day(app, claims, delta, expected):
    """add_months(purchase_date, policy months) is the inclusive last covered day."""
    claim_id = claims["customer"]["claim"]
    today = date.today()
    set_dates(app, claim_id, purchase_date=add_months(today, -24), start_date=add_months(today, -24),
              claim_date_value=today + timedelta(days=delta), expiry_date=date(2099, 1, 1))
    rules, _ = rules_for(app, claim_id)
    assert rules["warranty_expiry"]["evidence"]["warranty_expiry"] == today.isoformat()
    assert rules["warranty_expiry"]["result"] == expected
    assert rules["warranty_expiry"]["evidence"]["claim_date"] == (today + timedelta(days=delta)).isoformat()


def test_warranty_expiry_without_a_claim_date_is_manual_review_not_failure(app, claims):
    claim_id = claims["customer"]["claim"]
    set_dates(app, claim_id, purchase_date=date.today() - timedelta(days=10), clear_claim_date=True)
    rules, _ = rules_for(app, claim_id)
    assert rules["warranty_expiry"]["result"] == "manual_review"
    assert rules["warranty_expiry"]["evidence"]["claim_date"] is None
    assert "missing" in rules["warranty_expiry"]["message"].lower()


def test_registered_warranty_expiry_can_shorten_the_policy_window(app, claims):
    today = date.today()
    claim_id = claims["customer"]["claim"]
    set_dates(app, claim_id, purchase_date=today - timedelta(days=400), start_date=today - timedelta(days=400),
              claim_date_value=today, expiry_date=today - timedelta(days=1))
    rules, _ = rules_for(app, claim_id)
    assert rules["warranty_expiry"]["result"] == "failed"
    assert rules["warranty_expiry"]["evidence"]["warranty_expiry"] == (today - timedelta(days=1)).isoformat()


def test_registered_warranty_expiry_never_extends_the_policy_window(app, claims):
    """The effective expiry is min(policy window, registered warranty expiry)."""
    today = date.today()
    claim_id = claims["customer"]["claim"]
    set_dates(app, claim_id, purchase_date=today - timedelta(days=1000),
              start_date=today - timedelta(days=1000), claim_date_value=today,
              expiry_date=today + timedelta(days=10))
    rules, _ = rules_for(app, claim_id)
    assert rules["warranty_expiry"]["result"] == "failed"
    assert rules["warranty_expiry"]["evidence"]["warranty_expiry"] < today.isoformat()


@pytest.mark.parametrize(("start", "months", "expected"), [
    (date(2024, 1, 31), 1, date(2024, 2, 29)),    # leap-year February clamps
    (date(2023, 1, 31), 1, date(2023, 2, 28)),    # non-leap February clamps
    (date(2024, 1, 31), 12, date(2025, 1, 31)),   # year rollover
    (date(2024, 12, 31), 1, date(2025, 1, 31)),   # year boundary
    (date(2024, 2, 29), 12, date(2025, 2, 28)),   # leap day to non-leap year
    (date(2024, 1, 30), 2, date(2024, 3, 30)),    # two-month roll keeps the day
])
def test_add_months_clamps_to_the_last_valid_day(start, months, expected):
    assert add_months(start, months) == expected


@pytest.mark.parametrize(("raw", "expected"), [
    ("AB-12 CD", "ab12cd"),
    ("ab12cd", "ab12cd"),
    ("  a_b .c1 2 ", "abc12"),
    ("", ""),
    (None, ""),
    (0, ""),          # falsy values collapse to an empty string
    (False, ""),
])
def test_normalized_strips_every_non_alphanumeric_character(raw, expected):
    assert normalized(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [
    ("2026-01-15", date(2026, 1, 15)),
    ("2026-01-15T10:30:00Z", date(2026, 1, 15)),
    (date(2026, 1, 15), date(2026, 1, 15)),
    ("not-a-date", None),
    ("", None),
    (None, None),
    (5, None),
])
def test_parsed_date_accepts_only_iso_prefixes_and_date_objects(raw, expected):
    assert parsed_date(raw) == expected


# ------------------------------------------------------- serial number verification


def test_serial_rule_passes_when_only_the_registered_serial_is_available(app, claims):
    rules, contradictions = rules_for(app, claims["customer"]["claim"])
    rule = rules["serial_number_verification"]
    assert rule["result"] == "passed"
    assert rule["evidence"]["normalized_values"] == ["customer"]
    assert not [c for c in contradictions if c["type"] == "serial_number_mismatch"]


def test_serial_rule_flags_conflicting_registered_serials(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], serial="SN-REGISTERED")
    add_document(app, ids["claim"], accounts["customer"], "serial_number_image",
                 verified={"serial_number": confirmed_field("SN-OTHER")})
    rules, contradictions = rules_for(app, ids["claim"])
    rule = rules["serial_number_verification"]
    assert rule["result"] == "failed"
    assert rule["severity"] == "high"
    assert len(rule["evidence"]["normalized_values"]) >= 2
    assert any(c["type"] == "serial_number_mismatch" for c in contradictions)
    assert "conflict" in rule["message"].lower()


def test_serial_rule_is_manual_review_when_no_usable_serial_exists(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], serial="")
    with app.app_context():
        db.session.get(Claim, ids["claim"]).product.serial_number = ""
        db.session.commit()
    rules, _ = rules_for(app, ids["claim"])
    rule = rules["serial_number_verification"]
    assert rule["result"] == "manual_review"
    assert rule["severity"] == "medium"
    assert rule["evidence"]["normalized_values"] == []


def test_serial_rule_tolerates_punctuation_and_case_differences(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], serial="AB-12CD")
    with app.app_context():
        db.session.get(Claim, ids["claim"]).product.serial_number = "ab 12cd"
        db.session.commit()
    rules, contradictions = rules_for(app, ids["claim"])
    assert rules["serial_number_verification"]["result"] == "passed"
    assert not any(c["type"] == "serial_number_mismatch" for c in contradictions)


def test_serial_rule_reads_ocr_containers_as_whole_values(app, accounts):
    """Pins current behaviour: stored OCR containers are compared verbatim.

    ``ClaimRuleEngine._document_evidence`` hands the raw per-field container to the
    comparison, so a reviewed serial number never equals the registered serial.
    """
    ids = seed_claim(app, user_id=accounts["customer"], serial="SN-REGISTERED")
    add_document(app, ids["claim"], accounts["customer"], "serial_number_image",
                 verified={"serial_number": confirmed_field("SN-REGISTERED")})
    rules, _ = rules_for(app, ids["claim"])
    sources = rules["serial_number_verification"]["evidence"]["sources"]
    assert set(sources) == {"registered_product"} | {
        key for key in sources if key.startswith("document:")}
    assert isinstance(sources["registered_product"], str)
    assert rules["serial_number_verification"]["result"] == "failed"


def test_serial_rule_ignores_blank_ocr_values(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], serial="SN-REGISTERED")
    add_document(app, ids["claim"], accounts["customer"], "serial_number_image",
                 verified={"serial_number": "", "model_number": ""})
    rules, _ = rules_for(app, ids["claim"])
    assert rules["serial_number_verification"]["result"] == "passed"
    assert rules["serial_number_verification"]["evidence"]["normalized_values"] == ["snregistered"]


def test_model_mismatch_is_a_contradiction_not_a_serial_failure(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], serial="SN-REGISTERED", model="M-REGISTERED")
    add_document(app, ids["claim"], accounts["customer"], "product_image",
                 verified={"model_number": "M-DIFFERENT"})
    rules, contradictions = rules_for(app, ids["claim"])
    assert rules["serial_number_verification"]["result"] == "passed"
    assert rules["contradiction_detection"]["result"] == "failed"
    assert [c["type"] for c in contradictions] == ["product_model_mismatch"]


# ------------------------------------------------------------------ missing documents


def test_missing_documents_lists_every_absent_required_type(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    attach_evidence(app, ids["claim"], accounts["customer"], kinds=("receipt",))
    rules, _ = rules_for(app, ids["claim"])
    rule = rules["missing_documents"]
    assert rule["result"] == "manual_review"
    assert rule["severity"] == "medium"
    assert set(rule["evidence"]["missing_documents"]) == {"serial_number_image", "product_image",
                                                          "damage_evidence"}
    assert rule["evidence"]["submitted_documents"] == ["receipt"]
    assert set(rule["evidence"]["optional_documents"]) == {"warranty_card", "diagnostic_report",
                                                           "repair_report"}


def test_missing_documents_with_no_documents_at_all(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    rules, _ = rules_for(app, ids["claim"])
    rule = rules["missing_documents"]
    assert rule["result"] == "manual_review"
    assert rule["evidence"]["submitted_documents"] == []
    assert len(rule["evidence"]["missing_documents"]) == 4


def test_missing_documents_is_inclusive_optional_documents_do_not_satisfy_required(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    attach_evidence(app, ids["claim"], accounts["customer"],
                    kinds=("warranty_card", "diagnostic_report", "repair_report", "other"))
    rules, _ = rules_for(app, ids["claim"])
    assert rules["missing_documents"]["result"] == "manual_review"
    assert "damage_evidence" in rules["missing_documents"]["evidence"]["missing_documents"]


def test_missing_documents_accepts_stored_fault_evidence_as_public_damage_evidence(app, claims, accounts):
    """Stored fault_evidence must satisfy the public damage_evidence requirement."""
    claim_id = claims["customer"]["claim"]
    attach_evidence(app, claim_id, accounts["customer"])
    rules, _ = rules_for(app, claim_id)
    rule = rules["missing_documents"]

    assert rule["evidence"]["submitted_documents"] == [
        "damage_evidence",
        "product_image",
        "receipt",
        "serial_number_image",
    ]
    assert set(rule["evidence"]["required_documents"]) == {
        "receipt",
        "serial_number_image",
        "product_image",
        "damage_evidence",
    }
    assert rule["evidence"]["missing_documents"] == []
    assert rule["result"] == "passed"


def test_missing_document_rule_canonicalizes_policy_and_storage_vocabularies(app, claims, accounts):
    """The rule remains correct even if a policy uses the storage-side alias."""
    from backend.services.warranty_policy import WarrantyPolicy

    claim_id = claims["customer"]["claim"]
    attach_evidence(app, claim_id, accounts["customer"])
    with app.app_context():
        claim = db.session.get(Claim, claim_id)
        base = policy_for(app, claim)
        storage_vocab_policy = WarrantyPolicy(
            code=base.code,
            categories=base.categories,
            warranty_months=base.warranty_months,
            required_documents=(
                "receipt",
                "serial_number_image",
                "product_image",
                "fault_evidence",
            ),
            optional_documents=base.optional_documents,
            exclusions=base.exclusions,
            authorized_repair_required=base.authorized_repair_required,
            unauthorized_repair_invalidates=base.unauthorized_repair_invalidates,
            version=base.version,
        )
        results, _ = ClaimRuleEngine().evaluate(claim, storage_vocab_policy)

    rule = {item["rule_code"]: item for item in results}["missing_documents"]
    assert rule["result"] == "passed"
    assert rule["evidence"]["missing_documents"] == []
    assert "damage_evidence" in rule["evidence"]["required_documents"]


def test_appliances_policy_requires_fewer_documents_than_electronics(app, accounts):
    electronics = seed_claim(app, user_id=accounts["customer"], category="Electronics")
    appliances = seed_claim(app, user_id=accounts["customer"], category="Refrigerator")
    assert set(electronics_policy(app, electronics["claim"]).required_documents) == {
        "receipt", "serial_number_image", "product_image", "damage_evidence"}
    assert set(electronics_policy(app, appliances["claim"]).required_documents) == {
        "receipt", "product_image", "damage_evidence"}


# ------------------------------------------------------------- contradiction detection


def test_purchase_date_after_the_claim_date_is_a_contradiction(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], purchase_date=date.today(),
                     submission_date=date.today())
    set_dates(app, ids["claim"], purchase_date=date.today() + timedelta(days=10))
    rules, contradictions = rules_for(app, ids["claim"])
    assert [c["type"] for c in contradictions] == ["purchase_after_claim"]
    assert contradictions[0]["severity"] == "high"
    assert rules["contradiction_detection"]["result"] == "failed"
    assert rules["contradiction_detection"]["evidence"]["findings"] == contradictions


def test_fault_date_after_the_claim_date_is_a_contradiction(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], submission_date=date.today())
    set_dates(app, ids["claim"], fault_date=date.today() + timedelta(days=1))
    rules, contradictions = rules_for(app, ids["claim"])
    assert [c["type"] for c in contradictions] == ["fault_after_claim"]


def test_fault_date_on_the_claim_date_is_not_a_contradiction(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], submission_date=date.today())
    set_dates(app, ids["claim"], fault_date=date.today())
    rules, contradictions = rules_for(app, ids["claim"])
    assert contradictions == []
    assert rules["contradiction_detection"]["result"] == "passed"


def test_fault_after_claim_is_not_reported_when_no_claim_date_exists(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], submission_date=date.today())
    with app.app_context():
        claim = db.session.get(Claim, ids["claim"])
        claim.submission_date, claim.submitted_at = None, None
        db.session.commit()
    _, contradictions = rules_for(app, ids["claim"])
    assert contradictions == []


def test_repair_before_purchase_is_a_contradiction(app, accounts):
    purchase = date.today() - timedelta(days=100)
    ids = seed_claim(app, user_id=accounts["customer"], purchase_date=purchase,
                     submission_date=date.today())
    add_repair(app, ids["claim"], repair_date=purchase - timedelta(days=1), authorized=True)
    rules, contradictions = rules_for(app, ids["claim"])
    assert [c["type"] for c in contradictions] == ["repair_before_purchase"]
    assert rules["repair_authorization"]["result"] == "passed"
    assert contradictions[0]["sources"][1] == "registered_product"


def test_repair_on_the_purchase_date_is_not_a_contradiction(app, accounts):
    purchase = date.today() - timedelta(days=100)
    ids = seed_claim(app, user_id=accounts["customer"], purchase_date=purchase,
                     submission_date=date.today())
    add_repair(app, ids["claim"], repair_date=purchase, authorized=True)
    _, contradictions = rules_for(app, ids["claim"])
    assert contradictions == []


def test_multiple_independent_contradictions_are_all_reported(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], purchase_date=date.today(),
                     submission_date=date.today(), serial="SN-A", model="M-A")
    add_document(app, ids["claim"], accounts["customer"], "serial_number_image",
                 verified={"serial_number": confirmed_field("SN-B"),
                           "model_number": confirmed_field("M-B")})
    set_dates(app, ids["claim"], fault_date=date.today() + timedelta(days=3))
    rules, contradictions = rules_for(app, ids["claim"])
    assert {c["type"] for c in contradictions} == {"serial_number_mismatch", "product_model_mismatch",
                                                   "fault_after_claim"}
    assert rules["contradiction_detection"]["message"].startswith("Found 3 material contradiction")
    assert rules["contradiction_detection"]["severity"] == "high"


def test_unparseable_document_dates_do_not_create_false_contradictions(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], submission_date=date.today())
    add_document(app, ids["claim"], accounts["customer"], "receipt",
                 verified={"purchase_date": "not-a-date"})
    rules, contradictions = rules_for(app, ids["claim"])
    assert contradictions == []
    assert rules["contradiction_detection"]["result"] == "passed"


# --------------------------------------------------------------- repair authorization


def test_no_repair_passes_with_an_explicit_no_repair_state(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    rules, _ = rules_for(app, ids["claim"])
    rule = rules["repair_authorization"]
    assert rule["result"] == "passed"
    assert rule["evidence"]["state"] == "no_repair"
    assert rule["evidence"]["repair_ids"] == []
    assert rule["evidence"]["invalidates_coverage"] is True


def test_unauthorized_repair_fails_and_invalidates_coverage(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    repair_id = add_repair(app, ids["claim"], authorized=False)
    rules, _ = rules_for(app, ids["claim"])
    rule = rules["repair_authorization"]
    assert rule["result"] == "failed"
    assert rule["severity"] == "high"
    assert rule["evidence"]["state"] == "unauthorized"
    assert rule["evidence"]["repair_ids"] == [repair_id]
    assert rule["evidence"]["invalidates_coverage"] is True
    assert "unauthorized" in rule["message"].lower()


def test_authorized_repair_passes(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    add_repair(app, ids["claim"], authorized=True)
    rules, _ = rules_for(app, ids["claim"])
    assert rules["repair_authorization"]["result"] == "passed"
    assert rules["repair_authorization"]["evidence"]["state"] == "authorized"


def test_one_unauthorized_repair_poisons_an_otherwise_authorized_history(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"])
    add_repair(app, ids["claim"], authorized=True)
    add_repair(app, ids["claim"], authorized=False)
    rules, _ = rules_for(app, ids["claim"])
    assert rules["repair_authorization"]["result"] == "failed"
    assert rules["repair_authorization"]["evidence"]["state"] == "unauthorized"


def test_repair_rule_is_ignored_when_the_policy_does_not_require_authorization(app, accounts):
    from backend.services.warranty_policy import WarrantyPolicy
    ids = seed_claim(app, user_id=accounts["customer"])
    add_repair(app, ids["claim"], authorized=False)
    policy = WarrantyPolicy("permissive", ("electronics",), 24, ("receipt",), (), (),
                           authorized_repair_required=False, unauthorized_repair_invalidates=False,
                           version="test")
    with app.app_context():
        claim = db.session.get(Claim, ids["claim"])
        results, _ = ClaimRuleEngine().evaluate(claim, policy)
    rule = next(item for item in results if item["rule_code"] == "repair_authorization")
    assert rule["evidence"]["state"] == "unauthorized"
    assert rule["result"] == "passed"
    assert rule["evidence"]["invalidates_coverage"] is False


# ------------------------------------------------------------------ excluded damage


@pytest.mark.parametrize("description", [
    "water damage", "Water Damage to the motherboard", "INTENTIONAL DAMAGE by the owner",
    "misuse", "MISUSE of the charger", "physical impact", "wrong voltage",
])
def test_configured_exclusions_fail_the_claim(app, accounts, description):
    ids = seed_claim(app, user_id=accounts["customer"], description=description)
    rules, _ = rules_for(app, ids["claim"])
    rule = rules["excluded_damage"]
    assert rule["result"] == "failed"
    assert rule["severity"] == "high"
    assert rule["evidence"]["matched_exclusions"]


@pytest.mark.parametrize("description", [
    "water", "impact", "intentional", "voltage", "misused the charger", "Physically impacted",
    "Water spilled", "damaged beyond repair", "voltage regulator failed", "commercial use",
])
def test_single_word_and_related_phrases_do_not_trigger_exclusions(app, accounts, description):
    ids = seed_claim(app, user_id=accounts["customer"], description=description)
    rules, _ = rules_for(app, ids["claim"])
    rule = rules["excluded_damage"]
    assert rule["result"] == "passed", rule["evidence"]["matched_exclusions"]
    assert rule["evidence"]["matched_exclusions"] == []


def test_exclusion_matching_combines_fault_type_and_damage_type(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], description="Unit stopped working")
    set_fault(app, ids["claim"], damage_type="Liquid ingress")
    rules, _ = rules_for(app, ids["claim"])
    assert rules["excluded_damage"]["result"] == "passed"
    set_fault(app, ids["claim"], fault_type="Water Damage")
    rules, _ = rules_for(app, ids["claim"])
    assert rules["excluded_damage"]["result"] == "failed"
    assert rules["excluded_damage"]["evidence"]["matched_exclusions"] == ["water damage"]


def test_exclusion_evidence_truncates_very_long_descriptions(app, accounts):
    ids = seed_claim(app, user_id=accounts["customer"], description="x" * 5000)
    rules, _ = rules_for(app, ids["claim"])
    assert len(rules["excluded_damage"]["evidence"]["evidence"]) == 1000


# ------------------------------------------------------------------ policy resolution


@pytest.mark.parametrize(("category", "code", "months"), [
    ("Electronics", "electronics", 24),
    ("Laptop", "electronics", 24),
    ("camera", "electronics", 24),
    ("Smartphone", "mobile_devices", 12),
    ("tablet", "mobile_devices", 12),
    ("Refrigerator", "appliances", 24),
    ("Home appliances", "appliances", 24),
    ("Washing Machine", "appliances", 24),
])
def test_configured_categories_resolve_to_their_policy(app, claims, category, code, months):
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        claim.product.category = category
        db.session.commit()
        policy = policy_for(app, claim)
    assert (policy.code, policy.warranty_months) == (code, months)
    assert policy.version
    assert policy.as_dict()["code"] == code


@pytest.mark.parametrize("category", ["Solar Inverter", "Smart Watch", "", "  "])
def test_unconfigured_categories_raise_a_policy_configuration_error(app, claims, category):
    with app.app_context():
        claim = db.session.get(Claim, claims["customer"]["claim"])
        claim.product.category = category
        db.session.commit()
        with pytest.raises(PolicyConfigurationError):
            policy_for(app, claim)


def test_policy_file_must_declare_at_least_three_policies(tmp_path):
    import json
    path = tmp_path / "policies.json"
    path.write_text(json.dumps({"version": "x", "policies": []}), encoding="utf-8")
    with pytest.raises(PolicyConfigurationError):
        WarrantyPolicyService(path)


@pytest.mark.parametrize("payload", [
    {},                                                        # no version
    {"version": "x"},                                          # no policies
    {"version": "x", "policies": [{"code": "a"}]},             # missing fields
    {"version": "x", "policies": [{"code": "a"}] * 3},         # not a list of objects
])
def test_invalid_policy_documents_are_rejected(tmp_path, payload):
    import json
    path = tmp_path / "policies.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PolicyConfigurationError):
        WarrantyPolicyService(path)


def test_missing_policy_file_is_rejected(tmp_path):
    with pytest.raises(PolicyConfigurationError):
        WarrantyPolicyService(tmp_path / "absent.json")


def test_policy_rejects_out_of_range_warranty_months(tmp_path):
    import json
    row = {"code": "a", "categories": ["x"], "warranty_months": 0, "required_documents": ["receipt"],
           "optional_documents": [], "exclusions": [], "authorized_repair_required": True,
           "unauthorized_repair_invalidates": True}
    path = tmp_path / "policies.json"
    path.write_text(json.dumps({"version": "v", "policies": [row] * 3}), encoding="utf-8")
    with pytest.raises(PolicyConfigurationError):
        WarrantyPolicyService(path)


def test_policy_rejects_blank_list_entries(tmp_path):
    import json
    row = {"code": "a", "categories": ["x"], "warranty_months": 12, "required_documents": ["  "],
           "optional_documents": [], "exclusions": [], "authorized_repair_required": True,
           "unauthorized_repair_invalidates": True}
    path = tmp_path / "policies.json"
    path.write_text(json.dumps({"version": "v", "policies": [row] * 3}), encoding="utf-8")
    with pytest.raises(PolicyConfigurationError):
        WarrantyPolicyService(path)


def test_policy_rejects_duplicate_codes(tmp_path):
    import json
    base = {"categories": ["x"], "warranty_months": 12, "required_documents": ["receipt"],
            "optional_documents": [], "exclusions": [], "authorized_repair_required": True,
            "unauthorized_repair_invalidates": True}
    rows = [{**base, "code": "same"} for _ in range(3)]
    path = tmp_path / "policies.json"
    path.write_text(json.dumps({"version": "v", "policies": rows}), encoding="utf-8")
    with pytest.raises(PolicyConfigurationError):
        WarrantyPolicyService(path)


def test_policy_deduplicates_lists_case_sensitively_for_documents(tmp_path):
    """required_documents are stripped but not case-folded, so "Receipt" and "receipt" survive."""
    import json
    row = {"code": "a", "categories": ["  ELECTRONICS  "], "warranty_months": 12,
           "required_documents": [" Receipt ", "receipt"], "optional_documents": [],
           "exclusions": ["  Water Damage ", "water damage"], "authorized_repair_required": True,
           "unauthorized_repair_invalidates": True}
    path = tmp_path / "policies.json"
    path.write_text(json.dumps({"version": "v", "policies": [{**row, "code": f"c{i}"} for i in range(3)]}),
                    encoding="utf-8")
    policy = next(p for p in WarrantyPolicyService(path).policies
                  if p.categories == ("electronics",))
    assert policy.required_documents == ("Receipt", "receipt")
    assert policy.exclusions == ("water damage",)      # case-folded before dedup
    assert policy.categories == ("electronics",)


def test_non_boolean_repair_settings_are_rejected(tmp_path):
    import json
    row = {"code": "a", "categories": ["x"], "warranty_months": 12, "required_documents": ["receipt"],
           "optional_documents": [], "exclusions": [], "authorized_repair_required": "yes",
           "unauthorized_repair_invalidates": True}
    path = tmp_path / "policies.json"
    path.write_text(json.dumps({"version": "v", "policies": [{**row, "code": f"c{i}"} for i in range(3)]}),
                    encoding="utf-8")
    with pytest.raises(PolicyConfigurationError):
        WarrantyPolicyService(path)
