def register_routes(app):
    from app.routes.users import users_bp
    from app.routes.urls import urls_bp
    from app.routes.events import events_bp
    from app.routes.auth import auth_bp
    from app.routes.metrics import metrics_bp
    from app.routes.logs import logs_bp
    from app.routes.fail import fail_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.prometheus import prometheus_bp

    from app.routes.exports import exports_bp
    from app.routes.analytics import analytics_bp
    from app.routes.delivery import delivery_bp

    for blueprint in (users_bp, urls_bp, events_bp, auth_bp, exports_bp, analytics_bp, delivery_bp):
        app.register_blueprint(blueprint)
        app.register_blueprint(blueprint, url_prefix="/api/v1", name=f"{blueprint.name}_v1")

    from app.routes.openapi import docs_bp

    app.register_blueprint(docs_bp)
    app.register_blueprint(metrics_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(logs_bp, url_prefix="/api/v1", name="logs_v1")
    app.register_blueprint(fail_bp)
    app.register_blueprint(fail_bp, url_prefix="/api/v1", name="fail_v1")
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(prometheus_bp)
