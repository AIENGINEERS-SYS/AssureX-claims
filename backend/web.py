"""Same-origin product UI; data remains behind JWT-protected API routes."""
from pathlib import Path
from flask import Blueprint, redirect, render_template
from backend.services.warranty_calculations import current_date

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
bp = Blueprint("web", __name__, template_folder=str(FRONTEND / "templates"),
               static_folder=str(FRONTEND / "static"), static_url_path="/assets")


@bp.get("/")
def home():
    return redirect("/products")


@bp.get("/products")
@bp.get("/products/new")
@bp.get("/products/<int:product_id>")
@bp.get("/products/<int:product_id>/edit")
def products(product_id=None):
    return render_template("products.html", server_date=current_date().isoformat())
