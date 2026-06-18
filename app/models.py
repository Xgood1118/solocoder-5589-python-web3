import os
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Text, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
from .config_loader import AppConfig


Base = declarative_base()


class Block(Base):
    __tablename__ = "blocks"
    id = Column(Integer, primary_key=True)
    chain_key = Column(String(32), nullable=False)
    number = Column(BigInteger, nullable=False)
    hash = Column(String(66), nullable=False)
    parent_hash = Column(String(66))
    nonce = Column(String(18))
    sha3_uncles = Column(String(66))
    logs_bloom = Column(Text)
    transactions_root = Column(String(66))
    state_root = Column(String(66))
    receipts_root = Column(String(66))
    miner = Column(String(42))
    difficulty = Column(String(40))
    total_difficulty = Column(String(40))
    size = Column(BigInteger)
    extra_data = Column(Text)
    gas_limit = Column(BigInteger)
    gas_used = Column(BigInteger)
    timestamp = Column(DateTime, nullable=False)
    transaction_count = Column(Integer, default=0)
    base_fee_per_gas = Column(String(40))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_blocks_chain_number", "chain_key", "number", unique=True),
        Index("idx_blocks_chain_hash", "chain_key", "hash", unique=True),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chain_key": self.chain_key,
            "number": self.number,
            "hash": self.hash,
            "parent_hash": self.parent_hash,
            "miner": self.miner,
            "difficulty": self.difficulty,
            "total_difficulty": self.total_difficulty,
            "size": self.size,
            "gas_limit": self.gas_limit,
            "gas_used": self.gas_used,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "transaction_count": self.transaction_count,
            "base_fee_per_gas": self.base_fee_per_gas,
        }


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    chain_key = Column(String(32), nullable=False)
    block_number = Column(BigInteger, nullable=False)
    block_hash = Column(String(66), nullable=False)
    hash = Column(String(66), nullable=False, unique=True)
    from_address = Column(String(42), nullable=False)
    to_address = Column(String(42))
    value = Column(String(40))
    nonce = Column(BigInteger)
    gas = Column(BigInteger)
    gas_price = Column(String(40))
    max_fee_per_gas = Column(String(40))
    max_priority_fee_per_gas = Column(String(40))
    input_data = Column(Text)
    transaction_index = Column(Integer)
    transaction_type = Column(Integer)
    status = Column(Integer)
    gas_used = Column(BigInteger)
    cumulative_gas_used = Column(BigInteger)
    revert_reason = Column(String(512))
    contract_address = Column(String(42))
    timestamp = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_tx_chain_from", "chain_key", "from_address"),
        Index("idx_tx_chain_to", "chain_key", "to_address"),
        Index("idx_tx_chain_block", "chain_key", "block_number"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chain_key": self.chain_key,
            "block_number": self.block_number,
            "block_hash": self.block_hash,
            "hash": self.hash,
            "from": self.from_address,
            "to": self.to_address,
            "value": self.value,
            "nonce": self.nonce,
            "gas": self.gas,
            "gas_price": self.gas_price,
            "max_fee_per_gas": self.max_fee_per_gas,
            "max_priority_fee_per_gas": self.max_priority_fee_per_gas,
            "input": self.input_data,
            "transaction_index": self.transaction_index,
            "type": self.transaction_type,
            "status": self.status,
            "gas_used": self.gas_used,
            "cumulative_gas_used": self.cumulative_gas_used,
            "revert_reason": self.revert_reason,
            "contract_address": self.contract_address,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class EventLog(Base):
    __tablename__ = "event_logs"
    id = Column(Integer, primary_key=True)
    chain_key = Column(String(32), nullable=False)
    block_number = Column(BigInteger, nullable=False)
    block_hash = Column(String(66), nullable=False)
    transaction_hash = Column(String(66), nullable=False)
    transaction_index = Column(Integer)
    log_index = Column(Integer)
    address = Column(String(42), nullable=False)
    data = Column(Text)
    topic0 = Column(String(66))
    topic1 = Column(String(66))
    topic2 = Column(String(66))
    topic3 = Column(String(66))
    removed = Column(Boolean, default=False)
    timestamp = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_log_chain_block", "chain_key", "block_number"),
        Index("idx_log_chain_tx", "chain_key", "transaction_hash"),
        Index("idx_log_chain_addr", "chain_key", "address"),
        Index("idx_log_chain_topic0", "chain_key", "topic0"),
    )

    def to_dict(self) -> dict:
        topics = []
        for t in [self.topic0, self.topic1, self.topic2, self.topic3]:
            if t:
                topics.append(t)
        return {
            "id": self.id,
            "chain_key": self.chain_key,
            "block_number": self.block_number,
            "block_hash": self.block_hash,
            "transaction_hash": self.transaction_hash,
            "transaction_index": self.transaction_index,
            "log_index": self.log_index,
            "address": self.address,
            "data": self.data,
            "topics": topics,
            "removed": self.removed,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class Token(Base):
    __tablename__ = "tokens"
    id = Column(Integer, primary_key=True)
    chain_key = Column(String(32), nullable=False)
    address = Column(String(42), nullable=False)
    name = Column(String(256))
    symbol = Column(String(64))
    decimals = Column(Integer, default=18)
    total_supply = Column(String(40))
    is_erc20 = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_token_chain_addr", "chain_key", "address", unique=True),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chain_key": self.chain_key,
            "address": self.address,
            "name": self.name,
            "symbol": self.symbol,
            "decimals": self.decimals,
            "total_supply": self.total_supply,
            "is_erc20": self.is_erc20,
        }


class TokenBalance(Base):
    __tablename__ = "token_balances"
    id = Column(Integer, primary_key=True)
    chain_key = Column(String(32), nullable=False)
    address = Column(String(42), nullable=False)
    token_address = Column(String(42), nullable=False)
    balance = Column(String(40))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_tbal_chain_addr_token", "chain_key", "address", "token_address", unique=True),
    )


class ChainSyncState(Base):
    __tablename__ = "chain_sync_state"
    id = Column(Integer, primary_key=True)
    chain_key = Column(String(32), nullable=False, unique=True)
    latest_block_number = Column(BigInteger, default=0)
    latest_block_hash = Column(String(66))
    last_sync_time = Column(DateTime)
    last_reorg_detected_at = Column(DateTime)
    reorg_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InternalTransaction(Base):
    __tablename__ = "internal_transactions"
    id = Column(Integer, primary_key=True)
    chain_key = Column(String(32), nullable=False)
    transaction_hash = Column(String(66), nullable=False)
    block_number = Column(BigInteger, nullable=False)
    trace_type = Column(String(32))
    from_address = Column(String(42))
    to_address = Column(String(42))
    value = Column(String(40))
    gas = Column(BigInteger)
    gas_used = Column(BigInteger)
    input_data = Column(Text)
    output_data = Column(Text)
    error = Column(String(256))
    trace_address = Column(String(128))
    timestamp = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_itx_chain_tx", "chain_key", "transaction_hash"),
    )


class DatabaseManager:
    _instance = None
    _engine = None
    _SessionLocal = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_engine()
        return cls._instance

    def _init_engine(self):
        cfg = AppConfig()
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_url = cfg.database["url"]
        if db_url.startswith("sqlite:///"):
            db_path = db_url.replace("sqlite:///", "")
            abs_db_path = os.path.join(base_dir, db_path)
            os.makedirs(os.path.dirname(abs_db_path), exist_ok=True)
            db_url = f"sqlite:///{abs_db_path}"
        self._engine = create_engine(db_url, echo=False, future=True)
        Base.metadata.create_all(self._engine)
        self._SessionLocal = sessionmaker(bind=self._engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def session(self):
        return self._SessionLocal()
