from src.config.settings import settings

print(f"jwt_secret len:            {len(settings.jwt_secret)}  (default 'change-me' has len 9)")
print(f"master_encryption_key len: {len(settings.master_encryption_key)}")
print(f"polygon_rpc_url:           {settings.polygon_rpc_url}")
print(f"mode:                      {settings.mode}")
print(f"live_trading_enabled:      {settings.live_trading_enabled}")
