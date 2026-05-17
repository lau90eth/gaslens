#!/usr/bin/env python3
"""
GasLens — EIP-7976 Calldata Floor Analyzer
"""

import json
import sys
import urllib.request
import ssl
from dataclasses import dataclass
from pathlib import Path

# Add glamlib to path (local clone)
glamlib_path = Path(__file__).parent / "glamlib_local"
if glamlib_path.exists():
    sys.path.insert(0, str(glamlib_path))
else:
    # Try home directory
    glamlib_home = Path.home() / "glamlib"
    if glamlib_home.exists():
        sys.path.insert(0, str(glamlib_home))
    else:
        print("Error: glamlib not found. Clone: git clone https://github.com/lau90eth/glamlib.git", file=sys.stderr)
        sys.exit(1)

from glamlib.eips import EIP_7976_FLOOR_PRE, EIP_7976_FLOOR_POST
from glamlib.calldata import count_calldata_tokens, calldata_floor_cost


# SSL workaround for WSL
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE


@dataclass
class FunctionAnalysis:
    function_name: str
    selector: str
    calldata_bytes: int
    tokens: int
    floor_pre: int
    floor_post: int
    headroom_pre: int
    headroom_post: int
    is_floor_dominated_pre: bool
    is_floor_dominated_post: bool


def abi_type_size(abi_type: str) -> int:
    if abi_type in ("address", "bool") or abi_type.startswith(("uint", "int")):
        return 32
    elif abi_type == "bytes" or abi_type.startswith("string"):
        return 64
    elif abi_type.endswith("[]"):
        return 64
    elif abi_type.startswith("bytes"):
        n = int(abi_type[5:]) if len(abi_type) > 5 else 1
        return ((n + 31) // 32) * 32
    return 32


def function_calldata_size(function_abi: dict) -> int:
    size = 4
    for inp in function_abi.get("inputs", []):
        size += abi_type_size(inp.get("type", ""))
    return size


def analyze_function(function_abi: dict) -> FunctionAnalysis:
    name = function_abi.get("name", "unknown")
    selector = function_abi.get("selector", "0x????????")
    
    calldata_size = function_calldata_size(function_abi)
    calldata_bytes = b'\x00' * calldata_size
    
    tokens = count_calldata_tokens(calldata_bytes)
    floor_pre = calldata_floor_cost(calldata_bytes, pre=True)
    floor_post = calldata_floor_cost(calldata_bytes, pre=False)
    
    headroom_pre = max(0, floor_pre - 21_000)
    headroom_post = max(0, floor_post - 21_000)
    
    typical_execution = 100_000
    is_dominated_pre = typical_execution < headroom_pre
    is_dominated_post = typical_execution < headroom_post
    
    return FunctionAnalysis(
        function_name=name,
        selector=selector,
        calldata_bytes=calldata_size,
        tokens=tokens,
        floor_pre=floor_pre,
        floor_post=floor_post,
        headroom_pre=headroom_pre,
        headroom_post=headroom_post,
        is_floor_dominated_pre=is_dominated_pre,
        is_floor_dominated_post=is_dominated_post,
    )


def analyze_abi(abi_json: list) -> list[FunctionAnalysis]:
    results = []
    for item in abi_json:
        if item.get("type") == "function":
            results.append(analyze_function(item))
    results.sort(key=lambda x: x.floor_post, reverse=True)
    return results


def print_report(results: list[FunctionAnalysis], contract_name: str = "Unknown"):
    print(f"\n{'='*80}")
    print(f"GasLens — EIP-7976 Calldata Floor Analysis: {contract_name}")
    print(f"{'='*80}")
    print("WARNING: This tool estimates CALDATA costs only.")
    print("Real tx cost = 21,000 intrinsic + execution_gas + calldata_cost")
    print("-" * 80)
    
    for r in results:
        if r.is_floor_dominated_post and not r.is_floor_dominated_pre:
            status = "🚨 NEWLY FLOOR-DOMINATED"
        elif r.is_floor_dominated_post and r.is_floor_dominated_pre:
            status = "⚠️ STILL FLOOR-DOMINATED"
        elif not r.is_floor_dominated_post and r.is_floor_dominated_pre:
            status = "✅ NO LONGER FLOOR-DOMINATED"
        else:
            status = "ℹ️ NEVER FLOOR-DOMINATED"
        
        print(f"\n{status}  {r.function_name} ({r.selector})")
        print(f"  Calldata: {r.calldata_bytes} bytes → {r.tokens} tokens")
        print(f"  Floor cost: {r.floor_pre:,} gas → {r.floor_post:,} gas (+{((r.floor_post-r.floor_pre)/r.floor_pre*100):.0f}%)")
        print(f"  Headroom: {r.headroom_pre:,} → {r.headroom_post:,} execution gas before not floor-dominated")
    
    newly = sum(1 for r in results if r.is_floor_dominated_post and not r.is_floor_dominated_pre)
    print(f"\n{'='*80}")
    print(f"SUMMARY: {len(results)} functions | {newly} newly floor-dominated post-Glamsterdam")


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
    
    results = analyze_abi(abi)
    print_report(results, args.name)


if __name__ == "__main__":
    main()
