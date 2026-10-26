"""隨機產生測試用的學生和獎懲紀錄（由 `python -m app.manage seed` 呼叫）

全部是亂數組出來的假資料，學系用國立暨南國際大學的學士班。學號 9 碼：入學學年 3 碼 + 系碼 3 碼
+ 流水號 3 碼，例如 114321027，不會跟已有的學號重複；獎懲紀錄隨機分給資料庫裡的所有學生，
日期落在入學後到今天之間。
"""

import random
import sqlite3
from datetime import date, timedelta

from app.database import now_iso
from app.enums import AdmissionChannel, Gender, Identity, RewardKind

# 暨大的學士班學系（依註冊組 112 學年雙主修輔系聯絡表，加上各學院學士班和護理學系）。
# 系碼是暫訂的，不是學校官方的代碼：第一碼代表學院，拿到正確的系碼直接改這裡
DEPARTMENTS = {  # 系碼：學系
    # 人文學院
    "101": "中國語文學系",
    "102": "外國語文學系",
    "103": "社會政策與社會工作學系",
    "104": "公共行政與政策學系",
    "105": "歷史學系",
    "106": "東南亞學系",
    # 管理學院
    "200": "管理學院學士班",
    "201": "國際企業學系",
    "202": "經濟學系",
    "203": "資訊管理學系",
    "204": "財務金融學系",
    "205": "觀光休閒與餐旅管理學系",
    # 科技學院
    "300": "科技學院學士班",
    "321": "資訊工程學系",
    "322": "電機工程學系",
    "323": "土木工程學系",
    "324": "應用化學系",
    "325": "應用材料及光電工程學系",
    # 教育學院
    "400": "教育學院學士班",
    "401": "國際文教與比較教育學系",
    "402": "教育政策與行政學系",
    "403": "諮商心理與人力資源發展學系",
    # 護理暨健康福祉學院
    "501": "護理學系",
}
SURNAMES = "陳林黃張李王吳劉蔡楊許鄭謝郭洪曾邱廖賴周徐蘇葉莊呂江何蕭羅高"
GIVEN_NAMES = {
    Gender.male: ["冠宇", "宇翔", "承恩", "柏翰", "家豪", "俊傑", "彥廷", "宗翰", "品睿", "子軒", "建宏", "哲維"],
    Gender.female: ["怡君", "雅婷", "詩涵", "欣妤", "佳穎", "宜蓁", "思妤", "雅筑", "語彤", "品妍", "郁婷", "心怡"],
}
FOREIGN_NAMES = {  # 外籍生的國籍：各性別的姓名
    "日本": {Gender.male: ["佐藤健太", "鈴木翔太", "高橋大輝"], Gender.female: ["田中美咲", "伊藤結衣", "渡邊陽菜"]},
    "越南": {Gender.male: ["Nguyen Van An", "Tran Minh Duc"], Gender.female: ["Le Thi Lan", "Pham Thu Trang"]},
    "印尼": {Gender.male: ["Budi Santoso", "Agus Pratama"], Gender.female: ["Siti Rahma", "Dewi Lestari"]},
    "美國": {Gender.male: ["Ethan Miller", "Ryan Carter"], Gender.female: ["Emily Johnson", "Grace Wilson"]},
}
OVERSEAS_CHINESE_NATIONALITIES = ["馬來西亞", "印尼", "緬甸", "泰國"]

# 抽樣權重，大致照一般大學的比例；港澳生、陸生不產生
IDENTITY_WEIGHTS = {
    Identity.general: 12,
    Identity.indigenous: 1,
    Identity.disability: 1,
    Identity.low_income: 1,
    Identity.middle_low_income: 1,
    Identity.overseas_chinese: 2,
    Identity.foreign: 2,
}
LOCAL_ADMISSION_WEIGHTS = {  # 本地生；身心障礙生另外加上身心障礙甄試
    AdmissionChannel.star: 3,
    AdmissionChannel.application: 5,
    AdmissionChannel.placement: 3,
    AdmissionChannel.special_selection: 1,
    AdmissionChannel.sports: 1,
    AdmissionChannel.transfer: 1,
}
KIND_WEIGHTS = {
    RewardKind.commendation: 8,
    RewardKind.minor_merit: 3,
    RewardKind.major_merit: 1,
    RewardKind.warning: 5,
    RewardKind.minor_demerit: 2,
    RewardKind.major_demerit: 1,
}
REASONS = {
    RewardKind.commendation: [
        "擔任班級幹部認真負責",
        "參與校慶志工服務",
        "協助系學會舉辦迎新活動",
        "拾獲錢包送交失物招領",
        "擔任新生定向輔導員",
    ],
    RewardKind.minor_merit: [
        "代表學校參加校際競賽獲得佳績",
        "擔任社團社長表現優異",
        "學業成績優異獲得書卷獎",
    ],
    RewardKind.major_merit: ["參加全國性競賽獲得第一名", "見義勇為協助救助傷患"],
    RewardKind.warning: [
        "上課無故缺席累計達規定次數",
        "宿舍違規使用電器",
        "未依規定停放機車",
        "宿舍晚歸未登記",
    ],
    RewardKind.minor_demerit: ["於宿舍內吸菸", "多次違規停車經勸導未改善", "冒用他人學生證進出圖書館"],
    RewardKind.major_demerit: ["考試作弊", "打架滋事致人受傷"],
}


def pick(rng: random.Random, weights: dict):
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def current_academic_year(today: date) -> int:
    """目前的學年（民國年），8 月起算新學年"""
    return today.year - 1911 - (today.month < 8)


def enrolled_on(grade: int, today: date) -> date:
    """這個年級的學生入學的日期（以 9 月 1 日計）"""
    entry_year = current_academic_year(today) - (grade - 1)
    return date(entry_year + 1911, 9, 1)


def random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max((end - start).days, 0)))


def random_student(rng: random.Random, today: date, taken: set[str]) -> dict:
    gender = rng.choice(list(Gender))
    identity = pick(rng, IDENTITY_WEIGHTS)
    chinese_name = rng.choice(SURNAMES) + rng.choice(GIVEN_NAMES[gender])
    if identity is Identity.foreign:
        nationality = rng.choice(list(FOREIGN_NAMES))
        name = rng.choice(FOREIGN_NAMES[nationality][gender])
        admission = AdmissionChannel.foreign
    elif identity is Identity.overseas_chinese:
        nationality, name = rng.choice(OVERSEAS_CHINESE_NATIONALITIES), chinese_name
        admission = AdmissionChannel.overseas_chinese
    else:
        nationality, name = "中華民國", chinese_name
        weights = dict(LOCAL_ADMISSION_WEIGHTS)
        if identity is Identity.disability:
            weights[AdmissionChannel.disability_exam] = 4
        admission = pick(rng, weights)

    grade = rng.randint(1, 4)
    code, department = rng.choice(list(DEPARTMENTS.items()))
    entry = enrolled_on(grade, today)
    while True:
        student_id = f"{entry.year - 1911}{code}{rng.randint(1, 60):03d}"
        if student_id not in taken:
            break
    taken.add(student_id)

    # 18 歲入學，有些人重考或晚讀會大一兩歲
    latest_birth = entry.replace(year=entry.year - 18 - pick(rng, {0: 8, 1: 1, 2: 1}))
    return {
        "student_id": student_id,
        "name": name,
        "gender": gender.value,
        "birth_date": random_date(rng, latest_birth - timedelta(days=364), latest_birth).isoformat(),
        "department": department,
        "grade": grade,
        "nationality": nationality,
        "identity": identity.value,
        "admission_channel": admission.value,
    }


def random_record(rng: random.Random, student: sqlite3.Row, today: date) -> dict:
    kind = pick(rng, KIND_WEIGHTS)
    start = min(enrolled_on(student["grade"], today), today)
    return {
        "student_id": student["student_id"],
        "record_date": random_date(rng, start, today).isoformat(),
        "kind": kind.value,
        "times": rng.choice([1, 1, 2]) if kind in (RewardKind.commendation, RewardKind.warning) else 1,
        "reason": rng.choice(REASONS[kind]),
    }


def seed(db: sqlite3.Connection, students: int, records: int) -> None:
    """新增 students 位學生、records 筆獎懲紀錄；要登記獎懲時資料庫裡至少要有一位學生"""
    rng = random.Random()
    today = date.today()
    created_at = now_iso()

    taken = {row["student_id"] for row in db.execute("SELECT student_id FROM students")}
    for _ in range(students):
        db.execute(
            "INSERT INTO students (student_id, name, gender, birth_date, department, grade,"
            " nationality, identity, admission_channel, created_at)"
            " VALUES (:student_id, :name, :gender, :birth_date, :department, :grade,"
            " :nationality, :identity, :admission_channel, :created_at)",
            {**random_student(rng, today, taken), "created_at": created_at},
        )

    candidates = db.execute("SELECT student_id, grade FROM students").fetchall()
    for _ in range(records):
        db.execute(
            "INSERT INTO rewards_punishments (student_id, record_date, kind, times, reason, created_at)"
            " VALUES (:student_id, :record_date, :kind, :times, :reason, :created_at)",
            {**random_record(rng, rng.choice(candidates), today), "created_at": created_at},
        )
