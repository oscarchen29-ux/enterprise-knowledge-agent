from typing import Any

from pydantic import BaseModel, Field

DESCRIPTION = """
模擬校務行政系統的後端 API（port 7000），給區網內的程式和測試使用。

> **這是封閉的測試環境**：API 直接相信 `X-Student-ID` header，連得到 API 的人都能冒用任何學號，
> 請不要放真實資料，也不要開放到區網以外。

## 學號從哪裡來

兩種方式擇一，都有的話以 header 為準：

- **`X-Student-ID` header**：直接帶學號，英數字 1–20 碼，不分大小寫（後端一律轉成大寫），不需要密碼。
  給程式、測試和這頁的「Try it out」用
- **登入 cookie**：用 `POST /auth/login` 以學號和密碼登入，伺服器設定 `session` cookie（8 小時有效），
  之後的 request 帶著 cookie 就好

其他規則：

- 只查得到自己學號的資料，查別人的會回 `404`
- 兩種都沒有，或登入過期，回 `401`；header 格式不對回 `422`
- 學生資料、登入密碼和獎懲紀錄由管理員用 `python -m app.manage` 建立。通行證、獎懲紀錄只看學號；
  `/auth/me`、`/students/me` 要這個學號有學生資料，用 header 指定的學號沒有的話回 `404`

## 錯誤格式

發生錯誤時回傳 JSON，`detail` 是錯誤說明：

```json
{"detail": "找不到這筆申請"}
```

欄位缺少或格式錯誤（`422`）時，`detail` 會是陣列，逐一列出有問題的欄位：

```json
{"detail": [{"type": "missing", "loc": ["body", "vehicle_model"], "msg": "Field required", "input": null}]}
```
"""


class ErrorMessage(BaseModel):
    """錯誤回應"""

    detail: str = Field(description="錯誤說明")


def error_response(description: str, detail: str) -> dict[str, Any]:
    """給 route 的 `responses=` 用，讓文件列出這個錯誤的說明和範例"""
    return {
        "model": ErrorMessage,
        "description": description,
        "content": {"application/json": {"example": {"detail": detail}}},
    }


# 需要學號的 router 都掛上這個，文件才會列出 401
UNAUTHORIZED = {401: error_response("沒帶 X-Student-ID，也沒有登入（或登入已過期）", "請先登入")}
# 用到 CurrentStudent（需要學生資料）的 endpoint 掛上這個
NO_STUDENT = {
    404: error_response("用 X-Student-ID 指定的學號沒有學生資料", "找不到這個學號的學生資料")
}
