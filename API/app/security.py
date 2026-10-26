import hashlib
import hmac
import secrets

# scrypt 參數：每次驗證約 50 ms，讓暴力猜密碼變慢
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    """回傳 `scrypt$N$r$p$salt$hash`（salt、hash 用 hex），參數跟著存，之後調整也能驗舊密碼"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    _, n, r, p, salt, digest = stored.split("$")
    actual = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p)
    )
    return hmac.compare_digest(actual, bytes.fromhex(digest))


# 學號不存在時也跑一次 scrypt，讓回應時間跟密碼錯誤一樣，避免被拿來試出哪些學號存在
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe())


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
