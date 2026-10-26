import sqlite3
import uuid
from datetime import datetime
from enum import Enum
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Path, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.database import UPLOAD_DIR, now_iso
from app.dependencies import Db, StudentId
from app.docs import UNAUTHORIZED, error_response

TAG_METADATA = {
    "name": "汽機車通行證",
    "description": "學生申請校園汽機車通行證。後端只紀錄申請資料和證件圖片（行照、駕照），不做審核。",
}
router = APIRouter(prefix="/vehicle-passes", tags=[TAG_METADATA["name"]], responses=UNAUTHORIZED)

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
# 用檔頭判斷圖片格式，不依賴 client 自己報的 Content-Type
IMAGE_SIGNATURES = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG\r\n\x1a\n": ".png",
}

NOT_FOUND = {404: error_response("編號不存在，或不是這個學號的申請", "找不到這筆申請")}


class VehicleType(str, Enum):
    """車輛種類：car 汽車、motorcycle 機車"""

    car = "car"
    motorcycle = "motorcycle"


class ImageKind(str, Enum):
    """證件圖片：vehicle_registration 行照、driver_license 駕照"""

    vehicle_registration = "vehicle_registration"
    driver_license = "driver_license"


class VehiclePass(BaseModel):
    """一筆通行證申請紀錄"""

    id: int = Field(description="申請編號", examples=[1])
    student_id: str = Field(description="學號（已轉成大寫）", examples=["B11012345"])
    vehicle_type: VehicleType = Field(
        description="車輛種類：car 汽車 / motorcycle 機車", examples=["motorcycle"]
    )
    vehicle_model: str = Field(description="型號", examples=["SYM JET SL 125"])
    created_at: datetime = Field(
        description="申請時間（ISO 8601，含時區）", examples=["2026-10-12T23:07:48+08:00"]
    )


PassId = Annotated[int, Path(description="申請編號（申請時回傳的 `id`）")]


def read_image(upload: UploadFile, label: str) -> tuple[bytes, str]:
    """讀出上傳的圖片並檢查大小與格式，回傳 (內容, 副檔名)"""
    data = upload.file.read(MAX_IMAGE_SIZE + 1)
    if len(data) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail=f"{label}圖片不能超過 5 MB")
    for signature, ext in IMAGE_SIGNATURES.items():
        if data.startswith(signature):
            return data, ext
    raise HTTPException(status_code=415, detail=f"{label}只接受 JPEG 或 PNG 圖片")


def save_image(data: bytes, ext: str) -> str:
    filename = f"{uuid.uuid4().hex}{ext}"
    (UPLOAD_DIR / filename).write_bytes(data)
    return filename


def get_own_pass(db: sqlite3.Connection, student_id: str, pass_id: int) -> sqlite3.Row:
    # 只查得到自己的申請，別人的一律當作不存在
    row = db.execute(
        "SELECT * FROM vehicle_passes WHERE id = ? AND student_id = ?",
        (pass_id, student_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到這筆申請")
    return row


def list_own_passes(db: sqlite3.Connection, student_id: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM vehicle_passes WHERE student_id = ? ORDER BY id DESC",
        (student_id,),
    ).fetchall()


@router.post(
    "",
    status_code=201,
    response_model=VehiclePass,
    summary="申請汽機車通行證",
    response_description="申請成功，回傳這筆申請紀錄",
    responses={
        413: error_response("行照或駕照超過 5 MB", "行照圖片不能超過 5 MB"),
        415: error_response("行照或駕照不是 JPEG / PNG 圖片", "駕照只接受 JPEG 或 PNG 圖片"),
    },
)
def apply_vehicle_pass(
    student_id: StudentId,
    db: Db,
    vehicle_type: Annotated[VehicleType, Form(description="種類：car 汽車 / motorcycle 機車")],
    vehicle_model: Annotated[
        str, Form(min_length=1, max_length=50, description="型號", examples=["SYM JET SL 125"])
    ],
    vehicle_registration: Annotated[UploadFile, File(description="行照圖片（JPEG / PNG，5 MB 以內）")],
    driver_license: Annotated[UploadFile, File(description="駕照圖片（JPEG / PNG，5 MB 以內）")],
):
    """
    送出一筆汽機車通行證申請（只做紀錄，不審核）。

    - 用 `multipart/form-data` 送出
    - 行照、駕照只接受 **JPEG / PNG**，每張 **5 MB** 以內；看的是檔案內容，只改副檔名會被擋下
    - 兩張圖都通過檢查才會存檔，有一張不合格整筆申請就不會成立
    """
    # 兩張都檢查過才寫入硬碟，免得一張存了、另一張才被擋下
    registration_image = read_image(vehicle_registration, "行照")
    license_image = read_image(driver_license, "駕照")

    cur = db.execute(
        "INSERT INTO vehicle_passes"
        " (student_id, vehicle_type, vehicle_model, vehicle_registration, driver_license, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            student_id,
            vehicle_type.value,
            vehicle_model,
            save_image(*registration_image),
            save_image(*license_image),
            now_iso(),
        ),
    )
    db.commit()
    return dict(get_own_pass(db, student_id, cur.lastrowid))


@router.get(
    "",
    response_model=list[VehiclePass],
    summary="查看自己的申請紀錄",
    response_description="這個學號的所有申請",
)
def list_vehicle_passes(student_id: StudentId, db: Db):
    """列出這個學號的所有申請，新的排在前面；還沒申請過會回傳空陣列 `[]`。"""
    return [dict(row) for row in list_own_passes(db, student_id)]


@router.get(
    "/{pass_id}",
    response_model=VehiclePass,
    summary="查看單筆申請",
    response_description="這筆申請紀錄",
    responses=NOT_FOUND,
)
def get_vehicle_pass(pass_id: PassId, student_id: StudentId, db: Db):
    """只查得到自己學號的申請，別人的一律回 `404`。"""
    return dict(get_own_pass(db, student_id, pass_id))


@router.get(
    "/{pass_id}/images/{kind}",
    response_class=FileResponse,
    summary="查看申請時上傳的行照 / 駕照",
    response_description="圖片檔（JPEG 或 PNG）",
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}}, **NOT_FOUND},
)
def get_vehicle_pass_image(
    pass_id: PassId,
    kind: Annotated[ImageKind, Path(description="要哪一張：vehicle_registration 行照 / driver_license 駕照")],
    student_id: StudentId,
    db: Db,
):
    """
    直接回傳圖片檔，不是 JSON。

    程式帶 `X-Student-ID` 取回；瀏覽器登入後靠 cookie 識別，可以直接把網址放進 `<img src>`。
    """
    row = get_own_pass(db, student_id, pass_id)
    return FileResponse(UPLOAD_DIR / row[kind.value])
