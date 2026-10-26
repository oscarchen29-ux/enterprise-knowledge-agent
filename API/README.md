# 模擬校務行政系統 API

用 FastAPI 和 SQLite 寫的模擬校務行政系統，目前提供**學生基本資料**、**獎懲紀錄**、**汽機車通行證申請**（後端只紀錄申請資料和證件圖片，不做審核）和學生登入。假資料以國立暨南國際大學的學系為範本。

| 程式 | 位址 | 用途 |
|---|---|---|
| API（FastAPI） | `0.0.0.0:7000` | 給區網的程式呼叫，學生只能查看自己的資料 |
| 資料庫管理介面（Flask） | `127.0.0.1:7001` | 給管理員用網頁查看、修改資料庫，只有這台電腦連得到 |

> **注意：這是封閉的測試環境。** API 直接相信 `X-Student-ID` header，連得到 API 的人都能冒用任何學號；區網上走的也是明碼 HTTP。請不要放真實資料、不要上傳真實的行照駕照，防火牆只開放給信任的網段，也不要開放到網際網路。

## 快速開始

需要 Python 3.10 以上。在專案根目錄執行：

```bash
pip install -r requirements.txt
python -m app.manage seed     # 隨機產生 20 位學生、20 筆獎懲紀錄（假資料）；想從空的開始可以跳過
python -m app.manage list     # 列出學生，挑一個學號來試
```

要自己建立學生、設定登入密碼，見[學生資料、帳號和獎懲紀錄](#學生資料帳號和獎懲紀錄)，或用[資料庫管理介面](#資料庫管理介面)。

然後雙擊 `start-api.cmd`（Linux 執行 `./start-api.sh`），視窗會印出這台電腦和區網用的網址。開發時也可以直接執行，改了程式會自動重新載入：

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 7000 --reload
```

啟動後可以打開（區網的其他電腦把 `127.0.0.1` 換成這台電腦的 IP）：

| 網址 | 內容 |
|---|---|
| http://127.0.0.1:7000/docs | Swagger UI；「Try it out」填 `X-Student-ID` 就能直接試（打開 `/` 也會轉到這裡） |
| http://127.0.0.1:7000/redoc | ReDoc 版的 API 文件 |
| http://127.0.0.1:7000/openapi.json | OpenAPI 規格，給程式讀取 |

要用網頁查看、修改資料庫，另外雙擊 `start-admin.cmd`（Linux 執行 `./start-admin.sh`），見下面的[資料庫管理介面](#資料庫管理介面)。

uvicorn 印的 `http://0.0.0.0:7000` 只代表「監聽所有網卡」，瀏覽器打不開（`ERR_ADDRESS_INVALID`），不要用那個。

區網要連得到，防火牆要放行 7000。Windows 第一次啟動可能會跳出防火牆視窗，要允許 Python 通訊；沒跳出來或之前按了拒絕，用**系統管理員**身分開 PowerShell 加規則：

```powershell
New-NetFirewallRule -DisplayName "校務 API 7000" -Direction Inbound -Protocol TCP -LocalPort 7000 -Action Allow -Profile Private -RemoteAddress LocalSubnet
```

`-RemoteAddress LocalSubnet` 讓只有同一個網段的裝置連得到。Windows 把網路歸類為「公用網路」時 `-Profile Private` 的規則不會生效，用 `Get-NetConnectionProfile` 查看，必要時改成 `-Profile Public`（`LocalSubnet` 要留著）。Linux 見 [docs/linux-deploy.md](docs/linux-deploy.md)，裡面也有開機自動啟動的設定。

資料存在 `data/` 資料夾，第一次啟動時會自動建立：學生資料、帳號、獎懲紀錄和通行證申請紀錄在 `data/school.db`（SQLite），圖片在 `data/uploads/`。要清空資料，關掉伺服器後刪除整個 `data/` 即可（學生資料和帳號也會一起清掉）。

## 學生資料、帳號和獎懲紀錄

只能由管理員在伺服器上管理（用下面的指令，或[資料庫管理介面](#資料庫管理介面)），沒有開放註冊，學生透過 API 只能查看自己的資料：

```bash
# 新增學生
python -m app.manage add B11012345 王小明 --gender 男 --birth-date 2005-03-14 \
  --department 資訊工程學系 --grade 3 --admission 個人申請

python -m app.manage passwd B11012345           # 設定或重設密碼，同時登出所有裝置
python -m app.manage list                       # 列出
python -m app.manage remove B11012345           # 刪除，連同帳號和獎懲紀錄（通行證申請紀錄會保留）
python -m app.manage record B11012345 嘉獎 --times 2 --reason 協助系學會活動   # 登記獎懲，日期預設今天
python -m app.manage seed --students 20 --records 20                          # 隨機產生假資料
```

- `add` 的國籍預設 `中華民國`、身分別預設 `一般生`，用 `--nationality`、`--identity` 指定。身分別、入學管道、獎懲類別的可選值見 `python -m app.manage add -h`、`record -h`，或 `app/enums.py`
- 學生資料和帳號是分開的：`add` 只建立學生資料，`passwd` 設了密碼才能用 `POST /auth/login` 登入；`X-Student-ID` 不需要密碼
- 學號是英數字 1–20 碼，格式不限（例如 `B11012345`）
- `seed` 產生的學生沒有密碼；獎懲紀錄會隨機分給資料庫裡的所有學生，日期落在入學後到今天之間

### 假資料的學系和學號

`seed` 用國立暨南國際大學的學士班學系（依註冊組 112 學年雙主修輔系聯絡表，加上各學院學士班和護理學系），學號 9 碼：

| 入學學年 | 系碼 | 流水號 | 例子 |
|---|---|---|---|
| 3 碼（民國年） | 3 碼 | 3 碼 | `114321056` = 114 學年入學、資訊工程學系 |

**系碼是暫訂的，不是暨大官方的代碼**（公開資料查不到）：第一碼代表學院，1 人文、2 管理、3 科技、4 教育、5 護理暨健康福祉。對照表在 `app/seed.py` 的 `DEPARTMENTS`，拿到正確的系碼直接改那裡，再重新產生假資料。

## 資料庫管理介面

用 Flask 做的網頁，可以查看和修改學生資料、登入密碼、獎懲紀錄，以及查看、刪除通行證申請。跟 API 是分開的程式，可以同時開著：

```bash
python -m admin      # 或雙擊 start-admin.cmd / 執行 ./start-admin.sh
```

然後打開 http://127.0.0.1:7001 。

| 頁面 | 可以做的事 |
|---|---|
| 學生 | 列出、搜尋學生（學號、姓名、學系），新增學生 |
| 學生的頁面 | 修改基本資料（學號不能改）、登記 / 編輯 / 刪除獎懲、設定或重設密碼、刪除帳號、刪除學生 |
| 獎懲紀錄 | 列出所有紀錄，可以只看獎勵或懲處，編輯、刪除 |
| 通行證申請 | 列出所有申請、看行照駕照圖片、刪除申請（連同圖片） |

- **沒有登入機制**，所以只綁 `127.0.0.1`，區網連不到；也只接受網址是 `127.0.0.1` 或 `localhost` 的 request，並擋掉其他網站送來的表單
- 伺服器在別台電腦（例如 Linux）時，用 SSH 通道連過去，再在自己電腦的瀏覽器打開 http://127.0.0.1:7001 ：`ssh -L 7001:127.0.0.1:7001 <帳號>@<伺服器>`
- 修改會直接寫進 `data/school.db`，沒有復原功能；刪除前會跳出確認視窗
- 欄位規則跟 `python -m app.manage` 一樣，學系、國籍欄位會列出暨大的學系和常見國籍當作提示，也可以自己填

## 共通規則

### 學號從哪裡來

告訴 API 學號有兩種方式，擇一即可，都有的話以 header 為準。

**`X-Student-ID` header**：給程式、測試用。

- 英數字 1–20 碼，不分大小寫（後端一律轉成大寫）
- 不需要密碼，API 直接相信這個 header（所以只能用在封閉的測試環境）
- 通行證、獎懲紀錄只看學號；`/auth/me`、`/students/me` 要這個學號有學生資料，不然回 `404`

```bash
curl http://127.0.0.1:7000/vehicle-passes -H "X-Student-ID: B11012345"
```

**登入 cookie**：用 `POST /auth/login` 送出學號和密碼，成功後伺服器會設定 `session` cookie（8 小時有效），之後的 request 帶著 cookie，後端就從登入狀態知道你的學號。用 curl 試時要加 `-c cookies.txt -b cookies.txt` 把 cookie 存起來再帶回去：

```bash
curl -c cookies.txt http://127.0.0.1:7000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"student_id": "B11012345", "password": "你的密碼"}'
```

| 方法 | 路徑 | 說明 |
|---|---|---|
| `POST` | `/auth/login` | 登入，學號或密碼錯誤回 `401` |
| `POST` | `/auth/logout` | 登出 |
| `GET` | `/auth/me` | 目前的學生（學號、姓名） |

其他規則：

- 只查得到自己學號的資料，查別人的會回 `404`
- 兩種都沒有，或登入過期，回 `401`；header 格式不對回 `422`
- API 沒有設定 CORS，其他網站的網頁用 `fetch` 呼叫會被瀏覽器擋下；之後要接網頁前端的話，在 `app/main.py` 加上 FastAPI 的 `CORSMiddleware`

### 錯誤格式

錯誤都回傳 JSON，`detail` 是錯誤說明：

```json
{"detail": "找不到這筆申請"}
```

欄位缺少或格式錯誤（`422`）時，`detail` 是陣列，逐一列出有問題的欄位：

```json
{"detail": [{"type": "missing", "loc": ["body", "vehicle_model"], "msg": "Field required", "input": null}]}
```

## 學生資料 API

`GET /students/me`：回傳自己的基本資料。年齡是依出生日期算到今天的足歲，資料庫只存出生日期。用 `X-Student-ID` 指定的學號沒有學生資料時回 `404`。

```bash
curl http://127.0.0.1:7000/students/me -H "X-Student-ID: B11012345"
```

```json
{
  "student_id": "B11012345",
  "name": "王小明",
  "gender": "男",
  "birth_date": "2005-03-14",
  "age": 21,
  "department": "資訊工程學系",
  "grade": 3,
  "nationality": "中華民國",
  "identity": "一般生",
  "admission_channel": "個人申請"
}
```

## 獎懲紀錄 API

`GET /rewards-punishments`：回傳自己的獎懲紀錄陣列，日期新的排在前面；沒有紀錄會回 `[]`。`category` 是 `獎勵`（嘉獎、小功、大功）或 `懲處`（警告、小過、大過）。

```bash
curl http://127.0.0.1:7000/rewards-punishments -H "X-Student-ID: B11012345"
```

```json
[
  {
    "id": 1,
    "student_id": "B11012345",
    "record_date": "2026-10-20",
    "category": "獎勵",
    "kind": "嘉獎",
    "times": 2,
    "reason": "協助系學會活動"
  }
]
```

## 汽機車通行證 API

| 方法 | 路徑 | 說明 |
|---|---|---|
| `POST` | `/vehicle-passes` | 申請通行證 |
| `GET` | `/vehicle-passes` | 查看自己的申請紀錄 |
| `GET` | `/vehicle-passes/{pass_id}` | 查看單筆申請 |
| `GET` | `/vehicle-passes/{pass_id}/images/{kind}` | 取回申請時上傳的行照 / 駕照 |

以下範例用 bash 的 `curl`，在這台電腦上呼叫 API；從區網其他電腦呼叫時把 `127.0.0.1` 換成這台電腦的 IP。Windows PowerShell 請把 `curl` 換成 `curl.exe`，行尾的 `\` 換成 `` ` ``。

### 申請通行證

`POST /vehicle-passes`，用 `multipart/form-data` 送出：

| 欄位 | 類型 | 說明 |
|---|---|---|
| `vehicle_type` | 字串 | `car`（汽車）或 `motorcycle`（機車） |
| `vehicle_model` | 字串 | 型號，1–50 字 |
| `vehicle_registration` | 檔案 | 行照圖片，JPEG 或 PNG，5 MB 以內 |
| `driver_license` | 檔案 | 駕照圖片，JPEG 或 PNG，5 MB 以內 |

圖片格式是看檔案內容判斷，只改副檔名會被擋下。兩張圖都通過檢查才會存檔，只要有一張不合格，整筆申請就不會成立。

```bash
curl -X POST http://127.0.0.1:7000/vehicle-passes \
  -H "X-Student-ID: B11012345" \
  -F "vehicle_type=motorcycle" \
  -F "vehicle_model=SYM JET SL 125" \
  -F "vehicle_registration=@registration.jpg" \
  -F "driver_license=@license.jpg"
```

成功時回 `201` 和這筆申請紀錄：

```json
{
  "id": 1,
  "student_id": "B11012345",
  "vehicle_type": "motorcycle",
  "vehicle_model": "SYM JET SL 125",
  "created_at": "2026-10-13T10:30:00+08:00"
}
```

| 狀態碼 | 情況 |
|---|---|
| `201` | 申請成功 |
| `413` | 行照或駕照超過 5 MB |
| `415` | 行照或駕照不是 JPEG / PNG |
| `422` | 欄位缺少或格式錯誤 |

### 查看自己的申請紀錄

`GET /vehicle-passes`：回傳申請紀錄的陣列，新的排在前面；還沒申請過會回 `[]`。

```bash
curl http://127.0.0.1:7000/vehicle-passes -H "X-Student-ID: B11012345"
```

### 查看單筆申請

`GET /vehicle-passes/{pass_id}`：回傳格式跟申請成功時一樣。編號不存在或不是自己的申請會回 `404`。

```bash
curl http://127.0.0.1:7000/vehicle-passes/1 -H "X-Student-ID: B11012345"
```

### 取回行照 / 駕照圖片

`GET /vehicle-passes/{pass_id}/images/{kind}`，`kind` 填 `vehicle_registration`（行照）或 `driver_license`（駕照）。回傳的是圖片檔本身（`image/jpeg` 或 `image/png`），不是 JSON；找不到申請會回 `404`。

```bash
curl http://127.0.0.1:7000/vehicle-passes/1/images/driver_license \
  -H "X-Student-ID: B11012345" -o downloaded.jpg
```

## 專案結構

```text
app/                       # API（0.0.0.0:7000）
├── main.py                # 建立 FastAPI app、掛上 router
├── database.py            # SQLite 連線與資料表定義（SCHEMA、SCHEMA_VERSION）
├── enums.py               # 性別、身分別、入學管道、獎懲類別的可選值
├── dependencies.py        # 共用的 dependency：學號 StudentId（X-Student-ID 或登入 cookie）、學生資料 CurrentStudent、資料庫連線 Db
├── docs.py                # API 文件的總說明、共用錯誤格式 error_response()、UNAUTHORIZED、NO_STUDENT
├── security.py            # 密碼雜湊、session token
├── manage.py              # 管理學生資料、密碼、獎懲紀錄的指令（python -m app.manage）
├── seed.py                # 隨機產生假資料（manage seed 用），也列出暨大的學系
└── routers/
    ├── auth.py                  # 登入 / 登出
    ├── students.py              # 學生資料 API
    ├── rewards_punishments.py   # 獎懲紀錄 API
    └── vehicle_passes.py        # 汽機車通行證 API
admin/                     # 資料庫管理介面（Flask，127.0.0.1:7001）
├── __main__.py            # 啟動（python -m admin）
├── web.py                 # 各頁面
├── forms.py               # 表單驗證
└── templates/             # 頁面範本
data/                      # 執行後自動產生：school.db、uploads/
deploy/systemd/            # Linux 開機自動啟動用的 service 檔
docs/linux-deploy.md       # 搬到 Linux 的步驟
start-api.cmd / .sh        # 啟動 API（Windows / Linux），會印出區網網址
start-admin.cmd / .sh      # 啟動資料庫管理介面
requirements.txt
```

### 資料表

| 資料表 | 內容 |
|---|---|
| `students` | 學生基本資料：學號、姓名、性別、出生日期、系所、年級、國籍、身分別、入學管道 |
| `accounts` | 登入帳號（密碼雜湊），設過密碼的學生才有 |
| `login_sessions` | 登入中的 session |
| `rewards_punishments` | 獎懲紀錄：學號、日期、獎懲類別、次數、事由 |
| `vehicle_passes` | 通行證申請紀錄 |

`accounts`、`login_sessions`、`rewards_punishments` 用外鍵連到學生，刪除學生時一起刪掉；`vehicle_passes` 不設外鍵，學號不需要有學生資料，刪除學生也會保留。

## 新增功能

可以照著 `app/routers/vehicle_passes.py` 的寫法：

1. 在 `app/database.py` 的 `SCHEMA` 加上新的資料表
2. 在 `app/routers/` 新增一個檔案，定義 `router` 和 `TAG_METADATA`（這個功能在文件上的名稱和說明）；需要學號的 router 加上 `responses=UNAUTHORIZED`
3. 在 `app/main.py` 用 `app.include_router()` 掛上新的 router，並把它的 `TAG_METADATA` 加進 `openapi_tags`
4. endpoint 用 `StudentId`、`Db` 取得學號和資料庫連線，需要學生資料（姓名等）的改用 `CurrentStudent`；可能回傳的錯誤用 `error_response()` 寫在 `responses=`，文件上才會列出來
5. 要在管理介面也能查看、修改，在 `admin/web.py` 加上頁面、`admin/templates/` 加上範本（繼承 `base.html`），並在 `base.html` 的導覽列加上連結；有表單的話，驗證規則寫在 `admin/forms.py`

`SCHEMA` 用的是 `CREATE TABLE IF NOT EXISTS`，所以新增資料表沒問題；但如果是**修改既有資料表的欄位**，已經建立的資料庫不會跟著改。這時把 `app/database.py` 的 `SCHEMA_VERSION` 加 1，舊的資料庫就會拒絕啟動並提醒重建；備份後刪掉 `data/school.db`，下次啟動會建立新的（會清空資料，可以用 `python -m app.manage seed` 補假資料）。

## 學號怎麼接到各個 endpoint

學號的來源集中在 `app/dependencies.py` 的 `get_student_id()`：有 `X-Student-ID` header 就直接用，沒有就從 `session` cookie 找登入紀錄。之後要開放給不信任的使用者，把 header 那段拿掉、只留 cookie 就好；要改成學校的單一登入（SSO），只要改 `app/routers/auth.py` 的登入流程，各個 endpoint 都不用動。
