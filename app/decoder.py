import os
import json
import threading
from typing import Dict, Optional, Tuple
from eth_abi import decode
from eth_utils import to_bytes, function_signature_to_4byte_selector
from .cache import TieredCache


BUILTIN_SIGNATURES: Dict[str, str] = {
    "0xa9059cbb": "transfer(address,uint256)",
    "0x095ea7b3": "approve(address,uint256)",
    "0x23b872dd": "transferFrom(address,address,uint256)",
    "0x70a08231": "balanceOf(address)",
    "0x18160ddd": "totalSupply()",
    "0xdd62ed3e": "allowance(address,address)",
    "0x313ce567": "decimals()",
    "0x06fdde03": "name()",
    "0x95d89b41": "symbol()",
    "0xd0e30db0": "deposit()",
    "0x2e1a7d4d": "withdraw(uint256)",
    "0x6ea056a9": "swapExactETHForTokens(uint256,address[],address,uint256)",
    "0x7ff36ab5": "swapExactETHForTokensSupportingFeeOnTransferTokens(uint256,address[],address,uint256)",
    "0xfb3bdb41": "swapETHForExactTokens(uint256,address[],address,uint256)",
    "0x38ed1739": "swapExactTokensForTokens(uint256,uint256,address[],address,uint256)",
    "0x5c11d795": "swapExactTokensForTokensSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)",
    "0x8803dbee": "swapTokensForExactTokens(uint256,uint256,address[],address,uint256)",
    "0x7a33d03e": "swapExactTokensForETH(uint256,uint256,address[],address,uint256)",
    "0x4a25d94a": "swapTokensForExactETH(uint256,uint256,address[],address,uint256)",
    "0x1f0fd342": "swapExactTokensForETHSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)",
}


TRANSFER_EVENT_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
APPROVAL_EVENT_SIG = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"

BUILTIN_EVENTS: Dict[str, Dict] = {
    TRANSFER_EVENT_SIG: {
        "name": "Transfer",
        "inputs": [
            {"name": "from", "type": "address", "indexed": True},
            {"name": "to", "type": "address", "indexed": True},
            {"name": "value", "type": "uint256", "indexed": False},
        ],
    },
    APPROVAL_EVENT_SIG: {
        "name": "Approval",
        "inputs": [
            {"name": "owner", "type": "address", "indexed": True},
            {"name": "spender", "type": "address", "indexed": True},
            {"name": "value", "type": "uint256", "indexed": False},
        ],
    },
}


def _pad_hex_to_bytes32(h: str) -> bytes:
    b = to_bytes(hexstr=h) if h.startswith("0x") else bytes.fromhex(h)
    if len(b) < 32:
        b = b"\x00" * (32 - len(b)) + b
    return b


class FourByteDatabase:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._signatures = dict(BUILTIN_SIGNATURES)
            cls._instance._events = dict(BUILTIN_EVENTS)
            cls._instance._lock = threading.Lock()
            cls._instance._cache = TieredCache()
            cls._instance._load_from_cache()
        return cls._instance

    def _load_from_cache(self):
        cached = self._cache.get("fourbyte", "signatures_db")
        if isinstance(cached, dict):
            with self._lock:
                for k, v in cached.items():
                    if k not in self._signatures:
                        self._signatures[k] = v

    def _save_to_cache(self):
        self._cache.set("fourbyte", "signatures_db", dict(self._signatures))

    def lookup(self, selector: str) -> Optional[str]:
        sel = selector.lower()
        if len(sel) == 10 and sel.startswith("0x"):
            with self._lock:
                return self._signatures.get(sel)
        return None

    def add_signature(self, selector: str, signature: str):
        sel = selector.lower()
        with self._lock:
            self._signatures[sel] = signature
        self._save_to_cache()

    def lookup_event(self, topic0: str) -> Optional[Dict]:
        return self._events.get(topic0.lower())


def parse_function_signature(sig: str) -> Tuple[str, list]:
    import re
    m = re.match(r"(\w+)\((.*)\)", sig.strip())
    if not m:
        return sig, []
    name = m.group(1)
    params_str = m.group(2)
    if not params_str.strip():
        return name, []
    params = []
    depth = 0
    current = ""
    for ch in params_str:
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            params.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        params.append(current.strip())
    return name, params


def decode_input_data(input_data: str) -> Dict:
    result = {"selector": None, "signature": None, "function": None, "params": [], "raw": input_data}
    if not input_data or input_data == "0x" or len(input_data) < 10:
        return result
    selector = input_data[:10].lower()
    result["selector"] = selector
    db = FourByteDatabase()
    sig = db.lookup(selector)
    if not sig:
        return result
    result["signature"] = sig
    name, param_types = parse_function_signature(sig)
    result["function"] = name
    try:
        if param_types:
            raw_params = to_bytes(hexstr=input_data[10:]) if len(input_data) > 10 else b""
            decoded = decode(param_types, raw_params)
            for t, v in zip(param_types, decoded):
                if isinstance(v, bytes):
                    v = v.hex()
                elif isinstance(v, int) and (t == "address" or "address" in t):
                    try:
                        v = "0x" + to_bytes(v).hex().zfill(40)
                    except Exception:
                        pass
                result["params"].append({"type": t, "value": v})
    except Exception as e:
        result["decode_error"] = str(e)
    return result


def decode_revert_reason(output: str) -> Optional[str]:
    if not output or output == "0x":
        return None
    try:
        data = to_bytes(hexstr=output) if output.startswith("0x") else bytes.fromhex(output)
        if len(data) >= 4 and data[:4] == b"\x08\xc3y\xa0":
            decoded = decode(["string"], data[4:])
            return decoded[0] if decoded else None
        if data[:4] == b"N\x48\x05\x58":
            decoded = decode(["string", "uint256"], data[4:])
            if decoded:
                return f"Panic({decoded[0]})" if len(decoded) >= 1 else "Panic"
        return None
    except Exception:
        return None


def decode_event_log(log: Dict) -> Dict:
    result = dict(log)
    topics = log.get("topics", [])
    if not topics:
        return result
    topic0 = topics[0].lower()
    db = FourByteDatabase()
    event_abi = db.lookup_event(topic0)
    if not event_abi:
        result["event"] = "Unknown"
        return result
    result["event"] = event_abi["name"]
    try:
        indexed_types = []
        indexed_values = []
        non_indexed_types = []
        indexed_names = []
        non_indexed_names = []
        for inp in event_abi["inputs"]:
            if inp.get("indexed"):
                indexed_types.append(inp["type"])
                indexed_names.append(inp["name"])
            else:
                non_indexed_types.append(inp["type"])
                non_indexed_names.append(inp["name"])
        decoded_params = {}
        for i, (t, name) in enumerate(zip(indexed_types, indexed_names)):
            if i + 1 < len(topics):
                try:
                    raw = _pad_hex_to_bytes32(topics[i + 1])
                    val = decode([t], raw)[0]
                    if t == "address" and isinstance(val, int):
                        val = "0x" + to_bytes(val).hex().zfill(40)
                    elif isinstance(val, bytes):
                        val = val.hex()
                    decoded_params[name] = val
                except Exception:
                    decoded_params[name] = topics[i + 1]
        if non_indexed_types and log.get("data") and log["data"] != "0x":
            try:
                raw_data = to_bytes(hexstr=log["data"])
                vals = decode(non_indexed_types, raw_data)
                for name, t, v in zip(non_indexed_names, non_indexed_types, vals):
                    if isinstance(v, bytes):
                        v = v.hex()
                    elif t == "address" and isinstance(v, int):
                        v = "0x" + to_bytes(v).hex().zfill(40)
                    decoded_params[name] = v
            except Exception:
                pass
        result["decoded"] = decoded_params
    except Exception as e:
        result["decode_error"] = str(e)
    return result
