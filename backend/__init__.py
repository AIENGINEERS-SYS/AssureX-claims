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
    from .cli import init_cli
    from .api import auth, admin, claims, review, products
    from .web import bp as web_bp
    init_jwt_callbacks()
    init_middleware(app)
    init_cli(app)
    for blueprint in (auth.bp, admin.bp, claims.bp, review.bp, products.bp, web_bp):
        app.register_blueprint(blueprint)
    return app
