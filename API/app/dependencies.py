import sqlite3
import time
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException

from app.database import get_db
from app.security import hash_token

SESSION_COOKIE = "session"

Db = Annotated[sqlite3.Connection, Depends(get_db)]


def get_student_id(
    db: Db,
    student_id: Annotated[
        str | None,
        Header(
            alias="X-Student-ID",
            pattern=r"^[A-Za-z0-9]{1,20}$",
            description="學號：英數字 1–20 碼，不分大小寫。不需要密碼，給程式和測試用；也可以改用登入 cookie",
            openapi_examples={"範例學號": {"value": "B11012345"}},
        ),
    ] = None,
    session: Annotated[
        str | None,
        Cookie(alias=SESSION_COOKIE, description="登入後由 `POST /auth/login` 設定的 cookie"),
    ] = None,
) -> str:
    # 這是封閉的測試環境，直接相信 header 帶來的學號：連得到 API 的人都能冒用任何學號（見 README）
    if student_id:
        return student_id.upper()

    # JOIN accounts 讓被刪掉的帳號立刻失效
    row = None
    if session:
        row = db.execute(
            "SELECT student_id FROM login_sessions JOIN accounts USING (student_id)"
            " WHERE token_hash = ? AND expires_at > ?",
            (hash_token(session), int(time.time())),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="請先登入")
    return row["student_id"]


StudentId = Annotated[str, Depends(get_student_id)]


def get_student(student_id: StudentId, db: Db) -> sqlite3.Row:
    # 用 X-Student-ID 時學號不一定有學生資料；通行證、獎懲紀錄只看學號，但姓名等基本資料要有這筆
    row = db.execute("SELECT * FROM students WHERE student_id = ?", (student_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到這個學號的學生資料")
    return row


CurrentStudent = Annotated[sqlite3.Row, Depends(get_student)]
