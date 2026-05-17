#!/usr/bin/env python3
"""
GasLens — Glamsterdam Gas Cost Simulator v0.3.4 (Refined Heuristics)
"""

import json
import sys
import urllib.request
import ssl
from dataclasses import dataclass


ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

CALLDATA_FLOOR_PRE = 10
CALLDATA_FLOOR_POST = 64
COST_NEW_ACCOUNT_PRE = 25_000
COST_SSTORE_INIT_PRE = 20_000
CPSB = 1530
COST_NEW_ACCOUNT_POST = 120 * CPSB
COST_SSTORE_INIT_POST = 64 * CPSB


@dataclass
class PriceConfig:
    eth_price_usd: float = 3500.0
    gas_price_gwei: float = 20.0
    def gas_to_usd(self, gas: int) -> float:
        return gas * self.gas_price_gwei * 1e-9 * self.eth_price_usd


@dataclass
class GasBreakdown:
    calldata: int = 0
    state_creation: int = 0
    base_execution: int = 21_000
    total: int = 0


@dataclass
class FunctionEstimate:
    function_name: str
    selector: str
    pre_gas: GasBreakdown
    post_gas: GasBreakdown
    pre_usd: float = 0.0
    post_usd: float = 0.0
    delta_pct: float = 0.0
    risk_level: str = ""


def abi_type_size(abi_type: str) -> int:
    if abi_type in ("address", "bool") or abi_type.startswith(("uint", "int")):
        return 32
    elif abi_type in ("bytes", "string") or abi_type.endswith("[]") or abi_type.startswith(("bytes", "string")):
        return 64
    return 32


def function_calldata_size(function_abi: dict) -> int:
    size = 4
    for inp in function_abi.get("inputs", []):
        size += abi_type_size(inp.get("type", ""))
    return size


def count_calldata_tokens(data_bytes: bytes) -> int:
    tokens = 0
    i = 0
    while i < len(data_bytes):
        if i + 3 < len(data_bytes) and all(b != 0 for b in data_bytes[i:i+4]):
            tokens += 1
            i += 4
        else:
            tokens += 1
            i += 1
    return tokens


def calldata_cost(data_bytes: bytes, floor_per_token: int) -> int:
    return count_calldata_tokens(data_bytes) * floor_per_token


def estimate_function(function_abi: dict, config: PriceConfig, contract_hints: dict = None) -> FunctionEstimate:
    name = function_abi.get("name", "unknown")
    selector = function_abi.get("selector", "0x????????")
    
    calldata_size = function_calldata_size(function_abi)
    calldata_bytes = b'\x00' * calldata_size
    
    calldata_pre = calldata_cost(calldata_bytes, CALLDATA_FLOOR_PRE)
    calldata_post = calldata_cost(calldata_bytes, CALLDATA_FLOOR_POST)
    
    # Refined heuristics
    state_pre = state_post = 0
    
    # Only factory/creation functions get new account cost
    if any(kw in name.lower() for kw in ["factory", "create", "deploy", "init", "constructor"]):
        state_pre = COST_NEW_ACCOUNT_PRE
        state_post = COST_NEW_ACCOUNT_POST
    
    # SSTORE only for functions that CLEARLY write state (not swap/transfer which delegate)
    elif any(kw in name.lower() for kw in ["mint", "burn", "set", "add", "register", "update", "store"]):
        # But NOT "swap", "transfer", "remove" which typically delegate
        if not any(bad in name.lower() for bad in ["swap", "transfer", "remove", "quote", "get", "weth"]):
            state_pre = COST_SSTORE_INIT_PRE
            state_post = COST_SSTORE_INIT_POST
    
    pre = GasBreakdown(calldata_pre, state_pre, 21_000, 21_000 + calldata_pre + state_pre)
    post = GasBreakdown(calldata_post, state_post, 21_000, 21_000 + calldata_post + state_post)
    
    pre_usd = config.gas_to_usd(pre.total)
    post_usd = config.gas_to_usd(post.total)
    delta_pct = ((post.total - pre.total) / pre.total * 100) if pre.total > 0 else 0
    
    if delta_pct > 200:
        risk = "CRITICAL"
    elif delta_pct > 50:
        risk = "HIGH"
    elif delta_pct > 10:
        risk = "MODERATE"
    elif delta_pct > 0:
        risk = "LOW"
    else:
        risk = "NONE"
    
    return FunctionEstimate(name, selector, pre, post, pre_usd, post_usd, delta_pct, risk)


def analyze_abi(abi_json: list, config: PriceConfig) -> list[FunctionEstimate]:
    results = []
    for item in abi_json:
        if item.get("type") == "function":
            results.append(estimate_function(item, config))
    results.sort(key=lambda x: x.delta_pct, reverse=True)
    return results


def print_report(results: list[FunctionEstimate], config: PriceConfig, name: str = "Unknown"):
    print(f"\n{'='*80}")
    print(f"GasLens — Glamsterdam Cost Analysis: {name}")
    print(f"{'='*80}")
    print(f"ETH: ${config.eth_price_usd:,.0f} | Gas: {config.gas_price_gwei} gwei")
    print("-" * 80)
    
    for est in results:
        print(f"\n[{est.risk_level}] {est.function_name} ({est.selector})")
        print(f"  Gas:  {est.pre_gas.total:>12,} → {est.post_gas.total:>12,} ({est.delta_pct:+.1f}%)")
        print(f"  USD:  ${est.pre_usd:>10.2f} → ${est.post_usd:>10.2f} (+${est.post_usd-est.pre_usd:+.2f})")
        print(f"  Breakdown: calldata {est.pre_gas.calldata:,}→{est.post_gas.calldata:,}, state {est.pre_gas.state_creation:,}→{est.post_gas.state_creation:,}")


def fetch_etherscan_v2(address: str, api_key: str) -> list:
    url = f"https://api.etherscan.io/v2/api?chainid=1&module=contract&action=getabi&address={address}&apikey={api_key}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, context=ssl_context, timeout=30) as response:
            data = json.loads(response.read().decode())
            if data.get("status") != "1":
                print(f"Etherscan error: {data.get('result')}", file=sys.stderr)
                return []
            return json.loads(data.get("result", "[]"))
    except Exception as e:
        print(f"Fetch failed: {e}", file=sys.stderr)
        return []


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--abi", help="ABI JSON file path")
    ap.add_argument("--etherscan", help="Contract address")
    ap.add_argument("--api-key", help="Etherscan API key")
    ap.add_argument("--name", default="Unknown")
    ap.add_argument("--eth-price", type=float, default=3500.0)
    ap.add_argument("--gas-price", type=float, default=20.0)
    args = ap.parse_args()
    
    abi = []
    if args.abi:
        with open(args.abi) as f:
            abi = json.load(f)
    elif args.etherscan and args.api_key:
        abi = fetch_etherscan_v2(args.etherscan, args.api_key)
        if not abi:
            sys.exit(1)
    else:
        print("Provide --abi or --etherscan + --api-key", file=sys.stderr)
        sys.exit(1)
    
    config = PriceConfig(args.eth_price, args.gas_price)
    results = analyze_abi(abi, config)
    print_report(results, config, args.name)
    
    critical = sum(1 for r in results if r.delta_pct > 200)
    high = sum(1 for r in results if 50 < r.delta_pct <= 200)
    moderate = sum(1 for r in results if 10 < r.delta_pct <= 50)
    print(f"\n{'='*80}")
    print(f"SUMMARY: {len(results)} functions | {critical} CRITICAL | {high} HIGH | {moderate} MODERATE")


if __name__ == "__main__":
    main()
