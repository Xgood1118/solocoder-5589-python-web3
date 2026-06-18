import os
import yaml
from typing import Any, Dict


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class AppConfig:
    _instance = None
    _config: Dict[str, Any] = None
    _chains: Dict[str, Any] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cls._config = load_yaml(os.path.join(base_dir, "config", "config.yaml"))
            cls._chains = load_yaml(os.path.join(base_dir, "config", "chains.yaml"))
        return cls._instance

    @property
    def server(self) -> Dict[str, Any]:
        return self._config["server"]

    @property
    def database(self) -> Dict[str, Any]:
        return self._config["database"]

    @property
    def cache(self) -> Dict[str, Any]:
        return self._config["cache"]

    @property
    def auth(self) -> Dict[str, Any]:
        return self._config["auth"]

    @property
    def scheduler(self) -> Dict[str, Any]:
        return self._config["scheduler"]

    @property
    def chains(self) -> Dict[str, Any]:
        return self._chains["chains"]

    def get_chain(self, chain_key: str) -> Dict[str, Any]:
        return self._chains["chains"].get(chain_key)

    def list_chain_keys(self):
        return list(self._chains["chains"].keys())
