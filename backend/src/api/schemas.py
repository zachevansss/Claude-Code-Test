"""Pydantic schemas for the HTTP API."""
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# --- Auth ---
class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: EmailStr
    is_active: bool

    model_config = {"from_attributes": True}


# --- Wallets ---
class WalletAddRequest(BaseModel):
    address: str
    label: str | None = None


class WalletOut(BaseModel):
    id: int
    address: str
    label: str | None
    is_active: bool

    model_config = {"from_attributes": True}


# --- Settings ---
class RiskSettingsRequest(BaseModel):
    sizing_strategy: str | None = None  # "percent" | "fixed" | "mirror"
    sizing_percent: float | None = None
    sizing_fixed_usd: float | None = None
    mirror_scale: float | None = None        # multiplier on source notional (mirror only)
    min_trade_usd: float | None = None       # floor; signals below this are skipped (mirror only)
    max_percent_per_trade: float | None = None
    max_exposure_per_market_usd: float | None = None
    daily_loss_cap_usd: float | None = None
    slippage_tolerance_pct: float | None = None


class ModeRequest(BaseModel):
    mode: str  # "paper" | "live"


class SettingsOut(BaseModel):
    mode: str
    sizing_strategy: str
    sizing_percent: float
    sizing_fixed_usd: float
    mirror_scale: float
    min_trade_usd: float
    max_percent_per_trade: float
    max_exposure_per_market_usd: float
    daily_loss_cap_usd: float
    slippage_tolerance_pct: float
    paper_balance_usd: float

    model_config = {"from_attributes": True}


# --- Bot ---
class BotStatusOut(BaseModel):
    status: str
    last_started_at: datetime | None
    last_error: str | None

    model_config = {"from_attributes": True}


# --- Trades ---
class TradeOut(BaseModel):
    id: int
    source_wallet: str
    market_id: str
    outcome: str
    side: str
    price: float
    size: float
    notional_usd: float
    mode: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Managed wallet ---
class ManagedWalletOut(BaseModel):
    address: str
    usdc_balance: float | None = None
    matic_balance: float | None = None
    balance_error: str | None = None  # populated if RPC lookup failed


class ApprovalAction(BaseModel):
    contract: str       # e.g. "USDC->CTF Exchange", "CTF->NegRisk Exchange"
    spender: str        # checksummed address
    status: str         # "approved" | "already"
    tx: str | None      # tx hash if a transaction was sent, None if skipped


class WalletSetupOut(BaseModel):
    address: str
    matic_balance: float
    actions: list[ApprovalAction]


class WalletImportRequest(BaseModel):
    private_key: str
    # Required when overwriting an existing managed wallet. Refused if any live
    # trades already exist for the user (would orphan position state).
    replace_existing: bool = False


# --- Stats ---
class WalletStat(BaseModel):
    wallet: str
    trades: int
    notional_usd: float


class StatsOut(BaseModel):
    total_pnl_usd: float
    total_trades: int
    win_rate: float
    roi_pct: float
    by_wallet: list[WalletStat]
