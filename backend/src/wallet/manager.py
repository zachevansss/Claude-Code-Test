"""WalletManager — generate, load, sign with managed wallets.

Generation uses eth_account.Account.create() (CSPRNG-backed). Private key bytes
are immediately encrypted via Fernet and the plaintext is dropped from scope as
quickly as possible."""
from eth_account import Account
from eth_account.signers.local import LocalAccount
from sqlalchemy.orm import Session

from src.models import ManagedWallet
from src.utils.logging import get_logger
from src.wallet.crypto import decrypt, encrypt

log = get_logger("WALLET")


class WalletManager:
    """All methods are static — there is no per-instance state."""

    @staticmethod
    def create_for_user(user_id: int, db: Session) -> ManagedWallet:
        """Generate a fresh EOA, encrypt the private key, persist. Idempotent
        wrt the unique (user_id) constraint — caller should check first."""
        acct: LocalAccount = Account.create()
        encrypted = encrypt(acct.key)  # acct.key is bytes
        wallet = ManagedWallet(
            user_id=user_id,
            address=acct.address.lower(),
            encrypted_private_key=encrypted,
        )
        db.add(wallet)
        db.flush()
        log.info("created managed wallet for user=%s address=%s", user_id, wallet.address)
        return wallet

    @staticmethod
    def import_for_user(
        user_id: int,
        private_key_hex: str,
        db: Session,
        replace_existing: bool = False,
        proxy_address: str | None = None,
    ) -> ManagedWallet:
        """Import an existing EOA from a hex private key. By default refuses if
        a managed wallet already exists for this user — pass replace_existing=True
        to overwrite. Validation: Account.from_key raises on a malformed key.

        Never log the private key. Only the derived address is safe to log."""
        key_clean = private_key_hex.strip()
        if key_clean.startswith("0x") or key_clean.startswith("0X"):
            key_clean = key_clean[2:]
        if len(key_clean) != 64:
            raise ValueError("private key must be 32 bytes / 64 hex chars (with optional 0x prefix)")
        try:
            acct: LocalAccount = Account.from_key(bytes.fromhex(key_clean))
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"invalid private key: {e}") from e

        # Validate / normalize proxy address if provided
        proxy = None
        if proxy_address:
            p = proxy_address.strip().lower()
            if not p.startswith("0x") or len(p) != 42:
                raise ValueError(
                    "proxy_address must be a 0x-prefixed 42-character hex string"
                )
            proxy = p

        existing = (
            db.query(ManagedWallet).filter(ManagedWallet.user_id == user_id).first()
        )
        encrypted = encrypt(acct.key)
        if existing:
            if not replace_existing:
                raise ValueError(
                    "user already has a managed wallet — pass replace_existing=true to overwrite"
                )
            existing.address = acct.address.lower()
            existing.encrypted_private_key = encrypted
            existing.proxy_address = proxy
            db.flush()
            log.info(
                "replaced managed wallet for user=%s address=%s proxy=%s",
                user_id, existing.address, proxy or "(none)",
            )
            return existing

        wallet = ManagedWallet(
            user_id=user_id,
            address=acct.address.lower(),
            encrypted_private_key=encrypted,
            proxy_address=proxy,
        )
        db.add(wallet)
        db.flush()
        log.info(
            "imported managed wallet for user=%s address=%s proxy=%s",
            user_id, wallet.address, proxy or "(none)",
        )
        return wallet

    @staticmethod
    def get_or_create(user_id: int, db: Session) -> ManagedWallet:
        existing = (
            db.query(ManagedWallet).filter(ManagedWallet.user_id == user_id).first()
        )
        if existing:
            return existing
        wallet = WalletManager.create_for_user(user_id, db)
        db.commit()
        return wallet

    @staticmethod
    def get_signer(wallet: ManagedWallet) -> LocalAccount:
        """Decrypt the private key and return an eth_account LocalAccount.
        Caller is responsible for not leaking the returned object."""
        pk = decrypt(wallet.encrypted_private_key)
        return Account.from_key(pk)

    @staticmethod
    def get_private_key_hex(wallet: ManagedWallet) -> str:
        """Return the decrypted private key as 0x-prefixed hex. Used by py-clob-client."""
        pk = decrypt(wallet.encrypted_private_key)
        return "0x" + pk.hex()
