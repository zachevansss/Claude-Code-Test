"""Backend entrypoint. `python main.py` starts uvicorn in dev mode."""
# Use the OS-native trust store for TLS verification when available. On some
# Windows + OneDrive + Microsoft Store Python setups, certifi's bundle is
# unreadable for chain validation even when intact on disk, breaking every
# HTTPS call (Alchemy RPC, Polymarket data API, etc.). truststore is a no-op
# on systems where certifi already works, so injecting unconditionally is safe.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import uvicorn

from src.api.app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
