from datetime import date
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.dependencies import Db, StudentId
from app.docs import UNAUTHORIZED
from app.enums import RewardKind

TAG_METADATA = {
    "name": "獎懲紀錄",
    "description": "學生的獎懲紀錄（嘉獎、小功、大功、警告、小過、大過）。"
    "紀錄由管理員用 `python -m app.manage record` 登記，學生只能查看自己的。",
}
router = APIRouter(
    prefix="/rewards-punishments", tags=[TAG_METADATA["name"]], responses=UNAUTHORIZED
)


class RewardPunishment(BaseModel):
    """一筆獎懲紀錄"""

    id: int = Field(description="紀錄編號", examples=[1])
    student_id: str = Field(description="學號（大寫）", examples=["B11012345"])
    record_date: date = Field(description="日期", examples=["2026-10-20"])
    category: Literal["獎勵", "懲處"] = Field(description="獎勵或懲處，由獎懲類別決定")
    kind: RewardKind = Field(description="獎懲類別")
    times: int = Field(description="次數", examples=[1])
    reason: str = Field(description="事由", examples=["擔任班級幹部認真負責"])


@router.get(
    "",
    response_model=list[RewardPunishment],
    summary="查看自己的獎懲紀錄",
    response_description="這個學號的所有獎懲紀錄",
)
def list_rewards_punishments(student_id: StudentId, db: Db):
    """列出這個學號的所有獎懲紀錄，日期新的排在前面；沒有紀錄會回傳空陣列 `[]`。"""
    rows = db.execute(
        "SELECT * FROM rewards_punishments WHERE student_id = ? ORDER BY record_date DESC, id DESC",
        (student_id,),
    ).fetchall()
    return [{**dict(row), "category": RewardKind(row["kind"]).category} for row in rows]
