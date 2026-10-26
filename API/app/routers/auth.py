import time
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field

from app.dependencies import SESSION_COOKIE, CurrentStudent, Db
from app.docs import NO_STUDENT, UNAUTHORIZED, error_response
from app.security import DUMMY_PASSWORD_HASH, hash_token, new_session_token, verify_password

TAG_METADATA = {
    "name": "登入",
    "description": "用學號和密碼登入，之後瀏覽器帶著 `session` cookie，其他 API 就知道你是誰。"
    "密碼由管理員用 `python -m app.manage passwd` 設定，沒有開放註冊。",
}
router = APIRouter(prefix="/auth", tags=[TAG_METADATA["name"]])

SESSION_TTL = 8 * 60 * 60  # 8 小時


class LoginRequest(BaseModel):
    student_id: str = Field(
        pattern=r"^[A-Za-z0-9]{1,20}$", description="學號，不分大小寫", examples=["B11012345"]
    )
    password: str = Field(min_length=1, max_length=128, description="密碼")


class Student(BaseModel):
    """目前登入的學生"""

    student_id: str = Field(description="學號（大寫）", examples=["B11012345"])
    name: str = Field(description="姓名", examples=["王小明"])


@router.post(
    "/login",
    response_model=Student,
    summary="登入",
    response_description="登入成功，回傳學生資料並設定 `session` cookie",
    responses={401: error_response("學號或密碼錯誤", "學號或密碼錯誤")},
)
def login(body: LoginRequest, db: Db, response: Response):
    """學號或密碼錯誤都回同一個訊息，不透露是哪一個錯。"""
    student_id = body.student_id.upper()
    row = db.execute(
        "SELECT student_id, name, password_hash FROM accounts JOIN students USING (student_id)"
        " WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    password_ok = verify_password(body.password, row["password_hash"] if row else DUMMY_PASSWORD_HASH)
    if row is None or not password_ok:
        raise HTTPException(status_code=401, detail="學號或密碼錯誤")

    token = new_session_token()
    now = int(time.time())
    db.execute("DELETE FROM login_sessions WHERE expires_at <= ?", (now,))  # 順便清掉過期的
    db.execute(
        "INSERT INTO login_sessions (token_hash, student_id, expires_at) VALUES (?, ?, ?)",
        (hash_token(token), student_id, now + SESSION_TTL),
    )
    db.commit()
    # 目前是區網 HTTP，所以沒有 secure；改成 HTTPS 後要加上 secure=True
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax")
    return {"student_id": row["student_id"], "name": row["name"]}


@router.post("/logout", status_code=204, summary="登出")
def logout(
    db: Db,
    response: Response,
    session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
):
    """刪掉這次登入的 session；沒登入也回 `204`。"""
    if session:
        db.execute("DELETE FROM login_sessions WHERE token_hash = ?", (hash_token(session),))
        db.commit()
    response.delete_cookie(SESSION_COOKIE)


@router.get(
    "/me", response_model=Student, summary="目前的學生", responses={**UNAUTHORIZED, **NO_STUDENT}
)
def me(student: CurrentStudent):
    """登入的學生，或 `X-Student-ID` 指定的學生。"""
    return {"student_id": student["student_id"], "name": student["name"]}
