"""One-time on-chain approvals: USDC approve + CTF setApprovalForAll."""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

# Polymarket Polygon mainnet contracts (chain 137).
POLYGON_USDC = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e (legacy bridged)
POLYGON_USDC_NATIVE = "0x3c499c542cef5e3811E1192ce70d8cC03d5c3359"  # native USDC
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
CTF_ADAPTER = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
CTF_TOKEN = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"


async def approve_usdc_and_ctf(rpc_url: str | None = None) -> dict:
    """Run the two on-chain approvals required for live CLOB trading.

    Reads POLYMARKET_PRIVATE_KEY from settings; signs locally; broadcasts via web3.py.
    Returns dict of tx hashes per approval.
    """
    from polymarket_agent_core.config import load_settings
    from web3 import Web3

    s = load_settings()
    if not s.polymarket_private_key:
        raise RuntimeError("POLYMARKET_PRIVATE_KEY is empty.")
    rpc = rpc_url or s.polygon_rpc_url or "https://polygon-rpc.com"
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
    if not w3.is_connected():
        raise RuntimeError(f"could not connect to RPC {rpc}")
    acct = w3.eth.account.from_key(s.polymarket_private_key)
    addr = acct.address
    log.info("allowances.start", from_addr=addr, chain_id=w3.eth.chain_id)

    # Minimal ERC20 approve ABI
    erc20_abi = [{
        "constant": False,
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    }]
    erc1155_abi = [{
        "constant": False,
        "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
        "name": "setApprovalForAll",
        "outputs": [],
        "type": "function",
    }]

    tx_hashes: dict[str, str] = {}
    nonce = w3.eth.get_transaction_count(addr)
    gas_price = w3.eth.gas_price

    # USDC.e + native USDC approve to CTF Exchange
    for label, usdc in (("usdc_e", POLYGON_USDC), ("usdc_native", POLYGON_USDC_NATIVE)):
        c = w3.eth.contract(address=Web3.to_checksum_address(usdc), abi=erc20_abi)
        tx = c.functions.approve(
            Web3.to_checksum_address(CTF_EXCHANGE),
            2**256 - 1,
        ).build_transaction({
            "from": addr,
            "nonce": nonce,
            "gas": 100_000,
            "gasPrice": gas_price,
        })
        signed = w3.eth.account.sign_transaction(tx, s.polymarket_private_key)
        h = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
        tx_hashes[f"approve_{label}"] = h
        log.info("allowances.approve", label=label, tx=h)
        nonce += 1

    # CTF setApprovalForAll
    ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF_TOKEN), abi=erc1155_abi)
    tx = ctf.functions.setApprovalForAll(
        Web3.to_checksum_address(CTF_EXCHANGE), True
    ).build_transaction({
        "from": addr,
        "nonce": nonce,
        "gas": 100_000,
        "gasPrice": gas_price,
    })
    signed = w3.eth.account.sign_transaction(tx, s.polymarket_private_key)
    h = w3.eth.send_raw_transaction(signed.raw_transaction).hex()
    tx_hashes["setApprovalForAll_ctf"] = h
    log.info("allowances.set_approval_for_all", tx=h)

    return tx_hashes
