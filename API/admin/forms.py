"""管理介面的表單驗證，欄位規則跟 API、python -m app.manage 一樣"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from werkzeug.datastructures import MultiDict

from app.enums import AdmissionChannel, Gender, Identity, RewardKind


class StudentForm(BaseModel):
    """學生基本資料（編輯時學號不能改，獎懲紀錄、帳號都靠學號連著）"""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=50)
    gender: Gender
    birth_date: date
    department: str = Field(min_length=1, max_length=50)
    grade: int = Field(ge=1, le=7)
    nationality: str = Field(min_length=1, max_length=50)
    identity: Identity
    admission_channel: AdmissionChannel


class NewStudentForm(StudentForm):
    student_id: str = Field(pattern=r"^[A-Za-z0-9]{1,20}$")


class RecordForm(BaseModel):
    """一筆獎懲紀錄"""

    model_config = ConfigDict(str_strip_whitespace=True)

    record_date: date
    kind: RewardKind
    times: int = Field(ge=1, le=3)
    reason: str = Field(min_length=1, max_length=200)


# pydantic 的錯誤訊息是英文，換成每個欄位自己的中文說明
HINTS = {
    "student_id": "學號要是英數字 1–20 碼",
    "name": "姓名要 1–50 個字",
    "gender": "請選擇性別",
    "birth_date": "出生日期格式不正確",
    "department": "學系要 1–50 個字",
    "grade": "年級要是 1–7",
    "nationality": "國籍要 1–50 個字",
    "identity": "請選擇身分別",
    "admission_channel": "請選擇入學管道",
    "record_date": "日期格式不正確",
    "kind": "請選擇獎懲類別",
    "times": "次數要是 1–3",
    "reason": "事由要 1–200 個字",
}


def parse(model: type[BaseModel], form: MultiDict) -> tuple[BaseModel | None, list[str]]:
    """驗證表單，回傳 (資料, [])；不通過時回傳 (None, 錯誤訊息)"""
    try:
        return model.model_validate(form.to_dict()), []
    except ValidationError as e:
        fields = sorted({error["loc"][0] for error in e.errors()}, key=list(HINTS).index)  # 照表單欄位的順序
        return None, [HINTS[field] for field in fields]
