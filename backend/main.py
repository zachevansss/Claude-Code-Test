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

import os

import uvicorn

from src.api.app import create_app

app = create_app()

if __name__ == "__main__":
    # Hot-reload is for local dev only. On the VPS it causes watchfiles to
    # constantly fire on log/DB writes, which thrashes the worker and stalls
    # the bot tick loop. Opt in explicitly via `DEV=1 python main.py` if you
    # want it locally. Production / systemd leaves it off.
    reload = os.environ.get("DEV", "").lower() in ("1", "true", "yes")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=reload)
