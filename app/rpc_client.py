import time
import threading
import json
import logging
from typing import Any, Dict, List, Optional, Callable
from urllib3 import PoolManager, Retry, Timeout
from urllib3.exceptions import MaxRetryError, HTTPError
from .chains import ChainConfig, RpcNode


logger = logging.getLogger(__name__)


class TokenBucket:
    def __init__(self, rate: int, capacity: Optional[int] = None):
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last_refill = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                remaining = tokens - self._tokens
                wait = remaining / self.rate
            if time.monotonic() + wait > deadline:
                return False
            time.sleep(min(wait, 0.05))


class ExponentialBackoff:
    def __init__(self, base_delay: float = 1.0, max_delay: float = 8.0, max_attempts: int = 4):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_attempts = max_attempts

    def delay(self, attempt: int) -> float:
        d = self.base_delay * (2 ** attempt)
        return min(d, self.max_delay)


class RpcClient:
    _pool_manager: Optional[PoolManager] = None

    @classmethod
    def _get_pool_manager(cls) -> PoolManager:
        if cls._pool_manager is None:
            retry = Retry(total=0, connect=0, read=0)
            timeout = Timeout(connect=10.0, read=30.0)
            cls._pool_manager = PoolManager(
                num_pools=50,
                maxsize=50,
                block=True,
                retries=retry,
                timeout=timeout,
                headers={"Content-Type": "application/json"},
            )
        return cls._pool_manager

    def __init__(self, chain: ChainConfig):
        self.chain = chain
        self.rate_limiter = TokenBucket(rate=chain.qps_limit, capacity=chain.qps_limit * 2)
        self.backoff = ExponentialBackoff(base_delay=1.0, max_delay=8.0, max_attempts=4)
        self._node_index = 0
        self._failed_nodes: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._request_id = 0

    def _get_active_nodes(self) -> List[RpcNode]:
        nodes = self.chain.sorted_rpc_nodes()
        now = time.monotonic()
        result = []
        for n in nodes:
            failed_at = self._failed_nodes.get(n.url, 0)
            if now - failed_at > 60:
                result.append(n)
        return result if result else nodes

    def _mark_node_failed(self, url: str):
        self._failed_nodes[url] = time.monotonic()

    def _next_node_url(self) -> str:
        with self._lock:
            nodes = self._get_active_nodes()
            if not nodes:
                nodes = self.chain.sorted_rpc_nodes()
            self._node_index = (self._node_index + 1) % len(nodes)
            return nodes[self._node_index].url

    def _build_payload(self, method: str, params: List[Any]) -> Dict[str, Any]:
        self._request_id += 1
        return {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

    def _do_http_post(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        encoded = json.dumps(payload).encode("utf-8")
        pm = self._get_pool_manager()
        resp = pm.request("POST", url, body=encoded)
        if resp.status != 200:
            raise HTTPError(f"HTTP {resp.status}: {resp.data[:200]}")
        data = json.loads(resp.data.decode("utf-8"))
        if "error" in data:
            raise Exception(f"RPC error: {data['error']}")
        return data

    def call(self, method: str, params: Optional[List[Any]] = None, node_url: Optional[str] = None) -> Any:
        if params is None:
            params = []
        payload = self._build_payload(method, params)
        nodes_to_try = [node_url] if node_url else [n.url for n in self._get_active_nodes()]

        last_exc = None
        for attempt in range(self.backoff.max_attempts):
            if not self.rate_limiter.acquire(timeout=5.0):
                raise Exception("Rate limit timeout")
            current_url = nodes_to_try[attempt % len(nodes_to_try)] if not node_url else node_url
            try:
                result = self._do_http_post(current_url, payload)
                return result.get("result")
            except Exception as e:
                last_exc = e
                logger.warning(f"RPC call failed on {current_url} (attempt {attempt + 1}): {e}")
                self._mark_node_failed(current_url)
                if attempt < self.backoff.max_attempts - 1:
                    time.sleep(self.backoff.delay(attempt))

        raise Exception(f"All RPC nodes failed after {self.backoff.max_attempts} attempts. Last error: {last_exc}")

    def batch_call(self, requests: List[Dict[str, Any]]) -> List[Any]:
        if not requests:
            return []
        if not self.rate_limiter.acquire(tokens=min(len(requests), 5), timeout=5.0):
            raise Exception("Rate limit timeout")

        payload = []
        for idx, r in enumerate(requests):
            self._request_id += 1
            payload.append({
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": r["method"],
                "params": r.get("params", []),
            })

        nodes = self._get_active_nodes()
        last_exc = None
        for attempt in range(self.backoff.max_attempts):
            url = nodes[attempt % len(nodes)].url
            try:
                results = self._do_http_post(url, payload)
                result_map = {r["id"]: r.get("result") for r in results}
                ordered = [result_map[p["id"]] for p in payload]
                return ordered
            except Exception as e:
                last_exc = e
                self._mark_node_failed(url)
                if attempt < self.backoff.max_attempts - 1:
                    time.sleep(self.backoff.delay(attempt))

        raise Exception(f"Batch RPC call failed. Last error: {last_exc}")


class RpcClientManager:
    _instance = None
    _clients: Dict[str, RpcClient] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            from .chains import ChainManager
            cm = ChainManager()
            for key in cm.keys():
                chain = cm.get(key)
                cls._clients[key] = RpcClient(chain)
        return cls._instance

    def get(self, chain_key: str) -> Optional[RpcClient]:
        return self._clients.get(chain_key)

    def all(self) -> Dict[str, RpcClient]:
        return dict(self._clients)
