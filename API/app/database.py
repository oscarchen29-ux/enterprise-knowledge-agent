import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "school.db"
UPLOAD_DIR = DATA_DIR / "uploads"

# 改了既有資料表的欄位就把這個數字加 1：舊的資料庫會拒絕啟動，提醒先備份再重建（見 README）
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    student_id        TEXT PRIMARY KEY,  -- 學號（大寫）
    name              TEXT NOT NULL,
    gender            TEXT NOT NULL,     -- 男 / 女
    birth_date        TEXT NOT NULL,     -- YYYY-MM-DD，年齡由 API 依今天的日期計算
    department        TEXT NOT NULL,     -- 系所，例如 資訊工程學系
    grade             INTEGER NOT NULL,  -- 年級
    nationality       TEXT NOT NULL,     -- 國籍，例如 中華民國
    identity          TEXT NOT NULL,     -- 身分別，可選值見 app/enums.py
    admission_channel TEXT NOT NULL,     -- 入學管道，可選值見 app/enums.py
    created_at        TEXT NOT NULL
);

-- 管理員設定過密碼的學生才有帳號，才能用 POST /auth/login 登入
CREATE TABLE IF NOT EXISTS accounts (
    student_id    TEXT PRIMARY KEY REFERENCES students ON DELETE CASCADE,
    password_hash TEXT NOT NULL,  -- 格式見 app/security.py
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_sessions (
    token_hash TEXT PRIMARY KEY,  -- cookie 裡 token 的 SHA-256，資料庫外洩也拿不到能用的 token
    student_id TEXT NOT NULL REFERENCES accounts ON DELETE CASCADE,
    expires_at INTEGER NOT NULL   -- Unix 時間（秒）
);

CREATE TABLE IF NOT EXISTS rewards_punishments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  TEXT NOT NULL REFERENCES students ON DELETE CASCADE,
    record_date TEXT NOT NULL,     -- YYYY-MM-DD
    kind        TEXT NOT NULL,     -- 嘉獎 / 小功 / 大功 / 警告 / 小過 / 大過
    times       INTEGER NOT NULL,  -- 次數，例如嘉獎兩次是 2
    reason      TEXT NOT NULL,     -- 事由
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rewards_punishments_student_id ON rewards_punishments (student_id);

-- 通行證不需要學生資料（X-Student-ID 可以是任何學號），刪除學生也要保留申請紀錄，所以不設外鍵
CREATE TABLE IF NOT EXISTS vehicle_passes (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id           TEXT NOT NULL,
    vehicle_type         TEXT NOT NULL,  -- car / motorcycle
    vehicle_model        TEXT NOT NULL,
    vehicle_registration TEXT NOT NULL,  -- 行照圖片檔名（放在 UPLOAD_DIR）
    driver_license       TEXT NOT NULL,  -- 駕照圖片檔名（放在 UPLOAD_DIR）
    created_at           TEXT NOT NULL
);
"""


def now_iso() -> str:
    """created_at 欄位用的時間：ISO 8601，含伺服器的時區"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    # 同步的 dependency 和 endpoint 可能在 threadpool 的不同 thread 執行，
    # 所以要關掉 check_same_thread；每個 request 都開自己的連線，不會共用
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite 預設不檢查外鍵，每個連線都要打開
    return conn


def init_db() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        has_tables = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'").fetchone()
        if has_tables and version != SCHEMA_VERSION:
            raise RuntimeError(
                f"{DB_PATH} 的資料表結構是舊版（第 {version} 版，程式要第 {SCHEMA_VERSION} 版），"
                "請關掉伺服器、備份後刪掉這個檔案，下次啟動會建立新的"
            )
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
