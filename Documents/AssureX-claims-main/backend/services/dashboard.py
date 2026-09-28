"""Role-scoped dashboard read models computed from persisted claims and evidence."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from flask import current_app
from sqlalchemy import and_, case, func, or_, select, union_all
from sqlalchemy.orm import aliased
from backend.db.models import (Claim, Document, DuplicateInvestigation, GTMPrediction,
    ModelVersion, Notification, Product, PythonPrediction, Review, RuleResult, User, Warranty)
from backend.extensions import db
from backend.api.claim_schemas import REQUIRED_DOCUMENTS
from .document_service import public_document_type, stored_document_type
from .warranty_calculations import current_date

WINDOWS = {"7d": 7, "30d": 30, "90d": 90, "12m": 365}


def stamp(value):
    return value.isoformat() if value else None


def count(query):
    return db.session.scalar(query) or 0


def paged(query, page=1, per_page=10):
    total = count(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = db.session.execute(query.limit(per_page).offset((page - 1) * per_page)).all()
    return rows, {"page": page, "per_page": per_page, "total": total,
        "pages": (total + per_page - 1) // per_page}


def warranty_rows(user_id=None):
    today = current_date()
    active = and_(Warranty.start_date <= today, Warranty.expiry_date >= today)
    priority = case((active, 3), (Warranty.expiry_date < today, 2), else_=1)
    ranked = select(Warranty.id.label("warranty_id"), Warranty.product_id, Warranty.start_date,
        Warranty.expiry_date, Warranty.extended_warranty,
        func.row_number().over(partition_by=Warranty.product_id, order_by=(priority.desc(),
            Warranty.extended_warranty.desc(), Warranty.expiry_date.desc(), Warranty.id.desc())).label("rn")).subquery()
    chosen = select(ranked).where(ranked.c.rn == 1).subquery()
    threshold = today + timedelta(days=current_app.config["WARRANTY_NEAR_EXPIRY_DAYS"])
    status = case((chosen.c.warranty_id.is_(None), "No Warranty"),
        (chosen.c.start_date > today, "Not Started"), (chosen.c.expiry_date < today, "Expired"),
        (chosen.c.extended_warranty.is_(True), "Extended Warranty"),
        (chosen.c.expiry_date <= threshold, "Near Expiry"), else_="Active")
    query = select(Product.id, Product.name, Product.user_id, chosen.c.expiry_date,
        status.label("status")).outerjoin(chosen, Product.id == chosen.c.product_id)
    if user_id is not None:
        query = query.where(Product.user_id == user_id)
    return query.subquery()


def warranty_summary(user_id=None):
    rows = warranty_rows(user_id)
    distribution = dict(db.session.execute(select(rows.c.status, func.count()).group_by(rows.c.status)).all())
    for label in ("Active", "Near Expiry", "Expired", "Extended Warranty", "No Warranty", "Not Started"):
        distribution.setdefault(label, 0)
    covered = sum(distribution[k] for k in ("Active", "Near Expiry", "Extended Warranty"))
    total = sum(distribution.values())
    today = current_date()
    upcoming = db.session.execute(select(rows.c.id, rows.c.name, rows.c.expiry_date).where(
        rows.c.status == "Near Expiry").order_by(rows.c.expiry_date, rows.c.id).limit(5)).all()
    nearest = db.session.scalar(select(func.min(rows.c.expiry_date)).where(
        rows.c.status.in_(("Active", "Near Expiry", "Extended Warranty"))))
    return {"total_products": total, "covered": covered, "expired": distribution["Expired"],
        "active_warranties": covered, "coverage_percentage": round(100 * covered / total, 1) if total else 0,
        "nearest_expiry_days": (nearest - today).days if nearest else None,
        "expiring_count": distribution["Near Expiry"], "distribution": distribution,
        "expiring": [{"product_id": row.id, "product_name": row.name,
            "expiry_date": row.expiry_date.isoformat(), "days_remaining": (row.expiry_date - today).days}
            for row in upcoming]}


def claim_counts(user_id=None):
    query = select(Claim.status, func.count()).group_by(Claim.status)
    if user_id is not None:
        query = query.where(Claim.user_id == user_id)
    values = dict(db.session.execute(query).all())
    return {"total": sum(values.values()), "draft": values.get("draft", 0),
        "submitted": values.get("submitted", 0), "approved": values.get("approved", 0),
        "rejected": values.get("rejected", 0), "under_review": sum(values.get(s, 0) for s in
            ("under_evaluation", "manual_review", "additional_information_required")),
        "closed": values.get("closed", 0), "by_status": values}


def customer_dashboard(user_id, page=1, per_page=10):
    warranty, claims = warranty_summary(user_id), claim_counts(user_id)
    rows, pagination = paged(select(Claim.id, Claim.claim_id, Claim.status, Claim.submitted_at,
        Claim.updated_at, Product.name.label("product_name")).outerjoin(
        Product, Product.id == Claim.product_id).where(Claim.user_id == user_id).
        order_by(Claim.updated_at.desc(), Claim.id.desc()), page, per_page)
    recent = [{"id": row.id, "claim_id": None if row.status == "draft" else row.claim_id,
        "product": row.product_name, "status": row.status, "submitted_at": stamp(row.submitted_at),
        "updated_at": stamp(row.updated_at), "href": f"/claims/draft/{row.id}" if row.status == "draft"
        else f"/claims/{row.claim_id}"} for row in rows]
    drafts = db.session.execute(select(Claim.id).where(Claim.user_id == user_id,
        Claim.status == "draft").order_by(Claim.updated_at.desc()).limit(5)).all()
    requests = db.session.execute(select(Claim.claim_id).where(Claim.user_id == user_id,
        Claim.status == "additional_information_required").order_by(Claim.updated_at.desc()).limit(5)).all()
    products = warranty_rows(user_id)
    incomplete = db.session.execute(select(products.c.id, products.c.name).where(
        products.c.status == "No Warranty").limit(5)).all()
    actions = ([{"kind": "resume_draft", "text": "Finish your claim draft", "href": f"/claims/draft/{r.id}"}
        for r in drafts] + [{"kind": "information_requested", "text": f"Information requested for {r.claim_id}",
        "href": f"/claims/{r.claim_id}"} for r in requests] +
        [{"kind": "warranty_expiry", "text": f"{w['product_name']} warranty expires in {w['days_remaining']} days",
        "href": f"/products/{w['product_id']}"} for w in warranty["expiring"]] +
        [{"kind": "complete_registration", "text": f"Add warranty details for {r.name}",
        "href": f"/products/{r.id}"} for r in incomplete])
    unread = count(select(func.count(Notification.id)).where(Notification.user_id == user_id,
        Notification.is_read.is_(False)))
    return {"as_of": stamp(datetime.now(timezone.utc)), "products": warranty, "claims": claims,
        "recent_claims": recent, "pagination": pagination, "actions": actions,
        "notifications_unread": unread, "charts": {"warranty_status": warranty["distribution"],
        "claims_over_time": trends("12m", "customer", user_id, "month")["series"]}}


def latest_predictions(model):
    from backend.db.models import ClaimEvaluation
    # An evaluation with a failed model must not inherit an older successful output.
    latest = select(ClaimEvaluation.claim_id, func.max(ClaimEvaluation.id).label("id")).group_by(
        ClaimEvaluation.claim_id).subquery()
    reference = (ClaimEvaluation.python_prediction_id if model is PythonPrediction else ClaimEvaluation.gtm_prediction_id)
    ranked = select(model.claim_id, model.predicted_class, model.top_confidence,
        model.confidence_invalid, func.row_number().over(partition_by=model.claim_id,
            order_by=(model.created_at.desc(), model.id.desc())).label("rn")).outerjoin(
                latest, latest.c.claim_id == model.claim_id).outerjoin(ClaimEvaluation,
                ClaimEvaluation.id == latest.c.id).where(
                    or_(latest.c.id.is_(None), model.id == reference)).subquery()
    return select(ranked).where(ranked.c.rn == 1).subquery()


def predictions():
    return latest_predictions(PythonPrediction), latest_predictions(GTMPrediction)


def disagreement_condition(py, gtm):
    return or_(py.c.predicted_class != gtm.c.predicted_class,
        func.abs(py.c.top_confidence - gtm.c.top_confidence) >= current_app.config["DASHBOARD_DISAGREEMENT_GAP"])


def review_scope(user_id, admin=False, claim=Claim):
    scope = and_(claim.status == "manual_review", claim.manual_review_required.is_(True))
    return scope if admin else and_(scope, or_(claim.assigned_reviewer_id.is_(None),
        claim.assigned_reviewer_id == user_id))


def duplicate_pairs(user_id=None):
    first, second, other = aliased(Document), aliased(Document), aliased(Claim)
    query = select(first.claim_id.label("claim_id"), second.claim_id.label("matching_claim_id"),
        first.file_hash).join(second, and_(first.file_hash == second.file_hash,
            first.claim_id < second.claim_id)).join(Claim, Claim.id == first.claim_id).join(
        other, other.id == second.claim_id).where(Claim.status != "draft", other.status != "draft")
    if user_id is not None:
        query = query.where(or_(review_scope(user_id), review_scope(user_id, claim=other)))
    return query.distinct().subquery()


def reviewer_dashboard(user_id, admin=False, page=1, per_page=10, search=""):
    scope = review_scope(user_id, admin)
    py, gtm = predictions()
    queue = select(Claim.id, Claim.claim_id, Claim.submitted_at, Claim.assigned_reviewer_id,
        User.full_name.label("customer"), Product.name.label("product"),
        py.c.confidence_invalid.label("py_risk"), gtm.c.confidence_invalid.label("gtm_risk"),
        py.c.predicted_class.label("py_class"), gtm.c.predicted_class.label("gtm_class"),
        py.c.top_confidence.label("py_conf"), gtm.c.top_confidence.label("gtm_conf")).join(
        User, Claim.user_id == User.id).outerjoin(Product, Claim.product_id == Product.id).outerjoin(
        py, py.c.claim_id == Claim.id).outerjoin(gtm, gtm.c.claim_id == Claim.id).where(scope)
    if search:
        term = "%" + search.replace("%", "\\%").replace("_", "\\_") + "%"
        queue = queue.where(or_(Claim.claim_id.ilike(term, escape="\\"),
            User.full_name.ilike(term, escape="\\"), Product.name.ilike(term, escape="\\")))
    rows, pagination = paged(queue.order_by(Claim.submitted_at.desc(), Claim.id.desc()), page, per_page)
    items = []
    for row in rows:
        scores = [float(x) for x in (row.py_risk, row.gtm_risk) if x is not None]
        risk = round(max(scores) * 100) if scores else None
        items.append({"id": row.id, "claim_id": row.claim_id, "customer": row.customer,
            "product": row.product, "submitted_at": stamp(row.submitted_at),
            "assigned_reviewer_id": row.assigned_reviewer_id, "risk_score": risk,
            "risk_source": "model_invalid_probability_proxy" if risk is not None else None,
            "priority": "Unscored" if risk is None else "High" if risk >= 75 else "Medium" if risk >= 40 else "Low",
            "model_outputs": {"python": {"class": row.py_class, "confidence": float(row.py_conf) if row.py_conf is not None else None},
                "gtm": {"class": row.gtm_class, "confidence": float(row.gtm_conf) if row.gtm_conf is not None else None}}})
    disagreements = db.session.execute(select(Claim.id, Claim.claim_id, py.c.predicted_class,
        py.c.top_confidence, gtm.c.predicted_class, gtm.c.top_confidence).join(
        py, py.c.claim_id == Claim.id).join(gtm, gtm.c.claim_id == Claim.id).where(
        scope, disagreement_condition(py, gtm)).order_by(Claim.submitted_at.desc()).limit(10)).all()
    missing_filters = [~select(Document.id).where(Document.claim_id == Claim.id,
        Document.document_type == stored_document_type(kind)).exists() for kind in REQUIRED_DOCUMENTS]
    ids = [row.id for row in db.session.execute(select(Claim.id).where(scope, or_(*missing_filters)).
        order_by(Claim.submitted_at.desc()).limit(10))]
    uploaded = defaultdict(set)
    if ids:
        for claim_id, kind in db.session.execute(select(Document.claim_id, Document.document_type).where(
                Document.claim_id.in_(ids))):
            uploaded[claim_id].add(public_document_type(kind))
    pair = duplicate_pairs(None if admin else user_id)
    decision = aliased(DuplicateInvestigation)
    first_claim, second_claim = aliased(Claim), aliased(Claim)
    duplicate_rows, duplicate_page = paged(select(pair.c.claim_id, pair.c.matching_claim_id,
        pair.c.file_hash, decision.status,
        (first_claim.id if admin else case((review_scope(user_id, claim=first_claim), first_claim.id),
            else_=second_claim.id)).label("reviewable_claim_id")).join(first_claim,
        first_claim.id == pair.c.claim_id).join(second_claim,
        second_claim.id == pair.c.matching_claim_id).outerjoin(decision, and_(decision.claim_id == pair.c.claim_id,
            decision.matching_claim_id == pair.c.matching_claim_id,
            decision.file_hash == pair.c.file_hash)).order_by(pair.c.claim_id.desc()), page, per_page)
    decisions = dict(db.session.execute(select(Review.decision, func.count()).where(
        Review.reviewer_user_id == user_id).group_by(Review.decision)).all()) if not admin else dict(
        db.session.execute(select(Review.decision, func.count()).group_by(Review.decision)).all())
    risk = case((py.c.confidence_invalid.is_(None), gtm.c.confidence_invalid),
        (gtm.c.confidence_invalid.is_(None), py.c.confidence_invalid),
        (py.c.confidence_invalid >= gtm.c.confidence_invalid, py.c.confidence_invalid),
        else_=gtm.c.confidence_invalid)
    bucket = case((risk.is_(None), "Unscored"), (risk < .40, "0–39"),
        (risk < .75, "40–74"), else_="75–100")
    counts = dict(db.session.execute(select(bucket, func.count()).select_from(Claim).
        outerjoin(py, py.c.claim_id == Claim.id).outerjoin(gtm, gtm.c.claim_id == Claim.id).
        where(scope).group_by(bucket)).all())
    risk_counts = {key: counts.get(key, 0) for key in ("0–39", "40–74", "75–100", "Unscored")}
    requests = select(Claim.id, Claim.claim_id, Claim.updated_at).where(
        Claim.status == "additional_information_required")
    if not admin:
        requests = requests.where(Claim.assigned_reviewer_id == user_id)
    info = db.session.execute(requests.order_by(Claim.updated_at.desc()).limit(10)).all()
    return {"as_of": stamp(datetime.now(timezone.utc)), "summary": {
        "total_manual_review": count(select(func.count(Claim.id)).where(scope)),
        "new_today": count(select(func.count(Claim.id)).where(scope, Claim.submission_date == current_date())),
        "pending": count(select(func.count(Claim.id)).where(scope)),
        "approved_reviews": decisions.get("approve", 0), "rejected_reviews": decisions.get("reject", 0)},
        "queue": items, "pagination": pagination,
        "disagreements": [{"id": r[0], "claim_id": r[1],
            "python": {"class": r[2], "confidence": float(r[3])},
            "gtm": {"class": r[4], "confidence": float(r[5])},
            "recommended_action": "Inspect evidence"} for r in disagreements],
        "missing_documents": [{"claim_id": cid, "missing": sorted(set(REQUIRED_DOCUMENTS) - uploaded[cid])}
            for cid in ids],
        "duplicates": [{"claim_id": r.claim_id, "matching_claim_id": r.matching_claim_id,
            "file_hash": r.file_hash, "similarity_score": 1.0, "risk_level": "Exact file match",
            "status": r.status or "pending", "reviewable_claim_id": r.reviewable_claim_id}
            for r in duplicate_rows],
        "duplicate_pagination": duplicate_page,
        "information_requests": [{"id": r.id, "claim_id": r.claim_id,
            "updated_at": stamp(r.updated_at)} for r in info],
        "charts": {"outcomes": {"Approved": decisions.get("approve", 0),
            "Rejected": decisions.get("reject", 0),
            "Escalated": decisions.get("request_information", 0)},
            "risk_distribution": risk_counts, "risk_distribution_scope": "visible_queue"}}


def percentage(numerator, denominator):
    return round(100 * numerator / denominator, 1) if denominator else 0.0


def disagreement_stats(start=None, end=None):
    py, gtm = predictions()
    query = select(func.count(), func.sum(case((disagreement_condition(py, gtm), 1), else_=0))).join(
        gtm, py.c.claim_id == gtm.c.claim_id).join(Claim, Claim.id == py.c.claim_id).where(
        Claim.status != "draft")
    if start:
        query = query.where(Claim.submitted_at >= start)
    if end:
        query = query.where(Claim.submitted_at < end)
    processed, disagree = db.session.execute(query).one()
    return {"processed": processed, "disagreements": disagree or 0,
        "rate": percentage(disagree or 0, processed)}


def confidence_stats(start=None, end=None):
    py, gtm = predictions()
    observations = union_all(select(py.c.claim_id, py.c.top_confidence),
        select(gtm.c.claim_id, gtm.c.top_confidence)).subquery()
    query = select(func.avg(observations.c.top_confidence), func.count()).join(
        Claim, Claim.id == observations.c.claim_id).where(Claim.status != "draft")
    if start:
        query = query.where(Claim.submitted_at >= start)
    if end:
        query = query.where(Claim.submitted_at < end)
    avg, samples = db.session.execute(query).one()
    return {"average": round(float(avg), 3) if avg is not None else None, "samples": samples}


def model_history(page=1, per_page=20):
    rows, pagination = paged(select(ModelVersion).order_by(ModelVersion.created_at.desc(),
        ModelVersion.id.desc()), page, per_page)
    result = []
    for (item,) in rows:
        values = item.metrics_json or {}
        result.append({"model_type": item.model_type, "model_name": item.model_name,
            "version": item.version, "is_active": item.is_active, "created_at": stamp(item.created_at),
            "metrics": {key: float(values[key]) if isinstance(values.get(key), (int, float, Decimal)) else None
                for key in ("accuracy", "precision", "recall", "f1")}})
    return {"items": result, **pagination}


def workload():
    pending = dict(db.session.execute(select(Claim.assigned_reviewer_id, func.count()).where(
        Claim.status == "manual_review", Claim.assigned_reviewer_id.is_not(None)).
        group_by(Claim.assigned_reviewer_id)).all())
    reviewers = db.session.execute(select(User.id, User.full_name).where(User.role == "reviewer",
        User.is_active.is_(True)).order_by(User.full_name)).all()
    seconds = ((func.julianday(Review.reviewed_at) - func.julianday(Claim.submitted_at)) * 86400
        if db.session.get_bind().dialect.name == "sqlite" else
        func.extract("epoch", Review.reviewed_at - Claim.submitted_at))
    averages = dict(db.session.execute(select(Review.reviewer_user_id, func.avg(seconds)).join(
        Claim, Review.claim_id == Claim.id).where(Review.decision.in_(("approve", "reject")),
        Claim.submitted_at.is_not(None)).group_by(Review.reviewer_user_id)).all())
    return {"pending_unassigned": count(select(func.count(Claim.id)).where(
        Claim.status == "manual_review", Claim.assigned_reviewer_id.is_(None))),
        "reviewers": [{"id": uid, "name": name, "assigned_pending": pending.get(uid, 0),
            "average_review_hours": round(max(0, averages[uid]) / 3600, 1) if averages.get(uid) is not None else None}
            for uid, name in reviewers]}


def admin_dashboard():
    now = datetime.now(timezone.utc)
    today = current_date()
    counts = claim_counts()
    decided = counts["approved"] + counts["rejected"]
    disagreement = disagreement_stats()
    confidence = confidence_stats()
    week = now - timedelta(days=7)
    last_disagreement, prior_disagreement = disagreement_stats(week), disagreement_stats(week - timedelta(days=7), week)
    last_confidence, prior_confidence = confidence_stats(week), confidence_stats(week - timedelta(days=7), week)
    pairs = duplicate_pairs()
    dispositions = dict(db.session.execute(select(DuplicateInvestigation.status, func.count()).group_by(
        DuplicateInvestigation.status)).all())
    fraud_claims = count(select(func.count(func.distinct(RuleResult.claim_id))).join(
        Claim, Claim.id == RuleResult.claim_id).where(Claim.status != "draft",
        RuleResult.rule_category == "fraud", RuleResult.result.in_(("failed", "warning"))))
    py, gtm = predictions()
    risk = case((py.c.confidence_invalid.is_(None), gtm.c.confidence_invalid),
        (gtm.c.confidence_invalid.is_(None), py.c.confidence_invalid),
        (py.c.confidence_invalid >= gtm.c.confidence_invalid, py.c.confidence_invalid),
        else_=gtm.c.confidence_invalid)
    bucket = case((risk >= .75, "high"), (risk >= .40, "medium"), else_="low")
    risk_rows = dict(db.session.execute(select(bucket, func.count()).select_from(Claim).
        outerjoin(py, py.c.claim_id == Claim.id).outerjoin(gtm, gtm.c.claim_id == Claim.id).
        where(Claim.status != "draft", risk.is_not(None)).group_by(bucket)).all())
    non_draft = count(select(func.count(Claim.id)).where(Claim.status != "draft"))
    warranty = warranty_summary()["distribution"]
    outcomes = {"Approved": counts["approved"], "Rejected": counts["rejected"],
        "Manual Review": counts["by_status"].get("manual_review", 0),
        "Pending": sum(counts["by_status"].get(s, 0) for s in
            ("submitted", "under_evaluation", "additional_information_required"))}
    observations = union_all(select(py.c.claim_id, py.c.top_confidence),
        select(gtm.c.claim_id, gtm.c.top_confidence)).subquery()
    cbucket = case((observations.c.top_confidence < .25, "0–24%"),
        (observations.c.top_confidence < .50, "25–49%"),
        (observations.c.top_confidence < .75, "50–74%"), else_="75–100%")
    confidence_distribution = dict(db.session.execute(select(cbucket, func.count()).join(
        Claim, Claim.id == observations.c.claim_id).where(Claim.status != "draft").group_by(cbucket)).all())
    return {"as_of": stamp(now), "claims": {"lifetime": counts["total"],
        "submitted_today": count(select(func.count(Claim.id)).where(Claim.submission_date == today)),
        "submitted_month": count(select(func.count(Claim.id)).where(
            Claim.submission_date >= today.replace(day=1))), "by_status": counts["by_status"]},
        "outcomes": {"valid": counts["approved"], "invalid": counts["rejected"],
            "manual_review": counts["by_status"].get("manual_review", 0),
            "valid_percentage": percentage(counts["approved"], decided),
            "invalid_percentage": percentage(counts["rejected"], decided)},
        "disagreement": {**disagreement, "weekly_change_percentage_points":
            round(last_disagreement["rate"] - prior_disagreement["rate"], 1)
            if last_disagreement["processed"] and prior_disagreement["processed"] else None},
        "model_confidence": {**confidence, "weekly_change":
            round(last_confidence["average"] - prior_confidence["average"], 3)
            if last_confidence["average"] is not None and prior_confidence["average"] is not None else None},
        "duplicate_alerts": {"active": max(0, count(select(func.count()).select_from(pairs)) -
            sum(dispositions.values())), "confirmed": dispositions.get("confirmed", 0),
            "false_positive": dispositions.get("false_positive", 0)},
        "fraud": {"high": risk_rows.get("high", 0), "medium": risk_rows.get("medium", 0),
            "low": risk_rows.get("low", 0), "scored_claims": sum(risk_rows.values()),
            "detected_claims": fraud_claims, "detection_rate": percentage(fraud_claims, non_draft),
            "risk_source": "model_invalid_probability_proxy"},
        "warranties": warranty, "reviewer_workload": workload(), "models": model_history()["items"],
        "charts": {"outcomes": outcomes, "warranties": warranty,
            "confidence_distribution": {key: confidence_distribution.get(key, 0) for key in
                ("0–24%", "25–49%", "50–74%", "75–100%")},
            "volume": trends("30d")["series"]}}


def trends(window="30d", role="admin", user_id=None, interval="day"):
    end = current_date()
    if window == "12m":
        year, month = divmod(end.year * 12 + end.month - 12, 12)
        start = date(year, month + 1, 1)
    else:
        start = end - timedelta(days=WINDOWS[window] - 1)
    days = (end - start).days + 1
    beginning = datetime.combine(start, datetime.min.time(), timezone.utc)
    sources = {"submitted": (Claim.submission_date, select(Claim.submission_date).where(
        Claim.submission_date >= start)), "approved": (Review.reviewed_at,
        select(Review.reviewed_at).join(Claim, Review.claim_id == Claim.id).where(
            Review.decision == "approve", Review.reviewed_at >= beginning))}
    if role == "admin":
        sources.update({"fraud_events": (RuleResult.created_at, select(RuleResult.created_at).where(
            RuleResult.rule_category == "fraud", RuleResult.result.in_(("failed", "warning")),
            RuleResult.created_at >= beginning)),
            "customers": (User.created_at, select(User.created_at).where(User.role == "customer",
                User.created_at >= beginning)),
            "warranty_expirations": (Warranty.expiry_date, select(Warranty.expiry_date).where(
                Warranty.expiry_date.between(start, end)))})
    series = {}
    for name, (column, query) in sources.items():
        if role == "customer":
            query = query.where(Claim.user_id == user_id)
        query = query.with_only_columns(func.date(column), func.count()).group_by(func.date(column))
        series[name] = {str(day): n for day, n in db.session.execute(query) if day is not None}
    points = [{"date": (start + timedelta(days=i)).isoformat(),
        **{name: values.get((start + timedelta(days=i)).isoformat(), 0) for name, values in series.items()}}
        for i in range(days)]
    if interval != "day":
        buckets = {}
        for point in points:
            parsed = date.fromisoformat(point["date"])
            key = ((parsed - timedelta(days=parsed.weekday())).isoformat() if interval == "week"
                else parsed.replace(day=1).isoformat())
            bucket = buckets.setdefault(key, {"date": key, **{name: 0 for name in series}})
            for name in series:
                bucket[name] += point[name]
        points = list(buckets.values())
    return {"window": window, "interval": interval, "start": start.isoformat(),
        "end": end.isoformat(), "series": points}
