"""Inspect Flask's actual registrations and frontend API calls without a live DB."""
import argparse
import inspect
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import create_app


def inventory():
    app = create_app({"ASSUREX_ENV": "development", "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "JWT_SECRET_KEY": "route-inventory-only-" * 4, "BCRYPT_LOG_ROUNDS": 4,
        "RATELIMIT_ENABLED": False, "MODEL_PRELOAD_ENABLED": False, "MODEL_PRELOAD_STRICT": False})
    lines = ["# Registered routes and frontend communication", "",
        "Generated from Flask's URL map and handler authorization metadata. Object scope is enforced by the shared claim scope, document scope and reporting services. Employee and reviewer access requires assignment; administrator roles are explicitly listed.", "",
        "| Method | Path | Blueprint | Authentication | Roles | Purpose / handler |", "| --- | --- | --- | --- | --- | --- |"]
    seen = set()
    for rule in sorted(app.url_map.iter_rules(), key=lambda item: (item.rule, item.endpoint)):
        view = app.view_functions[rule.endpoint]
        roles = getattr(view, "required_roles", ())
        try:
            source = inspect.getsource(view)
        except (OSError, TypeError):
            source = ""
        decorators = source.split("def ", 1)[0]
        auth = "JWT" if roles or "jwt_required" in decorators or "verify_jwt_in_request" in source else "Public"
        if "fresh=True" in decorators:
            auth += " (fresh)"
        if "refresh=True" in decorators:
            auth += " (refresh)"
        owner = rule.endpoint.split(".")[0] if "." in rule.endpoint else "application"
        purpose = (inspect.getdoc(view) or rule.endpoint.replace("_", " ")).splitlines()[0].replace("|", "/")
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            key = (method, rule.rule)
            if key in seen:
                raise RuntimeError(f"Duplicate handler: {key}")
            seen.add(key)
            lines.append(f"| {method} | `{rule.rule}` | {owner} | {auth} | {', '.join(roles) or ('all authenticated accounts' if auth != 'Public' else 'public')} | {purpose} |")
    lines += ["", "Frontend navigation and primary backend contracts:", "",
        "| Frontend route group | Allowed roles | Backend APIs |", "| --- | --- | --- |",
        "| `/claims`, `/claims/draft/:id`, `/claims/:claimId` | customer | `/api/claims/my`, drafts/uploads/submission, own claim details and assessment reports |",
        "| `/products/*` | customer, admin | `/api/products/*`, warranty and product document APIs |",
        "| `/dashboard/customer` | customer, admin (explicit customer ID) | `/api/dashboard/customer`, trends and notifications |",
        "| `/dashboard/employee` | employee, admin | `/api/claims/assigned`, `/api/employee/claims/:id`, processing status and assessment retry |",
        "| `/dashboard/reviewer` | reviewer, admin | `/api/dashboard/reviewer/*`, `/api/review/*`, permitted evaluation and evidence APIs |",
        "| `/dashboard/admin`, `/dashboard/operations` | admin | `/api/dashboard/admin`, `/api/admin/*`, assignment and assessment retry |",
        "| `/dashboard/notifications` | authenticated | `/api/notifications/*` |",
        "| `/reports/*` | authenticated, with per-report service authorization | `/api/reports/*` |",
        "| `/search/*` | authenticated, with scoped results | `/api/search`, claim/product/warranty/review searches |", "",
        "Discovered frontend API call sites (dynamic path expressions are retained):", "",
        "| Source | API path / expression |", "| --- | --- |"]
    for path in sorted((ROOT / "frontend/src").rglob("*")):
        if path.suffix not in {".jsx", ".js"}:
            continue
        source = path.read_text(encoding="utf-8")
        calls = set(re.findall(r"(?:api\.(?:get|post|patch|put|delete)|query)\([^\n;]{0,180}", source))
        for call in sorted(calls):
            lines.append(f"| `{path.relative_to(ROOT).as_posix()}` | `{call.replace('`', chr(39)).replace('|', '/')}` |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = inventory()
    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(output)
