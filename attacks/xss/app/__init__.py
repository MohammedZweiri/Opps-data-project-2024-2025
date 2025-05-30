import logging

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# Setup extensions
db = SQLAlchemy()


def create_app(config=None):
    app = Flask(__name__)

    # Load configuration
    if config is None:
        app.config.from_object('app.config.Config')
    else:
        app.config.from_object(config)

    # Set up logging
    if not app.debug:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        app.logger.addHandler(handler)

    # Initialize extensions with app
    db.init_app(app)

    # Create database tables
    from .models import CollectedData, AppState
    with app.app_context():
        db.create_all()

    # Register blueprints
    from .routes import main_bp
    app.register_blueprint(main_bp)

    # Register error handlers
    register_error_handlers(app)

    return app


def register_error_handlers(app):
    """Register error handlers with the Flask application."""

    @app.errorhandler(404)
    def page_not_found(e):
        from flask import jsonify
        return jsonify({"error": "Resource not found"}), 404

    @app.errorhandler(500)
    def server_error(e):
        from flask import jsonify
        app.logger.error(f"Server error: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500

    @app.errorhandler(400)
    def bad_request(e):
        from flask import jsonify
        return jsonify({"error": "Bad request"}), 400
