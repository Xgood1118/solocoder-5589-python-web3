import logging
from datetime import datetime
from typing import Dict, List, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from .chains import ChainManager
from .rpc_client import RpcClientManager
from .models import (
    DatabaseManager, Block, Transaction, EventLog, ChainSyncState, InternalTransaction,
)
from .config_loader import AppConfig
from .decoder import decode_revert_reason


logger = logging.getLogger(__name__)


def _to_int(value):
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    try:
        return int(value)
    except Exception:
        return 0


def _ts_to_dt(ts):
    try:
        return datetime.utcfromtimestamp(_to_int(ts))
    except Exception:
        return datetime.utcnow()


class SyncService:
    def __init__(self):
        self.chain_mgr = ChainManager()
        self.rpc_mgr = RpcClientManager()
        self.db = DatabaseManager()
        self.cfg = AppConfig()
        self.scheduler: Optional[BackgroundScheduler] = None

    def start(self):
        if not self.cfg.scheduler.get("enabled", True):
            logger.info("Scheduler disabled by config")
            return
        self.scheduler = BackgroundScheduler(timezone="UTC")
        block_interval = self.cfg.scheduler.get("block_sync_interval_seconds", 15)
        event_interval = self.cfg.scheduler.get("event_sync_interval_seconds", 30)
        for chain_key in self.chain_mgr.keys():
            self.scheduler.add_job(
                self.sync_blocks,
                trigger=IntervalTrigger(seconds=block_interval),
                args=[chain_key],
                id=f"sync_blocks_{chain_key}",
                max_instances=1,
                coalesce=True,
            )
            self.scheduler.add_job(
                self.sync_events,
                trigger=IntervalTrigger(seconds=event_interval),
                args=[chain_key],
                id=f"sync_events_{chain_key}",
                max_instances=1,
                coalesce=True,
            )
        self.scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self):
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    def _get_sync_state(self, chain_key: str) -> ChainSyncState:
        session = self.db.session()
        try:
            state = session.query(ChainSyncState).filter(ChainSyncState.chain_key == chain_key).first()
            if not state:
                state = ChainSyncState(chain_key=chain_key, latest_block_number=0)
                session.add(state)
                session.commit()
                session.refresh(state)
            return state
        finally:
            session.close()

    def _update_sync_state(self, chain_key: str, block_number: int, block_hash: str):
        session = self.db.session()
        try:
            state = session.query(ChainSyncState).filter(ChainSyncState.chain_key == chain_key).first()
            if not state:
                state = ChainSyncState(chain_key=chain_key)
                session.add(state)
            state.latest_block_number = block_number
            state.latest_block_hash = block_hash
            state.last_sync_time = datetime.utcnow()
            session.commit()
        finally:
            session.close()

    def _detect_and_handle_reorg(self, chain_key: str, current_block_number: int, current_block_hash: str) -> int:
        scan_depth = self.cfg.scheduler.get("reorg_scan_depth", 20)
        rpc = self.rpc_mgr.get(chain_key)
        if not rpc:
            return current_block_number

        session = self.db.session()
        try:
            check_start = max(0, current_block_number - scan_depth)
            for n in range(current_block_number, check_start - 1, -1):
                remote_block = rpc.call("eth_getBlockByNumber", [hex(n), False])
                if not remote_block:
                    continue
                remote_hash = remote_block.get("hash")
                local = session.query(Block).filter(
                    Block.chain_key == chain_key,
                    Block.number == n,
                ).first()
                if local and local.hash != remote_hash:
                    logger.warning(f"Reorg detected on {chain_key} at block {n}")
                    self._rollback_blocks(chain_key, n)
                    state = session.query(ChainSyncState).filter(ChainSyncState.chain_key == chain_key).first()
                    if state:
                        state.reorg_count = (state.reorg_count or 0) + 1
                        state.last_reorg_detected_at = datetime.utcnow()
                        state.latest_block_number = n - 1
                        state.latest_block_hash = None
                    session.commit()
                    return n - 1
            return current_block_number
        finally:
            session.close()

    def _rollback_blocks(self, chain_key: str, from_block_number: int):
        session = self.db.session()
        try:
            session.query(EventLog).filter(
                EventLog.chain_key == chain_key,
                EventLog.block_number >= from_block_number,
            ).delete(synchronize_session=False)
            session.query(InternalTransaction).filter(
                InternalTransaction.chain_key == chain_key,
                InternalTransaction.block_number >= from_block_number,
            ).delete(synchronize_session=False)
            session.query(Transaction).filter(
                Transaction.chain_key == chain_key,
                Transaction.block_number >= from_block_number,
            ).delete(synchronize_session=False)
            session.query(Block).filter(
                Block.chain_key == chain_key,
                Block.number >= from_block_number,
            ).delete(synchronize_session=False)
            session.commit()
            logger.info(f"Rolled back blocks >= {from_block_number} on {chain_key}")
        except Exception as e:
            logger.error(f"Rollback error: {e}")
            session.rollback()
        finally:
            session.close()

    def sync_blocks(self, chain_key: str, max_blocks_per_run: int = 100):
        try:
            rpc = self.rpc_mgr.get(chain_key)
            if not rpc:
                return
            state = self._get_sync_state(chain_key)
            remote_latest_hex = rpc.call("eth_blockNumber", [])
            if not remote_latest_hex:
                return
            remote_latest = _to_int(remote_latest_hex)

            if state.latest_block_hash:
                corrected = self._detect_and_handle_reorg(chain_key, state.latest_block_number, state.latest_block_hash)
                if corrected < state.latest_block_number:
                    state = self._get_sync_state(chain_key)

            start_block = state.latest_block_number + 1
            end_block = min(start_block + max_blocks_per_run - 1, remote_latest)
            if start_block > end_block:
                return

            logger.info(f"Syncing {chain_key} blocks {start_block} -> {end_block}")
            for n in range(start_block, end_block + 1):
                self._sync_single_block(chain_key, n)
            self._update_sync_state(chain_key, end_block, None)
        except Exception as e:
            logger.error(f"sync_blocks error for {chain_key}: {e}", exc_info=True)

    def _sync_single_block(self, chain_key: str, block_number: int):
        rpc = self.rpc_mgr.get(chain_key)
        if not rpc:
            return
        block_data = rpc.call("eth_getBlockByNumber", [hex(block_number), True])
        if not block_data:
            return

        session = self.db.session()
        try:
            existing = session.query(Block).filter(
                Block.chain_key == chain_key,
                Block.number == block_number,
            ).first()
            if existing:
                return

            block_ts = _ts_to_dt(block_data.get("timestamp"))
            txs = block_data.get("transactions", [])
            block = Block(
                chain_key=chain_key,
                number=block_number,
                hash=block_data.get("hash"),
                parent_hash=block_data.get("parentHash"),
                nonce=block_data.get("nonce"),
                sha3_uncles=block_data.get("sha3Uncles"),
                logs_bloom=block_data.get("logsBloom"),
                transactions_root=block_data.get("transactionsRoot"),
                state_root=block_data.get("stateRoot"),
                receipts_root=block_data.get("receiptsRoot"),
                miner=block_data.get("miner"),
                difficulty=str(_to_int(block_data.get("difficulty"))),
                total_difficulty=str(_to_int(block_data.get("totalDifficulty"))),
                size=_to_int(block_data.get("size")),
                extra_data=block_data.get("extraData"),
                gas_limit=_to_int(block_data.get("gasLimit")),
                gas_used=_to_int(block_data.get("gasUsed")),
                timestamp=block_ts,
                transaction_count=len(txs) if isinstance(txs, list) else 0,
                base_fee_per_gas=str(_to_int(block_data.get("baseFeePerGas"))) if block_data.get("baseFeePerGas") else None,
            )
            session.add(block)

            if isinstance(txs, list):
                for tx in txs:
                    if isinstance(tx, dict):
                        self._save_transaction(session, chain_key, tx, block_ts)

            session.commit()
            self._update_sync_state(chain_key, block_number, block_data.get("hash"))
        except Exception as e:
            logger.error(f"Error syncing block {block_number} on {chain_key}: {e}")
            session.rollback()
        finally:
            session.close()

    def _save_transaction(self, session, chain_key: str, tx: dict, block_ts: datetime):
        try:
            existing = session.query(Transaction).filter(Transaction.hash == tx.get("hash")).first()
            if existing:
                return
            tx_hash = tx.get("hash")
            input_data = tx.get("input", "0x")
            tr = Transaction(
                chain_key=chain_key,
                block_number=_to_int(tx.get("blockNumber")),
                block_hash=tx.get("blockHash"),
                hash=tx_hash,
                from_address=tx.get("from"),
                to_address=tx.get("to"),
                value=str(_to_int(tx.get("value"))),
                nonce=_to_int(tx.get("nonce")),
                gas=_to_int(tx.get("gas")),
                gas_price=str(_to_int(tx.get("gasPrice"))) if tx.get("gasPrice") else None,
                max_fee_per_gas=str(_to_int(tx.get("maxFeePerGas"))) if tx.get("maxFeePerGas") else None,
                max_priority_fee_per_gas=str(_to_int(tx.get("maxPriorityFeePerGas"))) if tx.get("maxPriorityFeePerGas") else None,
                input_data=input_data,
                transaction_index=_to_int(tx.get("transactionIndex")),
                transaction_type=_to_int(tx.get("type")) if tx.get("type") else None,
                timestamp=block_ts,
            )
            session.add(tr)
            session.flush()
            self._fetch_and_save_receipt(session, chain_key, tx_hash, block_ts)
        except Exception as e:
            logger.warning(f"Error saving tx {tx.get('hash')}: {e}")

    def _fetch_and_save_receipt(self, session, chain_key: str, tx_hash: str, block_ts: datetime):
        try:
            rpc = self.rpc_mgr.get(chain_key)
            if not rpc:
                return
            receipt = rpc.call("eth_getTransactionReceipt", [tx_hash])
            if not receipt:
                return
            tx = session.query(Transaction).filter(Transaction.hash == tx_hash).first()
            if not tx:
                return
            tx.status = _to_int(receipt.get("status"))
            tx.gas_used = _to_int(receipt.get("gasUsed"))
            tx.cumulative_gas_used = _to_int(receipt.get("cumulativeGasUsed"))
            tx.contract_address = receipt.get("contractAddress")
            if tx.status == 0:
                tx.revert_reason = decode_revert_reason(receipt.get("output", ""))

            logs = receipt.get("logs", []) or []
            for lg in logs:
                topics = lg.get("topics", [])
                el = EventLog(
                    chain_key=chain_key,
                    block_number=_to_int(lg.get("blockNumber")),
                    block_hash=lg.get("blockHash"),
                    transaction_hash=lg.get("transactionHash"),
                    transaction_index=_to_int(lg.get("transactionIndex")),
                    log_index=_to_int(lg.get("logIndex")),
                    address=lg.get("address"),
                    data=lg.get("data", "0x"),
                    topic0=topics[0] if len(topics) > 0 else None,
                    topic1=topics[1] if len(topics) > 1 else None,
                    topic2=topics[2] if len(topics) > 2 else None,
                    topic3=topics[3] if len(topics) > 3 else None,
                    removed=bool(lg.get("removed", False)),
                    timestamp=block_ts,
                )
                session.add(el)
        except Exception as e:
            logger.warning(f"Error processing receipt for {tx_hash}: {e}")

    def sync_events(self, chain_key: str, blocks_per_run: int = 50):
        try:
            chain = self.chain_mgr.get(chain_key)
            rpc = self.rpc_mgr.get(chain_key)
            if not chain or not rpc:
                return
            state = self._get_sync_state(chain_key)
            if state.latest_block_number <= 0:
                return
            from_block = max(0, state.latest_block_number - blocks_per_run)
            to_block = state.latest_block_number
            topics = chain.event_topics or []
            if not topics:
                return
            logs = rpc.call("eth_getLogs", [{
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "topics": [topics],
            }]) or []
            logger.info(f"Event sync {chain_key}: {len(logs)} logs from blocks {from_block}-{to_block}")
        except Exception as e:
            logger.error(f"sync_events error for {chain_key}: {e}")
