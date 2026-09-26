"""Calendar-based calculations. Expiry dates include that entire UTC calendar day."""
from calendar import monthrange
from datetime import date, datetime, timezone

DEFAULT_NEAR_EXPIRY_DAYS = 30
STATUSES = ("Active", "Near Expiry", "Expired", "Extended Warranty", "Not Started", "No Warranty")


def current_date():
    return datetime.now(timezone.utc).date()


def calculate_warranty_expiry(start_date, duration, unit="months"):
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 1 or unit not in {"months", "years"}:
        raise ValueError("Duration must be a positive whole number of months or years.")
    months = duration * (12 if unit == "years" else 1)
    year, month = divmod(start_date.year * 12 + start_date.month - 1 + months, 12)
    if not 1 <= year <= 9999:
        raise ValueError("Warranty expiry is outside the supported date range.")
    return date(year, month + 1, min(start_date.day, monthrange(year, month + 1)[1]))


def _elapsed(start, end):
    if end <= start:
        return "0 days"
    months = (end.year - start.year) * 12 + end.month - start.month
    if months and calculate_warranty_expiry(start, months) > end:
        months -= 1
    if not months:
        days = (end - start).days
        return f"{days} day" + ("s" if days != 1 else "")
    years, months = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} year" + ("s" if years != 1 else ""))
    if months:
        parts.append(f"{months} month" + ("s" if months != 1 else ""))
    return ", ".join(parts)


def calculate_product_age(purchase_date, today=None):
    return _elapsed(purchase_date, today or current_date())


def calculate_warranty_remaining(expiry_date, today=None, *, start_date=None):
    today = today or current_date()
    if expiry_date is None:
        return "No warranty"
    if start_date and start_date > today:
        return "Not started"
    if expiry_date < today:
        return "Expired"
    return _elapsed(today, expiry_date) + " remaining"


def select_current_warranty(warranties, today=None):
    """Legacy overlaps: active extensions win, then latest expiry and stable ID."""
    today = today or current_date()
    records = list(warranties)
    active = [w for w in records if w.start_date <= today <= w.expiry_date]
    if active:
        return max(active, key=lambda w: (w.extended_warranty, w.expiry_date, w.id or 0))
    expired = [w for w in records if w.expiry_date < today]
    if expired:
        return max(expired, key=lambda w: (w.expiry_date, w.id or 0))
    return min(records, key=lambda w: (w.start_date, w.id or 0), default=None)


def calculate_warranty_status(warranties, today=None, near_expiry_days=DEFAULT_NEAR_EXPIRY_DAYS):
    today = today or current_date()
    warranty = select_current_warranty(warranties, today)
    if warranty is None:
        return "No Warranty"
    if warranty.start_date > today:
        return "Not Started"
    if warranty.expiry_date < today:
        return "Expired"
    if warranty.extended_warranty:
        return "Extended Warranty"
    if (warranty.expiry_date - today).days <= near_expiry_days:
        return "Near Expiry"
    return "Active"
