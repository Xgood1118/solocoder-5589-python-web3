import os
import logging
from flask import Flask, jsonify
from flask_cors import CORS
from app.config_loader import AppConfig
from app.routes import api_bp
from app.sync_service import SyncService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("blockchain_explorer")


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)
    cfg = AppConfig()

    app.register_blueprint(api_bp, url_prefix="/api/v1")

    @app.route("/")
    def index():
        return jsonify({
            "name": "Blockchain Explorer Backend",
            "version": "1.0.0",
            "docs": "/api/v1/chains",
            "health": "/api/v1/health",
        })

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not Found"}), 404

    @app.errorhandler(500)
    def server_error(e):
        logger.exception("Unhandled server error")
        return jsonify({"error": "Internal Server Error"}), 500

    sync = SyncService()
    sync.start()
    app.config["sync_service"] = sync

    @app.teardown_appcontext
    def shutdown_sync(exception=None):
        pass

    return app


if __name__ == "__main__":
    app = create_app()
    cfg = AppConfig()
    server_cfg = cfg.server
    logger.info(f"Starting server on {server_cfg['host']}:{server_cfg['port']}")
    app.run(
        host=server_cfg.get("host", "0.0.0.0"),
        port=server_cfg.get("port", 5002),
        debug=server_cfg.get("debug", False),
        use_reloader=False,
        threaded=True,
    )
