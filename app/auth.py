import time
import threading
from functools import wraps
from typing import Any, Callable, Dict
from flask import request, jsonify, g
from .config_loader import AppConfig


class RateLimiter:
    _instance = None
    _buckets: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def check(self, identifier: str, max_requests: int, window_seconds: int) -> bool:
        with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(identifier, {"count": 0, "reset_at": now + window_seconds})
            if now > bucket["reset_at"]:
                bucket = {"count": 0, "reset_at": now + window_seconds}
            if bucket["count"] >= max_requests:
                self._buckets[identifier] = bucket
                return False
            bucket["count"] += 1
            self._buckets[identifier] = bucket
            return True


def require_auth():
    def decorator(f: Callable):
        @wraps(f)
        def wrapper(*args, **kwargs):
            cfg = AppConfig()
            api_keys = cfg.auth.get("api_keys", [])
            if api_keys:
                provided = request.headers.get("X-API-Key") or request.args.get("api_key")
                if not provided or provided not in api_keys:
                    return jsonify({"error": "Unauthorized: invalid or missing API key"}), 401
            rate_cfg = cfg.auth.get("rate_limit", {}).get("default", {})
            max_req = rate_cfg.get("requests", 100)
            window = rate_cfg.get("window_seconds", 60)
            identifier = provided or request.remote_addr or "unknown"
            rl = RateLimiter()
            if not rl.check(f"{identifier}:{request.endpoint}", max_req, window):
                return jsonify({"error": "Rate limit exceeded"}), 429
            return f(*args, **kwargs)
        return wrapper
    return decorator


def optional_auth():
    def decorator(f: Callable):
        @wraps(f)
        def wrapper(*args, **kwargs):
            cfg = AppConfig()
            api_keys = cfg.auth.get("api_keys", [])
            provided = request.headers.get("X-API-Key") or request.args.get("api_key")
            g.authenticated = bool(provided and provided in api_keys)
            rate_cfg = cfg.auth.get("rate_limit", {}).get("default", {})
            max_req = rate_cfg.get("requests", 100)
            window = rate_cfg.get("window_seconds", 60)
            identifier = provided or request.remote_addr or "unknown"
            rl = RateLimiter()
            if not rl.check(f"{identifier}:{request.endpoint}", max_req, window):
                return jsonify({"error": "Rate limit exceeded"}), 429
            return f(*args, **kwargs)
        return wrapper
    return decorator
