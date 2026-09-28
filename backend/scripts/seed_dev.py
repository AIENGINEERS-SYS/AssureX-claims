"""Opt-in development fixtures; passwords are supplied through the environment."""
import os
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import select
from backend.db.models import Claim, ModelVersion, Product, User, Warranty
from backend.db.session import session_factory


def hash_password(password: str) -> str:
    user = User()
    user.set_password(password)
    return user.password_hash


def main() -> None:
    if os.getenv("ASSUREX_ENV") != "development":
        raise SystemExit("Seeding requires ASSUREX_ENV=development")
    admin_password = os.environ.get("ASSUREX_DEV_ADMIN_PASSWORD")
    customer_password = os.environ.get("ASSUREX_DEV_CUSTOMER_PASSWORD")
    if not admin_password or not customer_password or min(map(len, (admin_password, customer_password))) < 12:
        raise SystemExit("Set both ASSUREX_DEV_*_PASSWORD variables to at least 12 characters")
    with session_factory()() as session, session.begin():
        if session.scalar(select(User.id).limit(1)) is not None:
            raise SystemExit("Seed only an empty development database")
        admin = User(email="admin@example.invalid", password_hash=hash_password(admin_password),
                     first_name="Demo", last_name="Admin", role="admin")
        customer = User(email="customer@example.invalid", password_hash=hash_password(customer_password),
                        first_name="Demo", last_name="Customer")
        session.add_all((admin, customer))
        session.flush()
        product = Product(user_id=customer.id, name="Demo Laptop", category="electronics", brand="Demo",
                          model_number="D-1", serial_number="DEMO-0001", purchase_date=date.today() - timedelta(days=30),
                          purchase_price=Decimal("450.00"), retailer="Demo Shop")
        session.add(product)
        session.flush()
        warranty = Warranty(product_id=product.id, provider="Demo Provider", start_date=product.purchase_date,
                            expiry_date=product.purchase_date + timedelta(days=365), coverage_duration_months=12)
        session.add(warranty)
        session.flush()
        session.add(Claim(user_id=customer.id, product_id=product.id, warranty_id=warranty.id,
                          fault_date=date.today(), fault_type="power", fault_description="Demo startup failure"))
        session.add_all((
            ModelVersion(model_type="python", model_name="demo-pending", version="0.0.0",
                         artifact_path="models/not-trained", training_dataset_version="demo", is_active=False),
            ModelVersion(model_type="gtm", model_name="demo-pending", version="0.0.0",
                         artifact_path="gtm_model/not-trained", training_dataset_version="demo", is_active=False),
        ))
    print("Created development fixtures. Model entries are placeholders, not trained releases.")


if __name__ == "__main__":
    main()
