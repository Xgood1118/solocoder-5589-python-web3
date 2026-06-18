import os
import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from web3 import Web3
from web3.providers.rpc import HTTPProvider
from web3.exceptions import ContractLogicError
from eth_utils import to_checksum_address, to_hex
from .chains import ChainManager, ChainConfig
from .rpc_client import RpcClientManager
from .models import (
    DatabaseManager, Block, Transaction, EventLog, Token, TokenBalance,
    ChainSyncState, InternalTransaction,
)
from .cache import TieredCache
from .decoder import decode_input_data, decode_event_log, decode_revert_reason


logger = logging.getLogger(__name__)


class Web3ProviderFactory:
    _instances: Dict[str, Web3] = {}

    @classmethod
    def get(cls, chain_key: str) -> Optional[Web3]:
        if chain_key not in cls._instances:
            cm = ChainManager()
            chain = cm.get(chain_key)
            if not chain:
                return None
            nodes = chain.sorted_rpc_nodes()
            if not nodes:
                return None
            w3 = Web3(HTTPProvider(nodes[0].url))
            cls._instances[chain_key] = w3
        return cls._instances[chain_key]


ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "name", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "string"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "string"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint8"}]},
    {"name": "totalSupply", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"type": "uint256"}]},
]


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.startswith("0x"):
            return int(value, 16)
        return int(value)
    try:
        return int(value)
    except Exception:
        return 0


def _to_hex_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.startswith("0x") else "0x" + value
    try:
        return to_hex(value)
    except Exception:
        return str(value)


def _ts_to_dt(ts: Any) -> datetime:
    try:
        t = _to_int(ts)
        return datetime.utcfromtimestamp(t)
    except Exception:
        return datetime.utcnow()


class BlockchainService:
    def __init__(self):
        self.rpc_mgr = RpcClientManager()
        self.chain_mgr = ChainManager()
        self.db = DatabaseManager()
        self.cache = TieredCache()

    # ---- Block operations ----

    def get_block_by_number(self, chain_key: str, block_number: int, with_transactions: bool = False) -> Optional[Dict]:
        key = f"{chain_key}_block_{block_number}_{with_transactions}"
        cached = self.cache.get("block", key)
        if cached:
            return cached

        rpc = self.rpc_mgr.get(chain_key)
        if not rpc:
            return None
        block_hex = hex(block_number) if not isinstance(block_number, str) else block_number
        data = rpc.call("eth_getBlockByNumber", [block_hex, with_transactions])
        if not data:
            return None
        result = self._format_block(data, with_transactions)
        self.cache.set("block", key, result)
        return result

    def get_block_by_hash(self, chain_key: str, block_hash: str, with_transactions: bool = False) -> Optional[Dict]:
        key = f"{chain_key}_block_hash_{block_hash}_{with_transactions}"
        cached = self.cache.get("block", key)
        if cached:
            return cached
        rpc = self.rpc_mgr.get(chain_key)
        if not rpc:
            return None
        data = rpc.call("eth_getBlockByHash", [block_hash, with_transactions])
        if not data:
            return None
        result = self._format_block(data, with_transactions)
        self.cache.set("block", key, result)
        return result

    def get_latest_block_number(self, chain_key: str) -> Optional[int]:
        rpc = self.rpc_mgr.get(chain_key)
        if not rpc:
            return None
        result = rpc.call("eth_blockNumber", [])
        return _to_int(result) if result else None

    def get_block_range(self, chain_key: str, start: int, end: int) -> List[Dict]:
        results = []
        for n in range(start, end + 1):
            b = self.get_block_by_number(chain_key, n)
            if b:
                results.append(b)
        return results

    def _format_block(self, data: Dict, with_tx: bool) -> Dict:
        txs = data.get("transactions", [])
        if not isinstance(txs, list):
            txs = []
        tx_count = len(txs)
        if with_tx and tx_count > 0 and isinstance(txs[0], dict):
            block_txs = txs
        else:
            block_txs = [tx for tx in txs if isinstance(tx, str)]
        return {
            "number": _to_int(data.get("number")),
            "hash": data.get("hash"),
            "parent_hash": data.get("parentHash"),
            "nonce": data.get("nonce"),
            "sha3_uncles": data.get("sha3Uncles"),
            "logs_bloom": data.get("logsBloom"),
            "transactions_root": data.get("transactionsRoot"),
            "state_root": data.get("stateRoot"),
            "receipts_root": data.get("receiptsRoot"),
            "miner": data.get("miner"),
            "difficulty": str(_to_int(data.get("difficulty"))),
            "total_difficulty": str(_to_int(data.get("totalDifficulty"))),
            "size": _to_int(data.get("size")),
            "extra_data": data.get("extraData"),
            "gas_limit": _to_int(data.get("gasLimit")),
            "gas_used": _to_int(data.get("gasUsed")),
            "timestamp": _ts_to_dt(data.get("timestamp")).isoformat(),
            "transaction_count": tx_count,
            "base_fee_per_gas": str(_to_int(data.get("baseFeePerGas"))) if data.get("baseFeePerGas") else None,
            "transactions": block_txs,
        }

    # ---- Transaction operations ----

    def get_transaction(self, chain_key: str, tx_hash: str) -> Optional[Dict]:
        key = f"{chain_key}_tx_{tx_hash}"
        cached = self.cache.get("transaction", key)
        if cached:
            return cached
        rpc = self.rpc_mgr.get(chain_key)
        if not rpc:
            return None

        results = rpc.batch_call([
            {"method": "eth_getTransactionByHash", "params": [tx_hash]},
            {"method": "eth_getTransactionReceipt", "params": [tx_hash]},
        ])
        tx_data, receipt_data = results[0], results[1]
        if not tx_data:
            return None
        result = self._format_transaction(tx_data, receipt_data)
        self.cache.set("transaction", key, result)
        return result

    def get_transaction_internal(self, chain_key: str, tx_hash: str) -> List[Dict]:
        rpc = self.rpc_mgr.get(chain_key)
        if not rpc:
            return []
        try:
            traces = rpc.call("debug_traceTransaction", [tx_hash, {"tracer": "callTracer"}])
            if traces and isinstance(traces, dict):
                return self._flatten_call_traces(tx_hash, traces)
        except Exception as e:
            logger.warning(f"Internal trace failed for {tx_hash}: {e}")
        return []

    def _flatten_call_traces(self, tx_hash: str, trace: Dict, depth: int = 0, trace_addr: str = "0") -> List[Dict]:
        result = []
        entry = {
            "transaction_hash": tx_hash,
            "trace_type": trace.get("type"),
            "from": trace.get("from"),
            "to": trace.get("to"),
            "value": str(_to_int(trace.get("value"))) if trace.get("value") else "0",
            "gas": _to_int(trace.get("gas")),
            "gas_used": _to_int(trace.get("gasUsed")),
            "input": trace.get("input"),
            "output": trace.get("output"),
            "error": trace.get("error"),
            "trace_address": trace_addr,
            "depth": depth,
            "revert_reason": decode_revert_reason(trace.get("output", "")) if trace.get("error") else None,
        }
        result.append(entry)
        calls = trace.get("calls", [])
        for i, c in enumerate(calls):
            result.extend(self._flatten_call_traces(tx_hash, c, depth + 1, f"{trace_addr}_{i}"))
        return result

    def _format_transaction(self, tx: Dict, receipt: Optional[Dict]) -> Dict:
        input_data = tx.get("input", "0x")
        decoded = decode_input_data(input_data)
        revert_reason = None
        if receipt:
            status = _to_int(receipt.get("status"))
            if status == 0:
                revert_reason = decode_revert_reason(receipt.get("output", ""))
        else:
            status = None

        return {
            "hash": tx.get("hash"),
            "block_number": _to_int(tx.get("blockNumber")),
            "block_hash": tx.get("blockHash"),
            "from": tx.get("from"),
            "to": tx.get("to"),
            "value": str(_to_int(tx.get("value"))),
            "nonce": _to_int(tx.get("nonce")),
            "gas": _to_int(tx.get("gas")),
            "gas_price": str(_to_int(tx.get("gasPrice"))) if tx.get("gasPrice") else None,
            "max_fee_per_gas": str(_to_int(tx.get("maxFeePerGas"))) if tx.get("maxFeePerGas") else None,
            "max_priority_fee_per_gas": str(_to_int(tx.get("maxPriorityFeePerGas"))) if tx.get("maxPriorityFeePerGas") else None,
            "input": input_data,
            "transaction_index": _to_int(tx.get("transactionIndex")),
            "type": _to_int(tx.get("type")) if tx.get("type") else None,
            "v": tx.get("v"),
            "r": tx.get("r"),
            "s": tx.get("s"),
            "status": status,
            "gas_used": _to_int(receipt.get("gasUsed")) if receipt else None,
            "cumulative_gas_used": _to_int(receipt.get("cumulativeGasUsed")) if receipt else None,
            "contract_address": receipt.get("contractAddress") if receipt else None,
            "logs": self._format_logs(receipt.get("logs", [])) if receipt else [],
            "decoded_input": decoded,
            "revert_reason": revert_reason,
        }

    def _format_logs(self, logs: List[Dict]) -> List[Dict]:
        return [decode_event_log(l) for l in logs]

    # ---- Address operations ----

    def get_native_balance(self, chain_key: str, address: str) -> Dict:
        cache_key = f"{chain_key}_native_balance_{address.lower()}"
        cached = self.cache.get("balance", cache_key)
        if cached:
            return cached
        rpc = self.rpc_mgr.get(chain_key)
        if not rpc:
            return {"balance": "0"}
        bal = rpc.call("eth_getBalance", [address, "latest"])
        result = {"balance": str(_to_int(bal)) if bal else "0", "address": address}
        self.cache.set("balance", cache_key, result)
        return result

    def get_erc20_balance(self, chain_key: str, address: str, token_address: str) -> Dict:
        cache_key = f"{chain_key}_erc20_balance_{address.lower()}_{token_address.lower()}"
        cached = self.cache.get("balance", cache_key)
        if cached:
            return cached
        w3 = Web3ProviderFactory.get(chain_key)
        if not w3:
            return {"balance": "0"}
        try:
            contract = w3.eth.contract(address=to_checksum_address(token_address), abi=ERC20_ABI)
            bal = contract.functions.balanceOf(to_checksum_address(address)).call()
            result = {"balance": str(bal), "address": address, "token_address": token_address}
            self.cache.set("balance", cache_key, result)
            return result
        except Exception as e:
            logger.warning(f"ERC20 balance error: {e}")
            return {"balance": "0", "error": str(e)}

    def get_token_info(self, chain_key: str, token_address: str) -> Dict:
        cache_key = f"{chain_key}_token_{token_address.lower()}"
        cached = self.cache.get("token_info", cache_key)
        if cached:
            return cached
        w3 = Web3ProviderFactory.get(chain_key)
        if not w3:
            return {}
        result = {"address": token_address}
        try:
            contract = w3.eth.contract(address=to_checksum_address(token_address), abi=ERC20_ABI)
            result["name"] = contract.functions.name().call()
            result["symbol"] = contract.functions.symbol().call()
            result["decimals"] = contract.functions.decimals().call()
            result["total_supply"] = str(contract.functions.totalSupply().call())
            result["is_erc20"] = True
        except Exception as e:
            logger.warning(f"Token info error for {token_address}: {e}")
            result["is_erc20"] = False
        self.cache.set("token_info", cache_key, result)
        return result

    def get_address_transactions(
        self, chain_key: str, address: str, page: int = 1, page_size: int = 20,
        direction: str = "both",
    ) -> Dict:
        session = self.db.session()
        try:
            addr = address.lower()
            query = session.query(Transaction).filter(Transaction.chain_key == chain_key)
            if direction == "in":
                query = query.filter(Transaction.to_address.ilike(addr))
            elif direction == "out":
                query = query.filter(Transaction.from_address.ilike(addr))
            else:
                query = query.filter(
                    (Transaction.from_address.ilike(addr)) | (Transaction.to_address.ilike(addr))
                )
            total = query.count()
            items = query.order_by(Transaction.block_number.desc()) \
                .offset((page - 1) * page_size).limit(page_size).all()
            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": [t.to_dict() for t in items],
            }
        finally:
            session.close()

    def get_address_token_holdings(self, chain_key: str, address: str) -> List[Dict]:
        session = self.db.session()
        try:
            rows = session.query(TokenBalance).filter(
                TokenBalance.chain_key == chain_key,
                TokenBalance.address.ilike(address.lower()),
            ).all()
            result = []
            for row in rows:
                token = self.get_token_info(chain_key, row.token_address)
                result.append({
                    "token_address": row.token_address,
                    "balance": row.balance,
                    "token": token,
                })
            return result
        finally:
            session.close()

    def get_recent_transactions(self, chain_key: str, address: str, limit: int = 10) -> List[Dict]:
        res = self.get_address_transactions(chain_key, address, page=1, page_size=limit)
        return res.get("items", [])

    # ---- Event logs ----

    def get_logs(self, chain_key: str, from_block: int, to_block: int,
                 address: Optional[str] = None, topics: Optional[List[str]] = None) -> List[Dict]:
        rpc = self.rpc_mgr.get(chain_key)
        if not rpc:
            return []
        params: Dict = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }
        if address:
            params["address"] = address
        if topics:
            params["topics"] = topics
        logs = rpc.call("eth_getLogs", [params]) or []
        return [decode_event_log(l) for l in logs]

    # ---- Statistics ----

    def get_address_activity(self, chain_key: str, address: str, period: str = "day") -> Dict:
        session = self.db.session()
        try:
            from sqlalchemy import func, and_
            addr = address.lower()
            if period == "week":
                date_group = func.strftime('%Y-%W', Transaction.timestamp)
            elif period == "month":
                date_group = func.strftime('%Y-%m', Transaction.timestamp)
            else:
                date_group = func.strftime('%Y-%m-%d', Transaction.timestamp)

            rows = session.query(
                date_group.label("period"),
                func.count(Transaction.id).label("count"),
            ).filter(
                Transaction.chain_key == chain_key,
                (Transaction.from_address.ilike(addr)) | (Transaction.to_address.ilike(addr))
            ).group_by("period").order_by("period").all()

            return {
                "period": period,
                "address": address,
                "data": [{"period": r.period, "count": r.count} for r in rows],
            }
        finally:
            session.close()

    def get_top_tokens(self, chain_key: str, limit: int = 20) -> List[Dict]:
        session = self.db.session()
        try:
            from sqlalchemy import func
            rows = session.query(
                EventLog.address,
                func.count(EventLog.id).label("count"),
            ).filter(
                EventLog.chain_key == chain_key,
                EventLog.topic0 == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
            ).group_by(EventLog.address).order_by(func.count(EventLog.id).desc()).limit(limit).all()
            result = []
            for r in rows:
                info = self.get_token_info(chain_key, r.address)
                result.append({"address": r.address, "transfer_count": r.count, "token": info})
            return result
        finally:
            session.close()

    def get_gas_price_trend(self, chain_key: str, hours: int = 24) -> List[Dict]:
        session = self.db.session()
        try:
            from sqlalchemy import func
            limit = hours * 12
            rows = session.query(
                Block.timestamp,
                Block.base_fee_per_gas,
            ).filter(
                Block.chain_key == chain_key,
            ).order_by(Block.number.desc()).limit(limit).all()
            return [{"timestamp": r.timestamp.isoformat() if r.timestamp else None,
                     "base_fee_per_gas": r.base_fee_per_gas} for r in rows]
        finally:
            session.close()
