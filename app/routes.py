from flask import Blueprint, jsonify, request
from typing import Optional
from .chains import ChainManager
from .blockchain_service import BlockchainService
from .auth import optional_auth
from .models import DatabaseManager, ChainSyncState


api_bp = Blueprint("api", __name__)
service = BlockchainService()
chain_mgr = ChainManager()


def _bad_request(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


def _not_found(msg: str):
    return jsonify({"error": msg}), 404


def _ok(data):
    return jsonify({"data": data})


def _validate_chain(chain_key: str) -> Optional[str]:
    if chain_key not in chain_mgr.keys():
        return f"Unsupported chain: {chain_key}. Available: {', '.join(chain_mgr.keys())}"
    return None


# ---- Health & Info ----

@api_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": "1.0.0"})


@api_bp.route("/chains", methods=["GET"])
@optional_auth()
def list_chains():
    result = []
    for key, chain in chain_mgr.all().items():
        result.append({
            "key": chain.key,
            "chain_id": chain.chain_id,
            "name": chain.name,
            "native_currency": {
                "name": chain.native_currency.name,
                "symbol": chain.native_currency.symbol,
                "decimals": chain.native_currency.decimals,
            },
            "block_time": chain.block_time,
            "explorer_url": chain.explorer_url,
            "rpc_node_count": len(chain.rpc_nodes),
        })
    db = DatabaseManager()
    session = db.session()
    try:
        for r in result:
            state = session.query(ChainSyncState).filter(ChainSyncState.chain_key == r["key"]).first()
            if state:
                r["latest_synced_block"] = state.latest_block_number
                r["last_sync_time"] = state.last_sync_time.isoformat() if state.last_sync_time else None
    finally:
        session.close()
    return _ok(result)


# ---- Block endpoints ----

@api_bp.route("/<chain_key>/block/number", methods=["GET"])
@optional_auth()
def get_latest_block(chain_key: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    num = service.get_latest_block_number(chain_key)
    return _ok({"number": num})


@api_bp.route("/<chain_key>/block/<block_id>", methods=["GET"])
@optional_auth()
def get_block(chain_key: str, block_id: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    with_tx = request.args.get("transactions", "false").lower() in ("1", "true", "yes")
    block = None
    if block_id.startswith("0x") and len(block_id) == 66:
        block = service.get_block_by_hash(chain_key, block_id, with_tx)
    elif block_id.isdigit():
        block = service.get_block_by_number(chain_key, int(block_id), with_tx)
    else:
        return _bad_request("Invalid block_id: must be block number (int) or block hash (0x...)")
    if not block:
        return _not_found("Block not found")
    return _ok(block)


@api_bp.route("/<chain_key>/blocks/range", methods=["GET"])
@optional_auth()
def get_block_range(chain_key: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    start = request.args.get("start")
    end = request.args.get("end")
    if not start or not end or not start.isdigit() or not end.isdigit():
        return _bad_request("Missing or invalid 'start' and 'end' query params (integers required)")
    start_n, end_n = int(start), int(end)
    if end_n < start_n:
        return _bad_request("'end' must be >= 'start'")
    if end_n - start_n > 100:
        return _bad_request("Range too large: max 100 blocks")
    blocks = service.get_block_range(chain_key, start_n, end_n)
    return _ok({"count": len(blocks), "blocks": blocks})


# ---- Transaction endpoints ----

@api_bp.route("/<chain_key>/tx/<tx_hash>", methods=["GET"])
@optional_auth()
def get_transaction(chain_key: str, tx_hash: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        return _bad_request("Invalid transaction hash format")
    tx = service.get_transaction(chain_key, tx_hash)
    if not tx:
        return _not_found("Transaction not found")
    return _ok(tx)


@api_bp.route("/<chain_key>/tx/<tx_hash>/internal", methods=["GET"])
@optional_auth()
def get_internal_transactions(chain_key: str, tx_hash: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        return _bad_request("Invalid transaction hash format")
    itxs = service.get_transaction_internal(chain_key, tx_hash)
    return _ok({"count": len(itxs), "items": itxs})


# ---- Address endpoints ----

@api_bp.route("/<chain_key>/address/<address>/balance", methods=["GET"])
@optional_auth()
def get_native_balance(chain_key: str, address: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    if not address.startswith("0x") or len(address) != 42:
        return _bad_request("Invalid address format")
    balance = service.get_native_balance(chain_key, address)
    return _ok(balance)


@api_bp.route("/<chain_key>/address/<address>/token/<token_address>/balance", methods=["GET"])
@optional_auth()
def get_erc20_balance(chain_key: str, address: str, token_address: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    for a in (address, token_address):
        if not a.startswith("0x") or len(a) != 42:
            return _bad_request(f"Invalid address format: {a}")
    balance = service.get_erc20_balance(chain_key, address, token_address)
    return _ok(balance)


@api_bp.route("/<chain_key>/address/<address>/transactions", methods=["GET"])
@optional_auth()
def get_address_transactions(chain_key: str, address: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    if not address.startswith("0x") or len(address) != 42:
        return _bad_request("Invalid address format")
    page = int(request.args.get("page", 1))
    page_size = min(int(request.args.get("page_size", 20)), 100)
    direction = request.args.get("direction", "both")
    if direction not in ("in", "out", "both"):
        return _bad_request("direction must be in | out | both")
    result = service.get_address_transactions(chain_key, address, page, page_size, direction)
    return _ok(result)


@api_bp.route("/<chain_key>/address/<address>/tokens", methods=["GET"])
@optional_auth()
def get_address_tokens(chain_key: str, address: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    if not address.startswith("0x") or len(address) != 42:
        return _bad_request("Invalid address format")
    holdings = service.get_address_token_holdings(chain_key, address)
    return _ok({"count": len(holdings), "items": holdings})


@api_bp.route("/<chain_key>/address/<address>/recent", methods=["GET"])
@optional_auth()
def get_recent_txs(chain_key: str, address: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    if not address.startswith("0x") or len(address) != 42:
        return _bad_request("Invalid address format")
    limit = min(int(request.args.get("limit", 10)), 100)
    items = service.get_recent_transactions(chain_key, address, limit)
    return _ok({"count": len(items), "items": items})


# ---- Token info ----

@api_bp.route("/<chain_key>/token/<token_address>", methods=["GET"])
@optional_auth()
def get_token_info(chain_key: str, token_address: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    if not token_address.startswith("0x") or len(token_address) != 42:
        return _bad_request("Invalid token address format")
    info = service.get_token_info(chain_key, token_address)
    return _ok(info)


# ---- Event logs ----

@api_bp.route("/<chain_key>/logs", methods=["GET"])
@optional_auth()
def get_logs(chain_key: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    from_block = request.args.get("from_block")
    to_block = request.args.get("to_block")
    address = request.args.get("address")
    topic0 = request.args.get("topic0")
    if not from_block or not to_block or not from_block.isdigit() or not to_block.isdigit():
        return _bad_request("Missing/invalid from_block and to_block (integers required)")
    from_n, to_n = int(from_block), int(to_block)
    if to_n - from_n > 5000:
        return _bad_request("Range too large: max 5000 blocks")
    topics = [topic0] if topic0 else None
    logs = service.get_logs(chain_key, from_n, to_n, address, topics)
    return _ok({"count": len(logs), "items": logs})


# ---- Statistics endpoints ----

@api_bp.route("/<chain_key>/stats/address/<address>/activity", methods=["GET"])
@optional_auth()
def get_address_activity(chain_key: str, address: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    period = request.args.get("period", "day")
    if period not in ("day", "week", "month"):
        return _bad_request("period must be day | week | month")
    data = service.get_address_activity(chain_key, address, period)
    return _ok(data)


@api_bp.route("/<chain_key>/stats/top-tokens", methods=["GET"])
@optional_auth()
def get_top_tokens(chain_key: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    limit = min(int(request.args.get("limit", 20)), 100)
    data = service.get_top_tokens(chain_key, limit)
    return _ok({"count": len(data), "items": data})


@api_bp.route("/<chain_key>/stats/gas-trend", methods=["GET"])
@optional_auth()
def get_gas_trend(chain_key: str):
    err = _validate_chain(chain_key)
    if err:
        return _bad_request(err)
    hours = min(int(request.args.get("hours", 24)), 168)
    data = service.get_gas_price_trend(chain_key, hours)
    return _ok({"count": len(data), "items": data})
