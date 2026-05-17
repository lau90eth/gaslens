# GasLens

> EIP-7976 Calldata Floor Analyzer. Honest about what it measures.

## What it does

Analyzes Ethereum contract ABIs to determine if functions are **calldata floor-dominated** under EIP-7976 (Glamsterdam).

- **Floor-dominated**: when calldata cost exceeds execution gas
- **Impact**: functions with low computation and large calldata see disproportionate cost increases

## What it does NOT do

- Does NOT estimate execution gas (use revm, Foundry, or Tenderly)
- Does NOT estimate state creation costs (use glamcheck or manual analysis)
- Does NOT predict exact transaction costs

## Install

    git clone https://github.com/lau90eth/gaslens
    cd gaslens
    git clone https://github.com/lau90eth/glamlib.git glamlib
    python3 gaslens.py --help

## Usage

    # Analyze contract from Etherscan
    python3 gaslens.py --etherscan 0xADDRESS --api-key YOUR_KEY --name "Contract Name"

    # Analyze local ABI
    python3 gaslens.py --abi contract_abi.json --name "Contract Name"

## Example

    python3 gaslens.py --etherscan 0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D --api-key YOUR_KEY --name "Uniswap V2 Router"

Output: calldata bytes, tokens, floor cost pre/post, headroom analysis.

## Understanding Output

| Status | Meaning |
|---|---|
|  | Function has enough execution gas that calldata floor never applies |
|  | Post-Glamsterdam, this function becomes floor-dominated |
|  | Was floor-dominated before, still is after |
|  | Was floor-dominated before, not anymore |

## Dependencies

- [glamlib](https://github.com/lau90eth/glamlib) — EIP constants shared with glamcheck

## License

MIT
