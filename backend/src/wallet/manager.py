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
