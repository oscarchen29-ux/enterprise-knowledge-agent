"""管理介面的頁面（啟動方式見 admin/__main__.py）"""

import secrets
import sqlite3
import time
from datetime import date
from urllib.parse import urlsplit

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from admin.forms import NewStudentForm, RecordForm, StudentForm, parse
from app.database import UPLOAD_DIR, connect, init_db, now_iso
from app.enums import AdmissionChannel, Gender, Identity, RewardKind
from app.routers.students import age_on
from app.security import MIN_PASSWORD_LENGTH, hash_password
from app.seed import DEPARTMENTS, FOREIGN_NAMES, OVERSEAS_CHINESE_NATIONALITIES

app = Flask(__name__)
app.secret_key = secrets.token_bytes(32)  # 只用來暫存提示訊息，每次啟動換一把沒關係
# 沒有登入機制，只靠這兩道擋掉其他網站：Host 防 DNS rebinding，Origin（見 reject_cross_site）防跨站送出表單
app.config["TRUSTED_HOSTS"] = ["127.0.0.1", "localhost"]

MAX_PASSWORD_LENGTH = 128  # 跟 POST /auth/login 的上限一樣
REWARD_KINDS = [kind.value for kind in RewardKind if kind.category == "獎勵"]
IMAGE_KINDS = {"vehicle_registration": "行照", "driver_license": "駕照"}
VEHICLE_TYPES = {"car": "汽車", "motorcycle": "機車"}

init_db()  # 跟 API 一樣：第一次執行時建立資料表，舊版的資料庫會在這裡擋下


def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc: BaseException | None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@app.before_request
def reject_cross_site() -> None:
    origin = request.headers.get("Origin")
    if request.method == "POST" and origin and urlsplit(origin).netloc != request.host:
        abort(403)


@app.template_filter("category")
def category(kind: str) -> str:
    return RewardKind(kind).category


@app.context_processor
def choices() -> dict:
    return {
        "genders": [member.value for member in Gender],
        "identities": [member.value for member in Identity],
        "admission_channels": [member.value for member in AdmissionChannel],
        "reward_kinds": [member.value for member in RewardKind],
        "image_kinds": IMAGE_KINDS,
        "vehicle_types": VEHICLE_TYPES,
        "today": date.today().isoformat(),
    }


def safe_next(default: str) -> str:
    """表單帶來的 next（處理完要回到哪一頁），只接受站內路徑，免得被拿來轉址到別的網站"""
    target = request.values.get("next", "")
    if target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return default


def flash_errors(errors: list[str]) -> None:
    for error in errors:
        flash(error, "error")


def with_age(row: sqlite3.Row) -> dict:
    return {**dict(row), "age": age_on(date.fromisoformat(row["birth_date"]), date.today())}


def get_student_or_404(student_id: str) -> sqlite3.Row:
    row = db().execute("SELECT * FROM students WHERE student_id = ?", (student_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def get_record_or_404(record_id: int) -> sqlite3.Row:
    row = db().execute(
        "SELECT r.*, s.name FROM rewards_punishments r JOIN students s USING (student_id) WHERE r.id = ?",
        (record_id,),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def render_student(student: sqlite3.Row | None, form, status: int = 200):
    """新增和編輯學生共用一頁；編輯時多列出獎懲紀錄、帳號和通行證"""
    departments = [row[0] for row in db().execute("SELECT DISTINCT department FROM students")]
    context = {
        "student": student,
        "form": form,
        # 學系、國籍是自由填寫，列出常用的當作輸入提示
        "departments": dict.fromkeys([*DEPARTMENTS.values(), *departments]),
        "nationalities": dict.fromkeys(["中華民國", *OVERSEAS_CHINESE_NATIONALITIES, *FOREIGN_NAMES]),
        "min_password_length": MIN_PASSWORD_LENGTH,
        "max_password_length": MAX_PASSWORD_LENGTH,
    }
    if student:
        student_id = student["student_id"]
        context["age"] = with_age(student)["age"]
        context["records"] = db().execute(
            "SELECT * FROM rewards_punishments WHERE student_id = ? ORDER BY record_date DESC, id DESC",
            (student_id,),
        ).fetchall()
        context["account"] = db().execute(
            "SELECT created_at, (SELECT COUNT(*) FROM login_sessions WHERE student_id = ? AND expires_at > ?)"
            " AS sessions FROM accounts WHERE student_id = ?",
            (student_id, int(time.time()), student_id),
        ).fetchone()
        context["passes"] = db().execute(
            "SELECT * FROM vehicle_passes WHERE student_id = ? ORDER BY id DESC", (student_id,)
        ).fetchall()
    return render_template("student.html", **context), status


@app.get("/")
def index():
    return redirect(url_for("students"))


@app.get("/students")
def students():
    q = request.args.get("q", "").strip()
    marks = ", ".join("?" * len(REWARD_KINDS))
    rows = db().execute(
        "SELECT s.*, a.student_id IS NOT NULL AS has_account,"
        " COALESCE(c.rewards, 0) AS rewards, COALESCE(c.total - c.rewards, 0) AS punishments"
        " FROM students s"
        " LEFT JOIN accounts a ON a.student_id = s.student_id"
        " LEFT JOIN ("
        f"   SELECT student_id, SUM(kind IN ({marks})) AS rewards, COUNT(*) AS total"
        "    FROM rewards_punishments GROUP BY student_id"
        " ) c ON c.student_id = s.student_id"
        " WHERE s.student_id LIKE ? OR s.name LIKE ? OR s.department LIKE ?"
        " ORDER BY s.student_id",
        (*REWARD_KINDS, *[f"%{q}%"] * 3),
    ).fetchall()
    return render_template("students.html", students=[with_age(row) for row in rows], q=q)


@app.route("/students/new", methods=["GET", "POST"])
def new_student():
    if request.method == "GET":
        return render_student(None, {"nationality": "中華民國", "identity": Identity.general.value, "grade": 1})

    form, errors = parse(NewStudentForm, request.form)
    if form:
        data = {**form.model_dump(mode="json"), "student_id": form.student_id.upper(), "created_at": now_iso()}
        if db().execute("SELECT 1 FROM students WHERE student_id = ?", (data["student_id"],)).fetchone():
            errors = [f"學號 {data['student_id']} 已經存在"]
        else:
            db().execute(
                "INSERT INTO students (student_id, name, gender, birth_date, department, grade,"
                " nationality, identity, admission_channel, created_at)"
                " VALUES (:student_id, :name, :gender, :birth_date, :department, :grade,"
                " :nationality, :identity, :admission_channel, :created_at)",
                data,
            )
            db().commit()
            flash(f"已新增 {data['student_id']} {data['name']}")
            return redirect(url_for("student", student_id=data["student_id"]))
    flash_errors(errors)
    return render_student(None, request.form, 400)


@app.route("/students/<student_id>", methods=["GET", "POST"])
def student(student_id: str):
    row = get_student_or_404(student_id)
    if request.method == "GET":
        return render_student(row, row)

    form, errors = parse(StudentForm, request.form)
    if form is None:
        flash_errors(errors)
        return render_student(row, request.form, 400)
    db().execute(
        "UPDATE students SET name = :name, gender = :gender, birth_date = :birth_date,"
        " department = :department, grade = :grade, nationality = :nationality,"
        " identity = :identity, admission_channel = :admission_channel WHERE student_id = :student_id",
        {**form.model_dump(mode="json"), "student_id": student_id},
    )
    db().commit()
    flash("已儲存基本資料")
    return redirect(url_for("student", student_id=student_id))


@app.post("/students/<student_id>/delete")
def delete_student(student_id: str):
    # 帳號、登入紀錄、獎懲紀錄由外鍵 ON DELETE CASCADE 一起刪掉
    if db().execute("DELETE FROM students WHERE student_id = ?", (student_id,)).rowcount == 0:
        abort(404)
    db().commit()
    flash(f"已刪除 {student_id}，連同帳號和獎懲紀錄（通行證申請紀錄保留）")
    return redirect(url_for("students"))


@app.post("/students/<student_id>/password")
def set_password(student_id: str):
    get_student_or_404(student_id)
    password = request.form.get("password", "")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        flash(f"密碼要 {MIN_PASSWORD_LENGTH}–{MAX_PASSWORD_LENGTH} 個字元", "error")
    elif password != request.form.get("password_again"):
        flash("兩次密碼不一樣", "error")
    else:
        db().execute(
            "INSERT INTO accounts (student_id, password_hash, created_at) VALUES (?, ?, ?)"
            " ON CONFLICT (student_id) DO UPDATE SET password_hash = excluded.password_hash",
            (student_id, hash_password(password), now_iso()),
        )
        db().execute("DELETE FROM login_sessions WHERE student_id = ?", (student_id,))
        db().commit()
        flash("已設定密碼，並登出這位學生所有的裝置")
    return redirect(url_for("student", student_id=student_id) + "#account")


@app.post("/students/<student_id>/account/delete")
def delete_account(student_id: str):
    # 登入紀錄由外鍵一起刪掉，已登入的裝置立刻失效
    db().execute("DELETE FROM accounts WHERE student_id = ?", (student_id,))
    db().commit()
    flash("已刪除登入帳號，這位學生不能再用密碼登入")
    return redirect(url_for("student", student_id=student_id) + "#account")


@app.post("/students/<student_id>/records")
def add_record(student_id: str):
    get_student_or_404(student_id)
    form, errors = parse(RecordForm, request.form)
    if form:
        db().execute(
            "INSERT INTO rewards_punishments (student_id, record_date, kind, times, reason, created_at)"
            " VALUES (:student_id, :record_date, :kind, :times, :reason, :created_at)",
            {**form.model_dump(mode="json"), "student_id": student_id, "created_at": now_iso()},
        )
        db().commit()
        flash(f"已新增 {form.kind.value} {form.times} 次")
    flash_errors(errors)
    return redirect(url_for("student", student_id=student_id) + "#records")


@app.get("/records")
def records():
    selected = request.args.get("category", "")
    marks = ", ".join("?" * len(REWARD_KINDS))
    where = {"獎勵": f"WHERE r.kind IN ({marks})", "懲處": f"WHERE r.kind NOT IN ({marks})"}.get(selected)
    rows = db().execute(
        "SELECT r.*, s.name FROM rewards_punishments r JOIN students s USING (student_id)"
        f" {where or ''} ORDER BY r.record_date DESC, r.id DESC",
        REWARD_KINDS if where else (),
    ).fetchall()
    return render_template("records.html", records=rows, selected=selected if where else "")


@app.route("/records/<int:record_id>", methods=["GET", "POST"])
def record(record_id: int):
    row = get_record_or_404(record_id)
    back = safe_next(url_for("student", student_id=row["student_id"]) + "#records")
    if request.method == "GET":
        return render_template("record.html", record=row, form=row, back=back)

    form, errors = parse(RecordForm, request.form)
    if form is None:
        flash_errors(errors)
        return render_template("record.html", record=row, form=request.form, back=back), 400
    db().execute(
        "UPDATE rewards_punishments SET record_date = :record_date, kind = :kind, times = :times,"
        " reason = :reason WHERE id = :id",
        {**form.model_dump(mode="json"), "id": record_id},
    )
    db().commit()
    flash("已儲存獎懲紀錄")
    return redirect(back)


@app.post("/records/<int:record_id>/delete")
def delete_record(record_id: int):
    row = get_record_or_404(record_id)
    db().execute("DELETE FROM rewards_punishments WHERE id = ?", (record_id,))
    db().commit()
    flash(f"已刪除 {row['student_id']} {row['record_date']} 的{row['kind']}")
    return redirect(safe_next(url_for("student", student_id=row["student_id"]) + "#records"))


@app.get("/vehicle-passes")
def vehicle_passes():
    rows = db().execute(
        "SELECT p.*, s.name FROM vehicle_passes p LEFT JOIN students s USING (student_id) ORDER BY p.id DESC"
    ).fetchall()
    return render_template("vehicle_passes.html", passes=rows)


@app.get("/vehicle-passes/<int:pass_id>/images/<kind>")
def vehicle_pass_image(pass_id: int, kind: str):
    row = db().execute("SELECT * FROM vehicle_passes WHERE id = ?", (pass_id,)).fetchone()
    if row is None or kind not in IMAGE_KINDS:
        abort(404)
    return send_from_directory(UPLOAD_DIR, row[kind])


@app.post("/vehicle-passes/<int:pass_id>/delete")
def delete_vehicle_pass(pass_id: int):
    row = db().execute("SELECT * FROM vehicle_passes WHERE id = ?", (pass_id,)).fetchone()
    if row is None:
        abort(404)
    db().execute("DELETE FROM vehicle_passes WHERE id = ?", (pass_id,))
    db().commit()
    for kind in IMAGE_KINDS:  # 資料庫刪掉後才刪圖片，萬一中途失敗也只會留下沒人用的檔案
        (UPLOAD_DIR / row[kind]).unlink(missing_ok=True)
    flash(f"已刪除通行證申請 #{pass_id}")
    return redirect(safe_next(url_for("vehicle_passes")))
