# GasLens

> Simulate Ethereum gas costs pre/post hard fork upgrades.

## Install

    git clone https://github.com/lau90eth/gaslens
    cd gaslens
    python3 gaslens.py --help

## Usage

    # Analyze contract from Etherscan
    python3 gaslens.py --etherscan 0xADDRESS --api-key YOUR_KEY --name "Contract Name"

    # Analyze local ABI file
    python3 gaslens.py --abi contract_abi.json --name "Contract Name"

    # Custom ETH/gas price
    python3 gaslens.py --etherscan 0xADDRESS --api-key KEY --eth-price 2200 --gas-price 15

## Example

    python3 gaslens.py --etherscan 0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D --api-key YOUR_KEY --name "Uniswap V2 Router"

Output: gas cost pre/post Glamsterdam with USD conversion and risk levels.

## Features

- ABI parser for real contracts
- EIP-7976 calldata floor analysis
- EIP-8037 state creation cost analysis
- USD conversion with configurable ETH/gas price
- Etherscan API V2 integration
- Risk scoring: CRITICAL / HIGH / MODERATE / LOW
- Text + JSON output

## License

MIT
