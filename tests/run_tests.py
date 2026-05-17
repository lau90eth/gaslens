#!/usr/bin/env python3
"""Smoke tests for gaslens v0.4."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GASLENS = ROOT / "gaslens.py"


def run(abi_path: str, name: str = "test", array_len_hint: int = 2) -> dict:
    proc = subprocess.run(
        [sys.executable, str(GASLENS),
         "--abi", abi_path, "--name", name, "--format", "json",
         "--array-len-hint", str(array_len_hint)],
        capture_output=True, text=True, cwd=ROOT,
    )
    if proc.returncode != 0:
        raise SystemExit(f"gaslens failed: {proc.stderr}")
    return json.loads(proc.stdout)


def expect(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{suffix}")
    return ok


def find_fn(rep: dict, name: str) -> dict:
    for f in rep["functions"]:
        if f["name"] == name:
            return f
    raise KeyError(name)


def main() -> int:
    print("gaslens v0.4 smoke tests\n")
    all_ok = True

    print("test_uniswap_v2_sizing")
    rep = run("fixtures/uniswap_v2_router_abi.json", "Uniswap V2")
    swap = find_fn(rep, "swapExactTokensForETH")
    all_ok &= expect("swap calldata = 260 B", swap["calldata_size_bytes"] == 260,
                     f"got {swap['calldata_size_bytes']}")
    all_ok &= expect("tokens = 650", swap["tokens"] == 650, f"got {swap['tokens']}")
    all_ok &= expect("floor_pre = 6,500", swap["floor_pre"] == 6500)
    all_ok &= expect("floor_post = 10,400", swap["floor_post"] == 10400)
    all_ok &= expect("headroom_post = 7,800", swap["headroom_post"] == 7800)
    all_ok &= expect("swap NOT marked LIKELY",
                     swap["verdict"] != "LIKELY_FLOOR_DOMINATED",
                     f"got {swap['verdict']}")

    print("\ntest_erc20")
    rep = run("fixtures/erc20_abi.json", "ERC-20")
    transfer = find_fn(rep, "transfer")
    all_ok &= expect("transfer calldata = 68 B", transfer["calldata_size_bytes"] == 68)
    all_ok &= expect("transfer in safe range",
                     transfer["verdict"] in ("VIEW_OR_PURE_RISK_ONLY", "NEVER_FLOOR_DOMINATED"),
                     f"got {transfer['verdict']}")

    print("\ntest_l2_batcher")
    rep = run("fixtures/l2_batcher_abi.json", "L2 Batcher", array_len_hint=4)
    submit = find_fn(rep, "submitBatch")
    all_ok &= expect("submitBatch verdict = LIKELY",
                     submit["verdict"] == "LIKELY_FLOOR_DOMINATED",
                     f"got {submit['verdict']}")
    all_ok &= expect("submitBatch headroom > 20k", submit["headroom_post"] > 20_000,
                     f"got {submit['headroom_post']}")

    print("\ntest_glamlib_constants")
    all_ok &= expect("pre floor = 10 (from glamlib)",
                     rep["spec"]["pre"]["floor_per_token"] == 10)
    all_ok &= expect("post floor = 16 (from glamlib v0.1.1)",
                     rep["spec"]["post"]["floor_per_token"] == 16)

    print("\n" + ("ALL TESTS PASSED" if all_ok else "FAILURES PRESENT"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
