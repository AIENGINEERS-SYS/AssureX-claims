"""Deterministic private claim cards used by the GTM image classifier."""
import hashlib
import json
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def card_payload(claim, policy):
    submitted = claim.submission_date or (claim.submitted_at.date() if claim.submitted_at else None)
    return {
        "claim_id": claim.claim_id,
        "product": claim.product.name,
        "category": claim.product.category,
        "brand": claim.product.brand,
        "model": claim.product.model_number,
        "purchase_date": claim.product.purchase_date.isoformat(),
        "claim_date": submitted.isoformat() if submitted else "unknown",
        "fault_type": claim.fault_type or "unknown",
        "damage_type": claim.damage_type or "unknown",
        "description": claim.fault_description or "",
        "documents": sorted({d.document_type for d in claim.documents}),
        "policy": policy.code,
    }


def render_claim_card(payload, output_path, *, variant=0):
    """Render a stable 224px RGB card without claimant PII."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    colors = ((245, 247, 250), (239, 246, 255), (247, 250, 242))
    image = Image.new("RGB", (224, 224), colors[variant % len(colors)])
    draw, font = ImageDraw.Draw(image), ImageFont.load_default()
    draw.rectangle((0, 0, 224, 28), fill=(20, 31, 43))
    draw.text((10, 9), "ASSUREX CLAIM EVIDENCE", fill=(255, 255, 255), font=font)
    rows = [
        ("PRODUCT", f"{payload.get('brand', '')} {payload.get('model', '')}"),
        ("CATEGORY", payload.get("category", "unknown")),
        ("FAULT", payload.get("fault_type", "unknown")),
        ("DAMAGE", payload.get("damage_type", "unknown")),
        ("DATES", f"{payload.get('purchase_date', '?')} / {payload.get('claim_date', '?')}"),
        ("POLICY", payload.get("policy", "unknown")),
        ("DOCS", ", ".join(payload.get("documents", [])) or "none"),
    ]
    y = 36
    for label, value in rows:
        draw.text((9, y), label, fill=(71, 85, 105), font=font)
        wrapped = textwrap.wrap(str(value), width=29)[:2] or ["unknown"]
        for line in wrapped:
            draw.text((66, y), line, fill=(15, 23, 42), font=font)
            y += 11
        y += 3
    description = textwrap.wrap(str(payload.get("description", "")), width=35)[:3]
    draw.line((9, y, 215, y), fill=(203, 213, 225), width=1)
    y += 6
    for line in description:
        draw.text((9, y), line, fill=(30, 41, 59), font=font)
        y += 11
    image.save(output, format="PNG", optimize=True)
    return output


def claim_card_path(claim, policy, root):
    payload = card_payload(claim, policy)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    return render_claim_card(payload, Path(root) / f"claim-{claim.id}-{digest}.png")
