"""Transaction-aware creation helpers for relationships that span several parents."""
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import Claim, GTMPrediction, ModelVersion, Product, PythonPrediction, Warranty


def create_claim(session: Session, *, user_id: int, product_id: int, warranty_id: int,
                 fault_date, fault_type: str, fault_description: str, damage_type: str | None = None) -> Claim:
    # Match product-management lock ordering so evidence cannot change while a claim is attached.
    product = session.scalar(select(Product).where(Product.id == product_id).with_for_update())
    warranty = session.scalar(select(Warranty).where(Warranty.id == warranty_id).with_for_update())
    if product is None or warranty is None or product.user_id != user_id or warranty.product_id != product_id:
        raise ValueError("Claim owner, product and warranty must refer to the same registered product")
    claim = Claim(user_id=user_id, product_id=product_id, warranty_id=warranty_id,
                  fault_date=fault_date, fault_type=fault_type, fault_description=fault_description,
                  damage_type=damage_type)
    session.add(claim)
    session.flush()
    return claim


def record_prediction(session: Session, *, model_version_id: int, claim_id: int,
                      predicted_class: str, confidence_valid: Decimal,
                      confidence_invalid: Decimal, confidence_manual_review: Decimal,
                      claim_summary_card_path: str | None = None):
    model = session.get(ModelVersion, model_version_id)
    if model is None or session.get(Claim, claim_id) is None:
        raise ValueError("Claim and model version must exist")
    scores = (confidence_valid, confidence_invalid, confidence_manual_review)
    if any(not 0 <= x <= 1 for x in scores) or abs(sum(scores) - 1) > Decimal("0.001"):
        raise ValueError("Probabilities must be between 0 and 1 and sum to 1")
    if predicted_class not in {"valid", "invalid", "manual_review"}:
        raise ValueError("Unknown prediction class")
    top = max(scores)
    if dict(zip(("valid", "invalid", "manual_review"), scores))[predicted_class] != top:
        raise ValueError("Predicted class must have the top confidence")
    if model.model_type == "gtm" and not claim_summary_card_path:
        raise ValueError("GTM prediction requires a summary card")
    cls = PythonPrediction if model.model_type == "python" else GTMPrediction
    kwargs = {"claim_summary_card_path": claim_summary_card_path} if model.model_type == "gtm" else {}
    prediction = cls(claim_id=claim_id, model_version_id=model_version_id, predicted_class=predicted_class,
                     confidence_valid=confidence_valid, confidence_invalid=confidence_invalid,
                     confidence_manual_review=confidence_manual_review, top_confidence=top, **kwargs)
    session.add(prediction)
    session.flush()
    return prediction
