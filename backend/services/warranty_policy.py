"""Validated, externally configured warranty policies."""
import json
from dataclasses import dataclass
from pathlib import Path


class PolicyConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class WarrantyPolicy:
    code: str
    categories: tuple[str, ...]
    warranty_months: int
    required_documents: tuple[str, ...]
    optional_documents: tuple[str, ...]
    exclusions: tuple[str, ...]
    authorized_repair_required: bool
    unauthorized_repair_invalidates: bool
    version: str

    def as_dict(self):
        return {
            "code": self.code,
            "categories": list(self.categories),
            "warranty_months": self.warranty_months,
            "required_documents": list(self.required_documents),
            "optional_documents": list(self.optional_documents),
            "exclusions": list(self.exclusions),
            "authorized_repair_required": self.authorized_repair_required,
            "unauthorized_repair_invalidates": self.unauthorized_repair_invalidates,
            "version": self.version,
        }


class WarrantyPolicyService:
    REQUIRED_KEYS = {
        "code", "categories", "warranty_months", "required_documents", "optional_documents",
        "exclusions", "authorized_repair_required", "unauthorized_repair_invalidates",
    }

    def __init__(self, path):
        self.path = Path(path)
        self.version, self.policies = self._load()

    def _load(self):
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PolicyConfigurationError("Warranty policy configuration is unavailable or invalid.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("version"), str):
            raise PolicyConfigurationError("Warranty policy configuration requires a version.")
        rows = payload.get("policies")
        if not isinstance(rows, list) or len(rows) < 3:
            raise PolicyConfigurationError("At least three warranty policies are required.")
        policies = []
        for row in rows:
            if not isinstance(row, dict) or self.REQUIRED_KEYS - row.keys():
                raise PolicyConfigurationError("A warranty policy is missing required fields.")
            list_fields = ("categories", "required_documents", "optional_documents", "exclusions")
            if any(not isinstance(row[key], list) or any(not isinstance(v, str) or not v.strip()
                    for v in row[key]) for key in list_fields):
                raise PolicyConfigurationError("Warranty policy lists must contain nonblank strings.")
            if type(row["warranty_months"]) is not int or not 1 <= row["warranty_months"] <= 1200:
                raise PolicyConfigurationError("Warranty months must be between 1 and 1200.")
            if type(row["authorized_repair_required"]) is not bool or type(row["unauthorized_repair_invalidates"]) is not bool:
                raise PolicyConfigurationError("Warranty repair settings must be boolean.")
            policies.append(WarrantyPolicy(
                code=row["code"].strip(),
                categories=tuple(v.strip().casefold() for v in row["categories"]),
                warranty_months=row["warranty_months"],
                required_documents=tuple(dict.fromkeys(v.strip() for v in row["required_documents"])),
                optional_documents=tuple(dict.fromkeys(v.strip() for v in row["optional_documents"])),
                exclusions=tuple(dict.fromkeys(v.strip().casefold() for v in row["exclusions"])),
                authorized_repair_required=row["authorized_repair_required"],
                unauthorized_repair_invalidates=row["unauthorized_repair_invalidates"],
                version=payload["version"],
            ))
        if len({p.code for p in policies}) != len(policies):
            raise PolicyConfigurationError("Warranty policy codes must be unique.")
        return payload["version"], tuple(policies)

    def for_product(self, product):
        category = (product.category or "").strip().casefold()
        for policy in self.policies:
            if category in policy.categories or any(token in category for token in policy.categories):
                return policy
        raise PolicyConfigurationError(f"No warranty policy is configured for category '{product.category}'.")
