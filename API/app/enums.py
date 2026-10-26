"""學生資料和獎懲紀錄裡有固定選項的欄位。資料庫存的是 value（中文），API 文件也會列出這些選項"""

from enum import Enum
from typing import Literal


class Gender(str, Enum):
    """性別"""

    male = "男"
    female = "女"


class Identity(str, Enum):
    """身分別"""

    general = "一般生"
    indigenous = "原住民"
    disability = "身心障礙"
    low_income = "低收入戶"
    middle_low_income = "中低收入戶"
    overseas_chinese = "僑生"
    hong_kong_macau = "港澳生"
    mainland = "陸生"
    foreign = "外籍生"


class AdmissionChannel(str, Enum):
    """入學管道"""

    star = "繁星推薦"
    application = "個人申請"
    placement = "分科測驗分發"
    special_selection = "特殊選才"
    sports = "運動績優"
    disability_exam = "身心障礙甄試"
    overseas_chinese = "僑生聯合分發"
    foreign = "外國學生申請"
    transfer = "轉學考"


class RewardKind(str, Enum):
    """獎懲類別：嘉獎、小功、大功是獎勵，警告、小過、大過是懲處"""

    commendation = "嘉獎"
    minor_merit = "小功"
    major_merit = "大功"
    warning = "警告"
    minor_demerit = "小過"
    major_demerit = "大過"

    @property
    def category(self) -> Literal["獎勵", "懲處"]:
        rewards = (RewardKind.commendation, RewardKind.minor_merit, RewardKind.major_merit)
        return "獎勵" if self in rewards else "懲處"
