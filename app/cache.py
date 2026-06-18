import os
import json
import time
import threading
from typing import Any, Dict, Optional
from .config_loader import AppConfig


class JsonFileCache:
    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key: str) -> str:
        safe = key.replace("/", "_").replace("\\", "_")
        return os.path.join(self.directory, f"{safe}.json")

    def get(self, key: str, ttl_seconds: int) -> Optional[Any]:
        path = self._path(key)
        try:
            with self._lock:
                if not os.path.exists(path):
                    return None
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if time.time() - data["timestamp"] > ttl_seconds:
                return None
            return data["value"]
        except Exception:
            return None

    def set(self, key: str, value: Any):
        path = self._path(key)
        payload = {"timestamp": time.time(), "value": value}
        with self._lock:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
            except Exception:
                pass

    def clear(self, key: str):
        path = self._path(key)
        with self._lock:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


class MemoryCache:
    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl_seconds: int) -> Optional[Any]:
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            if time.time() - item["timestamp"] > ttl_seconds:
                del self._store[key]
                return None
            return item["value"]

    def set(self, key: str, value: Any):
        with self._lock:
            self._store[key] = {"timestamp": time.time(), "value": value}

    def clear(self, key: str):
        with self._lock:
            self._store.pop(key, None)


class TieredCache:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cfg = AppConfig()
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            json_dir = os.path.join(base_dir, cfg.cache["json_dir"])
            cls._instance.memory = MemoryCache()
            cls._instance.disk = JsonFileCache(json_dir)
            cls._instance.ttl = cfg.cache["ttl_seconds"]
        return cls._instance

    def _ttl(self, category: str) -> int:
        return self.ttl.get(category, 60)

    def get(self, category: str, key: str) -> Optional[Any]:
        ttl = self._ttl(category)
        val = self.memory.get(key, ttl)
        if val is not None:
            return val
        val = self.disk.get(key, ttl)
        if val is not None:
            self.memory.set(key, val)
        return val

    def set(self, category: str, key: str, value: Any):
        self.memory.set(key, value)
        self.disk.set(key, value)

    def clear(self, category: str, key: str):
        self.memory.clear(key)
        self.disk.clear(key)
