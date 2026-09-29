"""AssureX application factory. Importing the package performs no database I/O."""


def create_app(config=None):
    import secrets
    from pathlib import Path
    from flask import Flask
    from sqlalchemy import event
    from .config import settings, validate_config
    from .extensions import bcrypt, db, jwt, limiter, migrate

    app = Flask(__name__)
    app.config.from_mapping(settings())
    if config:
        app.config.update(config)
    validate_config(app)
    db.init_app(app)
    bcrypt.init_app(app)
    jwt.init_app(app)
    limiter.init_app(app)
    migrate.init_app(app, db, directory=str(Path(__file__).parent / "db" / "migrations"))
    with app.app_context():
        if db.engine.dialect.name == "sqlite":
            @event.listens_for(db.engine, "connect")
            def foreign_keys(connection, record):
                connection.execute("PRAGMA foreign_keys=ON")
        app.extensions["dummy_password_hash"] = bcrypt.generate_password_hash(secrets.token_urlsafe(32))

    from .security import init_jwt_callbacks
    from .middleware import init_middleware
    from .frontend import init_frontend_routes
    from .cli import init_cli
    from .api import auth, admin, claims, review, products, claim_workflow, documents, dashboard, predictions, notifications
    init_jwt_callbacks()
    init_middleware(app)
    init_frontend_routes(app)
    init_cli(app)
    @app.cli.command("dashboard-reminders")
    def dashboard_reminders():
        """Create idempotent warranty expiry notifications."""
        from .services.notifications import create_warranty_reminders
        print(f"Created {create_warranty_reminders()} warranty reminders.")

    @app.cli.command("notification-reminders")
    def notification_reminders():
        """Phase 24 daily notification scheduler entry point."""
        from .services.notifications import create_warranty_reminders
        print(f"Created {create_warranty_reminders()} warranty reminders.")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    for blueprint in (auth.bp, admin.bp, claims.bp, review.bp, products.bp, claim_workflow.bp, documents.bp,
                      dashboard.bp, predictions.bp, notifications.bp):
        app.register_blueprint(blueprint)

    if app.config["MODEL_PRELOAD_ENABLED"]:
        from .services.predictions import preload_models
        try:
            with app.app_context():
                metrics = preload_models(app)
            app.logger.info(
                "model_preload_complete python_ms=%s gtm_ms=%s total_ms=%s",
                metrics["python_load_ms"], metrics["gtm_load_ms"], metrics["total_load_ms"],
            )
        except Exception as exc:
            app.logger.exception("model_preload_failed error=%s", type(exc).__name__)
            if app.config["MODEL_PRELOAD_STRICT"]:
                raise RuntimeError("Configured ML models could not be preloaded.") from exc
    return app
