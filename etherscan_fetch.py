#!/usr/bin/env python3
"""
Fetch ABI from Etherscan API
"""

import json
import urllib.request
import sys


ETHERSCAN_API_URL = "https://api.etherscan.io/api"


def fetch_abi(address: str, api_key: str) -> list:
    """Fetch contract ABI from Etherscan."""
    url = f"{ETHERSCAN_API_URL}?module=contract&action=getabi&address={address}&apikey={api_key}"
    
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode())
            
            if data.get("status") != "1":
                print(f"Error: {data.get('result', 'Unknown error')}", file=sys.stderr)
                return []
            
            abi_str = data.get("result", "[]")
            return json.loads(abi_str)
            
    except Exception as e:
        print(f"Fetch failed: {e}", file=sys.stderr)
        return []


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 etherscan_fetch.py <contract_address> <api_key>")
        print("Example: python3 etherscan_fetch.py 0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D YOUR_API_KEY")
        sys.exit(1)
    
    address = sys.argv[1]
    api_key = sys.argv[2]
    
    abi = fetch_abi(address, api_key)
    if abi:
        print(json.dumps(abi, indent=2))
        print(f"\nFetched {len(abi)} ABI items", file=sys.stderr)
    else:
        sys.exit(1)
