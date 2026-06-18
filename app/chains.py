from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from .config_loader import AppConfig


@dataclass
class RpcNode:
    url: str
    priority: int = 1


@dataclass
class NativeCurrency:
    name: str
    symbol: str
    decimals: int


@dataclass
class ChainConfig:
    key: str
    chain_id: int
    name: str
    native_currency: NativeCurrency
    rpc_nodes: List[RpcNode]
    qps_limit: int
    block_time: float
    explorer_url: str
    event_topics: List[str] = field(default_factory=list)

    def sorted_rpc_nodes(self) -> List[RpcNode]:
        return sorted(self.rpc_nodes, key=lambda n: n.priority)


class ChainManager:
    _instance = None
    _chains: Dict[str, ChainConfig] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_chains()
        return cls._instance

    def _load_chains(self):
        cfg = AppConfig()
        for key, data in cfg.chains.items():
            native = NativeCurrency(**data["native_currency"])
            nodes = [RpcNode(**n) for n in data["rpc_nodes"]]
            chain = ChainConfig(
                key=key,
                chain_id=data["chain_id"],
                name=data["name"],
                native_currency=native,
                rpc_nodes=nodes,
                qps_limit=data.get("qps_limit", 30),
                block_time=data.get("block_time", 12),
                explorer_url=data.get("explorer_url", ""),
                event_topics=data.get("event_topics", []),
            )
            self._chains[key] = chain

    def get(self, key: str) -> Optional[ChainConfig]:
        return self._chains.get(key)

    def all(self) -> Dict[str, ChainConfig]:
        return dict(self._chains)

    def keys(self) -> List[str]:
        return list(self._chains.keys())

    def get_by_chain_id(self, chain_id: int) -> Optional[ChainConfig]:
        for c in self._chains.values():
            if c.chain_id == chain_id:
                return c
        return None
