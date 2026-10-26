"""管理學生資料、登入密碼和獎懲紀錄（在伺服器這台電腦上執行，沒有對外的 API）

    python -m app.manage add B11012345 王小明 --gender 男 --birth-date 2005-03-14 \\
        --department 資訊工程學系 --grade 3 --admission 個人申請   # 新增學生
    python -m app.manage passwd B11012345         # 設定或重設密碼（設了才能登入），順便登出所有裝置
    python -m app.manage list                     # 列出所有學生
    python -m app.manage remove B11012345         # 刪除學生，連同帳號和獎懲紀錄（通行證申請紀錄會保留）
    python -m app.manage record B11012345 嘉獎 --times 2 --reason 協助系學會活動   # 登記獎懲
    python -m app.manage seed                     # 隨機產生 20 位學生和 20 筆獎懲紀錄（假資料）

add 的國籍預設中華民國、身分別預設一般生，可以用 --nationality、--identity 指定。
不是在終端機執行時（例如用管線餵資料），密碼改從標準輸入讀一行。
"""

import argparse
import getpass
import re
import sqlite3
import sys
from contextlib import closing
from datetime import date

from app.database import connect, init_db, now_iso
from app.enums import AdmissionChannel, Gender, Identity, RewardKind
from app.security import MIN_PASSWORD_LENGTH, hash_password
from app.seed import seed

STUDENT_ID = re.compile(r"^[A-Za-z0-9]{1,20}$")


def student_id_arg(value: str) -> str:
    if not STUDENT_ID.match(value):
        raise argparse.ArgumentTypeError("學號要是英數字 1–20 碼")
    return value.upper()


def text_arg(label: str, max_length: int):
    def parse(value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= max_length:
            raise argparse.ArgumentTypeError(f"{label}要 1–{max_length} 個字")
        return value

    return parse


def date_arg(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError("日期格式是 YYYY-MM-DD") from None


def values(enum) -> list[str]:
    return [member.value for member in enum]


def read_password() -> str:
    if sys.stdin.isatty():
        password = getpass.getpass("密碼：")
        if getpass.getpass("再輸入一次：") != password:
            sys.exit("兩次密碼不一樣")
    else:
        password = sys.stdin.readline().rstrip("\r\n")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"密碼至少要 {MIN_PASSWORD_LENGTH} 個字元")
    return password


def require_student(db: sqlite3.Connection, student_id: str) -> None:
    if not db.execute("SELECT 1 FROM students WHERE student_id = ?", (student_id,)).fetchone():
        sys.exit(f"找不到 {student_id}，請先用 add 新增")


def add(db: sqlite3.Connection, args: argparse.Namespace) -> None:
    if db.execute("SELECT 1 FROM students WHERE student_id = ?", (args.student_id,)).fetchone():
        sys.exit(f"{args.student_id} 已經存在")
    db.execute(
        "INSERT INTO students (student_id, name, gender, birth_date, department, grade,"
        " nationality, identity, admission_channel, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            args.student_id,
            args.name,
            args.gender,
            args.birth_date,
            args.department,
            args.grade,
            args.nationality,
            args.identity,
            args.admission,
            now_iso(),
        ),
    )
    print(f"已新增 {args.student_id} {args.name}；要能登入，請再用 passwd 設定密碼")


def passwd(db: sqlite3.Connection, args: argparse.Namespace) -> None:
    require_student(db, args.student_id)
    db.execute(
        "INSERT INTO accounts (student_id, password_hash, created_at) VALUES (?, ?, ?)"
        " ON CONFLICT (student_id) DO UPDATE SET password_hash = excluded.password_hash",
        (args.student_id, hash_password(read_password()), now_iso()),
    )
    db.execute("DELETE FROM login_sessions WHERE student_id = ?", (args.student_id,))
    print(f"已設定 {args.student_id} 的密碼，並登出所有裝置")


def list_students(db: sqlite3.Connection, args: argparse.Namespace) -> None:
    rows = db.execute(
        "SELECT student_id, name, department, grade, identity, accounts.student_id IS NOT NULL"
        " FROM students LEFT JOIN accounts USING (student_id) ORDER BY student_id"
    )
    for row in rows:
        login = "可登入" if row[5] else "未設密碼"
        print(f"{row[0]}\t{row[1]}\t{row[2]} {row[3]} 年級\t{row[4]}\t{login}")


def remove(db: sqlite3.Connection, args: argparse.Namespace) -> None:
    # 帳號、登入紀錄、獎懲紀錄由外鍵 ON DELETE CASCADE 一起刪掉
    cur = db.execute("DELETE FROM students WHERE student_id = ?", (args.student_id,))
    if cur.rowcount == 0:
        sys.exit(f"找不到 {args.student_id}")
    print(f"已刪除 {args.student_id}，連同帳號和獎懲紀錄（通行證申請紀錄保留）")


def record(db: sqlite3.Connection, args: argparse.Namespace) -> None:
    require_student(db, args.student_id)
    db.execute(
        "INSERT INTO rewards_punishments (student_id, record_date, kind, times, reason, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (args.student_id, args.date, args.kind, args.times, args.reason, now_iso()),
    )
    print(f"已登記 {args.student_id} {args.date} {args.kind} {args.times} 次：{args.reason}")


def seed_data(db: sqlite3.Connection, args: argparse.Namespace) -> None:
    if args.records and not args.students and not db.execute("SELECT 1 FROM students").fetchone():
        sys.exit("還沒有學生可以登記獎懲，請用 --students 一起產生")
    seed(db, args.students, args.records)
    print(f"已新增 {args.students} 位學生、{args.records} 筆獎懲紀錄（假資料，學生沒有設定密碼）")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.manage", description="管理學生資料、登入密碼和獎懲紀錄"
    )
    commands = parser.add_subparsers(required=True)

    p = commands.add_parser("add", help="新增學生")
    p.add_argument("student_id", type=student_id_arg)
    p.add_argument("name", type=text_arg("姓名", 50))
    p.add_argument("--gender", required=True, choices=values(Gender), help="性別")
    p.add_argument("--birth-date", required=True, type=date_arg, help="出生日期 YYYY-MM-DD")
    p.add_argument("--department", required=True, type=text_arg("系所", 50), help="系所")
    p.add_argument("--grade", required=True, type=int, choices=range(1, 8), help="年級 1–7")
    p.add_argument("--nationality", default="中華民國", type=text_arg("國籍", 50), help="國籍")
    p.add_argument("--identity", default=Identity.general.value, choices=values(Identity), help="身分別")
    p.add_argument("--admission", required=True, choices=values(AdmissionChannel), help="入學管道")
    p.set_defaults(handler=add)

    p = commands.add_parser("passwd", help="設定或重設密碼")
    p.add_argument("student_id", type=student_id_arg)
    p.set_defaults(handler=passwd)

    p = commands.add_parser("list", help="列出所有學生")
    p.set_defaults(handler=list_students)

    p = commands.add_parser("remove", help="刪除學生")
    p.add_argument("student_id", type=student_id_arg)
    p.set_defaults(handler=remove)

    p = commands.add_parser("record", help="登記獎懲")
    p.add_argument("student_id", type=student_id_arg)
    p.add_argument("kind", choices=values(RewardKind), help="獎懲類別")
    p.add_argument("--times", type=int, default=1, choices=range(1, 4), help="次數 1–3，預設 1")
    p.add_argument("--date", type=date_arg, default=date.today().isoformat(), help="日期，預設今天")
    p.add_argument("--reason", required=True, type=text_arg("事由", 200), help="事由")
    p.set_defaults(handler=record)

    p = commands.add_parser("seed", help="隨機產生測試用的學生和獎懲紀錄")
    for option, label in (("--students", "學生人數"), ("--records", "獎懲紀錄筆數")):
        p.add_argument(
            option, type=int, default=20, choices=range(0, 1001), metavar="N", help=f"{label}，預設 20"
        )
    p.set_defaults(handler=seed_data)

    args = parser.parse_args()
    init_db()
    with closing(connect()) as db:
        args.handler(db, args)
        db.commit()


if __name__ == "__main__":
    main()
