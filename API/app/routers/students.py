from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.dependencies import CurrentStudent
from app.docs import NO_STUDENT, UNAUTHORIZED
from app.enums import AdmissionChannel, Gender, Identity

TAG_METADATA = {
    "name": "學生資料",
    "description": "學生的基本資料（系級、國籍、身分別、入學管道等）。"
    "資料由管理員用 `python -m app.manage` 建立，學生只能查看自己的。",
}
router = APIRouter(prefix="/students", tags=[TAG_METADATA["name"]], responses=UNAUTHORIZED)


class StudentProfile(BaseModel):
    """學生的基本資料"""

    student_id: str = Field(description="學號（大寫）", examples=["B11012345"])
    name: str = Field(description="姓名", examples=["王小明"])
    gender: Gender = Field(description="性別")
    birth_date: date = Field(description="出生日期", examples=["2005-03-14"])
    age: int = Field(description="年齡（依出生日期算到今天的足歲）", examples=[21])
    department: str = Field(description="系所", examples=["資訊工程學系"])
    grade: int = Field(description="年級", examples=[3])
    nationality: str = Field(description="國籍", examples=["中華民國"])
    identity: Identity = Field(description="身分別")
    admission_channel: AdmissionChannel = Field(description="入學管道")


def age_on(birth_date: date, today: date) -> int:
    # 今年的生日還沒到就少一歲
    before_birthday = (today.month, today.day) < (birth_date.month, birth_date.day)
    return today.year - birth_date.year - before_birthday


@router.get(
    "/me",
    response_model=StudentProfile,
    summary="查看自己的基本資料",
    response_description="這個學號的學生資料",
    responses=NO_STUDENT,
)
def get_my_profile(student: CurrentStudent):
    """登入的學生，或 `X-Student-ID` 指定的學生。年齡依伺服器今天的日期計算。"""
    age = age_on(date.fromisoformat(student["birth_date"]), date.today())
    return {**dict(student), "age": age}
