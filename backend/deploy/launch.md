# Live Launch — Step-by-Step

Follow these steps in order. Each box is one logical chunk; don't skip ahead.

---

## ☐ Step 0 — Before you start

Have these in front of you:
- A funded **Coinbase account** with at least $1,005 USDC (the $5 buffer covers Polygon network fees on the deposit; you'll send ~$1,000 to your wallet plus ~0.5 MATIC for gas)
- Your **existing Polymarket account credentials** — you'll export the private key from it
- Credit card for the **VPS provider** (~$5/month)
- An account at **Alchemy** or **Infura** for a free Polygon RPC API key

---

## ☐ Step 1 — Rent the VPS (~5 min)

**Recommended: Hetzner Cloud** (cheapest, fastest signup, EU regions).

1. Sign up at https://www.hetzner.com/cloud
2. Create a new project → "Add Server"
3. Settings:
   - **Location:** any non-US (Falkenstein/Nuremberg/Helsinki are fine)
   - **Image:** Ubuntu 22.04
   - **Type:** CX22 (€4.51/mo, 2 vCPU, 4GB RAM — plenty)
   - **SSH key:** add your public key (generate with `ssh-keygen -t ed25519` if you don't have one)
   - **Name:** `copytrade-bot`
4. Click "Create & Buy Now". Server boots in ~30 seconds.
5. Copy the IPv4 address. SSH in:
   ```
   ssh root@<your-vps-ip>
   ```

**Alternatives:** DigitalOcean ($6/mo), Vultr ($6/mo), Linode ($5/mo). Same idea — pick non-US region, Ubuntu 22.04.

---

## ☐ Step 2 — Verify VPS IP is non-US (~30 sec)

```bash
curl https://ipinfo.io/country
```

**Must NOT return `US`.** If it does, you picked a US region — destroy the server and create one in EU/Asia.

---

## ☐ Step 3 — Run the installer (~3 min)

Still on the VPS as root:

```bash
# Create a non-root user (better security than running as root)
adduser ubuntu --disabled-password --gecos ""
usermod -aG sudo ubuntu

# Copy your SSH key to the new user
mkdir -p /home/ubuntu/.ssh
cp ~/.ssh/authorized_keys /home/ubuntu/.ssh/
chown -R ubuntu:ubuntu /home/ubuntu/.ssh
chmod 700 /home/ubuntu/.ssh
chmod 600 /home/ubuntu/.ssh/authorized_keys

# Switch to that user
su - ubuntu
```

Now run the installer:

```bash
curl -L https://raw.githubusercontent.com/zachevansss/Claude-Code-Test/main/backend/deploy/install.sh | bash
```

This takes ~3 min. Installs Python 3.12, clones repo, sets up venv, installs deps.

---

## ☐ Step 4 — Get a Polygon RPC URL (~3 min)

1. Go to https://www.alchemy.com (or https://www.infura.io)
2. Sign up (free tier is plenty for one bot)
3. Create a new app → Polygon → Mainnet
4. Copy the HTTPS URL (looks like `https://polygon-mainnet.g.alchemy.com/v2/abc123...`)

---

## ☐ Step 5 — Generate master encryption key + JWT secret

On the VPS as `ubuntu`:

```bash
cd ~/copytrade/backend
./.venv/bin/python -m src.wallet.crypto generate
# → outputs a base64 string. Copy it.
```

```bash
./.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
# → outputs a long random string. Copy it.
```

**🔒 BACK UP THE MASTER KEY TO A PASSWORD MANAGER NOW.** Losing it = permanently losing access to your wallet.

---

## ☐ Step 6 — Fill in .env

```bash
nano ~/copytrade/backend/.env
```

Set these three lines:
```
MASTER_ENCRYPTION_KEY=<paste the master key>
JWT_SECRET=<paste the JWT secret>
POLYGON_RPC_URL=<paste your Alchemy URL>
```

Leave the rest of the file as-is. Save and exit (Ctrl+O, Enter, Ctrl+X in nano).

---

## ☐ Step 7 — Install systemd service (~1 min)

```bash
sudo cp ~/copytrade/backend/deploy/copytrade.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now copytrade
```

Verify it started:
```bash
sudo systemctl status copytrade
```
Should show `active (running)`. If it crashed, check `sudo journalctl -u copytrade -n 50`.

Tail the logs in real time:
```bash
sudo journalctl -u copytrade -f
```

You should see `[BOT_MANAGER] restarted 0 bots from DB state` and `[API] API ready in mode=paper`. Leave this terminal running so you can watch what happens.

---

## ☐ Step 8 — Sign up + import your existing Polymarket wallet (~3 min)

In a **second SSH session** to the same VPS (or just curl from your laptop targeting the VPS IP — the API is on port 8000):

```bash
# Sign up
curl -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"YOUR_STRONG_PASSWORD"}'

# Log in to get a token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -d 'username=you@example.com&password=YOUR_STRONG_PASSWORD' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "Token: $TOKEN"
```

Now export your private key from Polymarket:

1. Open Polymarket on a device with VPN connected to a non-US country
2. Settings → Wallet → Export Private Key
3. Confirm and copy the 0x-prefixed hex string

Import it into the bot:

```bash
curl -X POST http://localhost:8000/wallet/import \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"private_key":"0xPASTE_YOUR_KEY_HERE","replace_existing":true}'
```

Verify it's the right wallet (should show your existing Polymarket address + USDC balance):

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/wallet
```

---

## ☐ Step 9 — Fund the wallet from Coinbase (~10 min — depends on Coinbase confirmation)

In Coinbase:

1. Send **USDC** on the **Polygon (POL)** network
2. Destination: the wallet address `/wallet` returned in step 8
3. Amount: **whatever balance you want over the existing $1K** (your existing balance + new deposit = total bankroll)
4. Also send **0.5 MATIC** on Polygon for gas (if your wallet is light on MATIC). Coinbase has a small MATIC balance available — convert a few dollars worth.

Wait for confirmation (Polygon is fast, ~1-2 min). Verify on Polygonscan if you want.

---

## ☐ Step 10 — Run wallet setup (approvals) (~2 min)

```bash
curl -X POST http://localhost:8000/wallet/setup \
  -H "Authorization: Bearer $TOKEN"
```

Returns a list of actions (USDC and CTF approvals on both standard and NegRisk exchanges). Most will say `"status": "already"` because your existing Polymarket account already has these set from manual trading.

---

## ☐ Step 11 — Register the source wallet to copy

```bash
curl -X POST http://localhost:8000/wallets/add \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"address":"0x2005d16a84ceefa912d4e380cd32e7ff827875ea","label":"primary copy target"}'
```

---

## ☐ Step 12 — Apply final live config

```bash
curl -X POST http://localhost:8000/settings/risk \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "sizing_strategy":"mirror",
    "mirror_scale":0.075,
    "mirror_power":0.5,
    "min_trade_usd":2.00,
    "max_percent_per_trade":1.0,
    "max_exposure_per_market_pct":100.0,
    "max_total_leverage_pct":100.0,
    "daily_loss_cap_pct":10.0,
    "slippage_tolerance_pct":1.5
  }'
```

---

## ☐ Step 13 — Switch to live mode

Edit `.env`:
```bash
sed -i 's/^MODE=paper$/MODE=live/' ~/copytrade/backend/.env
sed -i 's/^LIVE_TRADING_ENABLED=False$/LIVE_TRADING_ENABLED=True/' ~/copytrade/backend/.env
```

Switch user setting + restart server:
```bash
curl -X POST http://localhost:8000/settings/mode \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"live"}'

sudo systemctl restart copytrade
```

In the journal-tail terminal you should see `[API] API ready in mode=live`.

---

## ☐ Step 14 — Start the bot

```bash
curl -X POST http://localhost:8000/bot/start \
  -H "Authorization: Bearer $TOKEN"
```

**Watch journalctl. You should see:**
- `[TRACKER] tracker initialized: seeded N activities`
- (5 sec later) `[TRACKER] emitting X new signal(s)` (when source does anything new)
- `[RISK] sized buy ...`
- `[EXECUTION] live-submit user=1 buy ... order_id=0x...`

**The first `[EXECUTION] live-submit` line is your first real trade.** Verify the order_id on Polymarket / Polygonscan.

---

## ☐ Step 15 — Monitor for the first hour

Keep the journalctl tail open. Watch for:
- ✅ Fills happening (small notional, $2–$10 each)
- ❌ Any `ERROR` or `Exception` lines — paste them to me
- ❌ `ExecutionRefused` — means a safety gate caught something; investigate

Open a third SSH session and watch the dashboard:
```bash
cd ~/copytrade/backend
./.venv/bin/python stats.py --watch
```

After 1 hour: if everything's running clean, you're done. Walk away. The bot runs 24/7 on the VPS now.

---

## Daily ops (after launch)

- **Check dashboard** once a day to confirm positive PnL trend
- **Manually redeem winning markets** via Polymarket UI (with VPN) every couple of days to recycle USDC into your wallet
- **Watch for crashes:** `sudo systemctl status copytrade` should always show `active (running)`. If it says `failed`, check journalctl
- **Daily backup runs automatically** if you set up the cron in Step 16 (optional, see backup-db.sh)

## Halting the bot

If something looks wrong:
```bash
curl -X POST http://localhost:8000/bot/stop -H "Authorization: Bearer $TOKEN"
# OR
sudo systemctl stop copytrade   # nukes the whole server
```

To re-enable:
```bash
sudo systemctl start copytrade
curl -X POST http://localhost:8000/bot/start -H "Authorization: Bearer $TOKEN"
```

## Scaling up as account grows

When your account doubles, bump `mirror_scale`:
```bash
curl -X POST http://localhost:8000/settings/risk \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mirror_scale":0.10}'
```

| Account | mirror_scale |
|---|---|
| $1K | 0.075 |
| $2K | 0.10 |
| $4K | 0.13 |
| $8K | 0.17 |
