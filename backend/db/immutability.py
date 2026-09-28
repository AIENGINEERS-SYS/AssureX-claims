"""Prevent ordinary ORM edits to evidence records; access controls will be added with APIs."""
from sqlalchemy import event
from .models import AuditLog, EvaluationResult, GTMPrediction, PythonPrediction, Review, RuleResult


def reject_change(mapper, connection, target) -> None:
    raise ValueError(f"{type(target).__name__} is append-only")


for record_type in (AuditLog, PythonPrediction, GTMPrediction, EvaluationResult, Review, RuleResult):
    event.listen(record_type, "before_update", reject_change)
    event.listen(record_type, "before_delete", reject_change)
