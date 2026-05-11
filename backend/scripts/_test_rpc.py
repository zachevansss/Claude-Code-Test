import sys
import httpx

url = sys.argv[1]
try:
    r = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}, timeout=10, verify=False)
    print(f"HTTP: {r.status_code}")
    print(f"Body: {r.text[:300]}")
    r2 = httpx.post(url, json={"jsonrpc": "2.0", "id": 2, "method": "eth_blockNumber", "params": []}, timeout=10, verify=False)
    print(f"\nblockNumber HTTP: {r2.status_code}")
    print(f"Body: {r2.text[:200]}")
except Exception as e:
    print(f"ERROR: {e}")
