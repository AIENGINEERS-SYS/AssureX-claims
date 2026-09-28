"""Load an explicit, versioned policy contract; prose policies require human review."""
import hashlib
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

DOCUMENT_TYPES = {"receipt", "invoice", "warranty_card", "product_image", "serial_number_image",
                  "fault_evidence", "diagnostic_report", "repair_report", "other"}


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    policy_code: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=80)
    months: StrictInt = Field(ge=1, le=1200)
    required_documents: list[str] = Field(max_length=20)
    exclusions: list[str] = Field(max_length=100)
    authorized_repair_required: StrictBool
    serial_case_sensitive: StrictBool
    final_day_inclusive: StrictBool

    @field_validator("required_documents", "exclusions")
    @classmethod
    def strings(cls, values, info):
        if any(not v.strip() or len(v) > 100 for v in values) or len(set(values)) != len(values):
            raise ValueError("Policy lists must contain unique, nonblank values")
        if info.field_name == "required_documents" and not set(values) <= DOCUMENT_TYPES:
            raise ValueError("Unknown document type")
        return values


class PolicyError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def load_policy(path, code):
    if not code:
        raise PolicyError("POLICY_NOT_FOUND")
    try:
        # Read once per evaluation: the digest and parsed rules describe identical bytes.
        raw = Path(path).read_bytes()
        if len(raw) > 1024 * 1024:
            raise ValueError("Policy file too large")
        source = json.loads(raw)
        records = source["policies"]
        if not isinstance(records, list) or len(records) > 1000:
            raise ValueError("Invalid policies")
        codes = [p["policy_code"] for p in records]
        if len(codes) != len(set(codes)):
            raise ValueError("Duplicate policy codes")
        selected = next((p for p in records if p["policy_code"] == code), None)
        if selected is None:
            raise PolicyError("POLICY_NOT_FOUND")
        # The existing research catalog is descriptive, not an adjudication contract.
        policy = Policy.model_validate(selected)
        return policy, hashlib.sha256(raw).hexdigest()
    except PolicyError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise PolicyError("POLICY_INVALID_OR_UNAVAILABLE") from None
