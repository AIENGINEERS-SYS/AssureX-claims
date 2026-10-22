"""Prevent ordinary ORM edits to evidence records; access controls will be added with APIs."""
from sqlalchemy import event, inspect
from .models import AuditLog, EvaluationResult, GTMPrediction, PythonPrediction, Review, RuleResult
from .assessment_models import AssessmentReport, AssessmentAttempt, AssessmentJob


def reject_change(mapper, connection, target) -> None:
    raise ValueError(f"{type(target).__name__} is append-only")


for record_type in (AuditLog, PythonPrediction, GTMPrediction, EvaluationResult, Review, RuleResult, AssessmentReport, AssessmentAttempt):
    event.listen(record_type, "before_update", reject_change)
    event.listen(record_type, "before_delete", reject_change)


@event.listens_for(AssessmentJob, "before_update")
def preserve_assessment_inputs(mapper, connection, target):
    state = inspect(target)
    for name in ("id", "claim_id", "version", "requested_by", "evidence_digest", "evidence_snapshot", "created_at"):
        if state.attrs[name].history.has_changes():
            raise ValueError("Assessment evidence and identity are immutable; create a new version")


event.listen(AssessmentJob, "before_delete", reject_change)
