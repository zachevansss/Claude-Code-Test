from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from src.api.schemas import LoginResponse, SignupRequest, UserOut
from src.auth.jwt import create_access_token
from src.auth.security import hash_password, verify_password
from src.database.session import get_db
from src.models import User, UserSettings
from src.utils.logging import get_logger
from src.wallet.crypto import CryptoError
from src.wallet.manager import WalletManager

router = APIRouter()
log = get_logger("AUTH")


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def signup(req: SignupRequest, db: Session = Depends(get_db)) -> UserOut:
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=req.email, password_hash=hash_password(req.password))
    user.settings = UserSettings()
    db.add(user)
    db.flush()  # assigns user.id without committing yet

    try:
        WalletManager.create_for_user(user.id, db)
    except CryptoError as e:
        db.rollback()
        # Surface the actionable hint about MASTER_ENCRYPTION_KEY rather than 500'ing.
        raise HTTPException(status_code=503, detail=str(e))

    db.commit()
    db.refresh(user)
    log.info("new user signed up: %s (managed wallet provisioned)", user.email)
    return user


@router.post("/login", response_model=LoginResponse)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> LoginResponse:
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user.id)
    log.info("user logged in: %s", user.email)
    return LoginResponse(access_token=token)
