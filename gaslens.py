#!/usr/bin/env python3
"""
GasLens v0.4 — Calldata Floor Impact Analyzer for Glamsterdam (EIP-7976).

For each function in an ABI:
  - estimates calldata size with proper ABI encoding (dynamic types included)
  - reports the headroom: execution gas needed to NOT be floor-dominated
  - classifies into 4 verdicts: NEVER / VIEW_OR_PURE_RISK / POSSIBLY / LIKELY

Spec:
  - EIP-7623: https://eips.ethereum.org/EIPS/eip-7623
  - EIP-7976: https://eips.ethereum.org/EIPS/eip-7976

License: MIT
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field

from glamlib.eips import EIP_7976_FLOOR_PRE, EIP_7976_FLOOR_POST

STANDARD_TOKEN_COST = 4
TX_INTRINSIC = 21_000
EIP_7623_URL = "https://eips.ethereum.org/EIPS/eip-7623"
EIP_7976_URL = "https://eips.ethereum.org/EIPS/eip-7976"


def _is_dynamic(t: str) -> bool:
    t = t.strip()
    if t in ("bytes", "string"):
        return True
    if t.endswith("[]"):
        return True
    if "[" in t and t.endswith("]"):
        inner = t[: t.rfind("[")]
        return _is_dynamic(inner)
    return False


def _abi_static_size(t: str, components: list[dict] | None = None) -> int:
    t = t.strip()
    if t == "bytes":
        raise ValueError("dynamic type in static sizing")
    if t.startswith(("uint", "int", "bytes", "address", "bool", "function", "fixed", "ufixed")):
        return 32
    if t == "tuple":
        if not components:
            return 32
        total = 0
        for c in components:
            ct = c.get("type", "")
            if _is_dynamic(ct):
                raise ValueError("dynamic tuple in static sizing")
            total += _abi_static_size(ct, c.get("components"))
        return total
    if "[" in t and t.endswith("]"):
        inner = t[: t.rfind("[")]
        k_str = t[t.rfind("[") + 1 : -1]
        if not k_str.isdigit():
            raise ValueError("dynamic array in static sizing")
        return int(k_str) * _abi_static_size(inner, components)
    return 32


def _abi_dynamic_size(t: str, components: list[dict] | None = None,
                      array_len_hint: int = 2) -> int:
    t = t.strip()
    if t in ("bytes", "string"):
        return 32 + 32 + 64
    if t.endswith("[]"):
        inner = t[:-2]
        per = (_abi_static_size(inner, components) if not _is_dynamic(inner)
               else _abi_dynamic_size(inner, components, array_len_hint))
        return 32 + 32 + array_len_hint * per
    if "[" in t and t.endswith("]"):
        inner = t[: t.rfind("[")]
        k_str = t[t.rfind("[") + 1 : -1]
        k = int(k_str) if k_str.isdigit() else array_len_hint
        per = _abi_dynamic_size(inner, components, array_len_hint)
        return 32 + 32 + k * per
    if t == "tuple":
        total = 32 + 32
        for c in components or []:
            ct = c.get("type", "")
            total += (_abi_dynamic_size(ct, c.get("components"), array_len_hint)
                      if _is_dynamic(ct) else _abi_static_size(ct, c.get("components")))
        return total
    return 32


def function_calldata_size(function_abi: dict, array_len_hint: int = 2) -> int:
    size = 4
    for inp in function_abi.get("inputs", []) or []:
        t = inp.get("type", "")
        comps = inp.get("components")
        size += (_abi_dynamic_size(t, comps, array_len_hint)
                 if _is_dynamic(t) else _abi_static_size(t, comps))
    return size


def estimate_byte_split(total_bytes: int, nonzero_fraction: float = 0.5) -> tuple[int, int]:
    nonzero = int(round(total_bytes * nonzero_fraction))
    return total_bytes - nonzero, nonzero


def count_tokens(zero_bytes: int, nonzero_bytes: int) -> int:
    """EIP-7623: tokens = zero + nonzero * 4"""
    return zero_bytes + nonzero_bytes * 4


@dataclass
class FunctionReport:
    name: str
    selector: str
    signature: str
    calldata_size_bytes: int
    zero_bytes: int
    nonzero_bytes: int
    tokens: int
    standard_calldata_gas: int
    floor_pre: int
    floor_post: int
    headroom_pre: int
    headroom_post: int
    verdict: str
    eip: str = "EIP-7976"
    notes: list[str] = field(default_factory=list)


def function_signature(function_abi: dict) -> str:
    inputs = function_abi.get("inputs", []) or []
    types = [inp.get("type", "") for inp in inputs]
    return f"{function_abi.get('name', '')}({','.join(types)})"


def compute_function(abi: dict, nonzero_fraction: float = 0.5,
                     array_len_hint: int = 2) -> FunctionReport:
    name = abi.get("name", "unknown")
    sig = function_signature(abi)
    selector = abi.get("selector", "0x????????")

    cd_size = function_calldata_size(abi, array_len_hint=array_len_hint)
    zero, nonzero = estimate_byte_split(cd_size, nonzero_fraction)
    tokens = count_tokens(zero, nonzero)

    standard_cd = STANDARD_TOKEN_COST * tokens
    floor_pre = EIP_7976_FLOOR_PRE * tokens
    floor_post = EIP_7976_FLOOR_POST * tokens

    headroom_pre = max(0, floor_pre - standard_cd)
    headroom_post = max(0, floor_post - standard_cd)

    notes = []
    if headroom_post == 0:
        verdict = "NEVER_FLOOR_DOMINATED"
    elif headroom_post < 5_000:
        verdict = "VIEW_OR_PURE_RISK_ONLY"
        notes.append("Only view/pure or extremely thin functions could pay the floor.")
    elif headroom_post < 20_000:
        verdict = "POSSIBLY_FLOOR_DOMINATED"
        notes.append("Light state work (single warm SSTORE) may approach the floor.")
    else:
        verdict = "LIKELY_FLOOR_DOMINATED"
        notes.append("Headroom exceeds typical SSTORE-write cost. Very likely to pay floor.")

    if headroom_pre > 0 and headroom_post > headroom_pre:
        notes.append(f"Headroom grows by {headroom_post - headroom_pre:,} gas under EIP-7976.")

    return FunctionReport(
        name=name, selector=selector, signature=sig,
        calldata_size_bytes=cd_size, zero_bytes=zero, nonzero_bytes=nonzero,
        tokens=tokens, standard_calldata_gas=standard_cd,
        floor_pre=floor_pre, floor_post=floor_post,
        headroom_pre=headroom_pre, headroom_post=headroom_post,
        verdict=verdict, notes=notes,
    )


def analyze_abi(abi: list[dict], nonzero_fraction: float = 0.5,
                array_len_hint: int = 2) -> list[FunctionReport]:
    out = []
    for item in abi:
        if item.get("type") != "function":
            continue
        try:
            out.append(compute_function(item, nonzero_fraction, array_len_hint))
        except Exception as e:
            print(f"warning: skipping {item.get('name', '?')}: {e}", file=sys.stderr)
    out.sort(key=lambda r: r.headroom_post, reverse=True)
    return out


def fetch_abi_etherscan(address: str, api_key: str, chain_id: int = 1) -> list[dict]:
    params = {
        "chainid": str(chain_id), "module": "contract", "action": "getabi",
        "address": address, "apikey": api_key,
    }
    url = "https://api.etherscan.io/v2/api?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=30) as resp:
        payload = json.loads(resp.read().decode())
    if str(payload.get("status")) != "1":
        raise RuntimeError(f"Etherscan: {payload.get('result')}")
    return json.loads(payload["result"])


def format_text(reports: list[FunctionReport], name: str) -> str:
    lines = ["=" * 78,
             "GasLens v0.4 - Calldata Floor Impact Analysis",
             f"Contract: {name}",
             "Spec:     EIP-7623 (pre) -> EIP-7976 (post-Glamsterdam)",
             "=" * 78]
    if not reports:
        lines.append("No function entries in ABI.")
        return "\n".join(lines)

    counts: dict[str, int] = {}
    for r in reports:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
        lines.append("")
        lines.append(f"[{r.verdict}]  {r.signature}")
        lines.append(f"  calldata: {r.calldata_size_bytes:>4} B  "
                     f"({r.zero_bytes} zero + {r.nonzero_bytes} nonzero, {r.tokens} tokens)")
        lines.append(f"  standard cost: {r.standard_calldata_gas:>6,} gas   "
                     f"floor pre: {r.floor_pre:>6,}   floor post: {r.floor_post:>6,}")
        lines.append(f"  headroom_pre:  {r.headroom_pre:>6,} gas   "
                     f"headroom_post: {r.headroom_post:>6,} gas")
        for note in r.notes:
            lines.append(f"  -> {note}")

    lines.append("")
    lines.append("-" * 78)
    lines.append("SUMMARY")
    for v, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {v:<28} {n} function(s)")
    return "\n".join(lines)


def format_json(reports: list[FunctionReport], name: str) -> str:
    return json.dumps({
        "schema_version": "0.4",
        "tool": "gaslens",
        "contract": name,
        "spec": {
            "pre": {"eip": "EIP-7623", "url": EIP_7623_URL, "floor_per_token": EIP_7976_FLOOR_PRE},
            "post": {"eip": "EIP-7976", "url": EIP_7976_URL, "floor_per_token": EIP_7976_FLOOR_POST},
            "standard_token_cost": STANDARD_TOKEN_COST,
        },
        "function_count": len(reports),
        "functions": [asdict(r) for r in reports],
    }, indent=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gaslens")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--abi", help="Local ABI JSON file")
    src.add_argument("--etherscan", help="Contract address")
    ap.add_argument("--api-key", help="Etherscan API key (or env ETHERSCAN_API_KEY)")
    ap.add_argument("--chain-id", type=int, default=1)
    ap.add_argument("--name", default="(unnamed contract)")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--nonzero-fraction", type=float, default=0.5)
    ap.add_argument("--array-len-hint", type=int, default=2)
    args = ap.parse_args(argv)

    if args.abi:
        with open(args.abi) as f:
            abi = json.load(f)
    else:
        key = args.api_key or os.environ.get("ETHERSCAN_API_KEY")
        if not key:
            print("error: --api-key or ETHERSCAN_API_KEY required", file=sys.stderr)
            return 2
        abi = fetch_abi_etherscan(args.etherscan, key, args.chain_id)

    reports = analyze_abi(abi, args.nonzero_fraction, args.array_len_hint)
    print(format_json(reports, args.name) if args.format == "json"
          else format_text(reports, args.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
