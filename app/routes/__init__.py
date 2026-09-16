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

    app.register_blueprint(users_bp)
    app.register_blueprint(urls_bp)
    app.register_blueprint(events_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(metrics_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(fail_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(prometheus_bp)
