"""把 docs_source/ 的官方 PDF 轉成 docs/ 的純文字知識庫。

為什麼要有這支腳本:
原本的 docs/ 是手工摘要,出處只有檔案裡自己打的一行「來源：xxx.ncnu.edu.tw」,
沒有網址、沒有下載日期、沒有雜湊,無法驗證。而且手工壓平表格時整欄消失過 ——
學士班科目表的「開課年級」在轉檔時被丟掉,導致「大三必修有哪些」這類問題
在架構上就無解,再好的檢索也救不回來。

這支腳本讓「PDF 原始檔 -> 知識庫文字」的過程可重現、可稽核:
每個輸出檔都帶有來源檔名、原始網址、下載時間與 SHA-256 前綴。

用法:
    python scripts/build_docs.py            # 全部重建
    python scripts/build_docs.py --dry-run  # 只看會產生什麼,不寫檔
"""

import argparse
import csv
import io
import os
import re
import sys
import unicodedata

from pypdf import PdfReader

# Windows 主控台預設 cp950,遇到 CJK 相容字(例如 U+F9F7)會直接拋
# UnicodeEncodeError 中斷整個轉檔。輸出一律走 UTF-8,無法表示的字以替代字元帶過。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs_source")
OUT = os.path.join(ROOT, "docs")
# 科目表的 PDF 原文以前直接附在 docs/ 的檔尾供查核,但 docs/ 就是索引的輸入,
# 那段原文因此也被切進索引:9 個檔共 91,850 字、193 塊,佔全部 988 塊的 20%。
# 內容是雙語表格被壓平後的字串湯(欄名與數值分家),模型讀不懂,卻會吃掉前 k 名。
# 改成寫到 docs_raw/:檔案仍在、仍可逐字查核,但 tools.py 只讀 docs/,不進索引。
RAW_OUT = os.path.join(ROOT, "docs_raw")
MANIFEST = os.path.join(SRC, "MANIFEST.tsv")

# 課程列。(R)/(E) 標記只有 113 學年度以後的版本才有,故設為選擇性;
# 課名短的話代號與課名同一行,課名長的話代號自成一行、英文名跨兩三行,
# 所以 name 用非貪婪跨行比對,再由 _chinese_name() 濾掉英文行。
COURSE_RE = re.compile(
    r"(?P<code>\d{6})\s*(?P<name>.*?)"
    r"(?P<credits>\d(?:\.\d)?)\s*(?P<type>必|選)\s*(?:\([RE]\)\s*)?"
    r"(?P<year>[一二三四][上下])",
    re.S,
)

OVERVIEW_RE = re.compile(
    r"最低畢業學分[：:]\s*(?P<total>\d+)\s*學分[，,]\s*"
    r"全校共同課程\s*(?P<common>\d+)\s*學分[，,]\s*"
    r"通識領域課程\s*(?P<general>\d+)\s*學分"
)
DEPT_RE = re.compile(r"系必修\s*(\d+)\s*學分")
ELECTIVE_RE = re.compile(r"專業選修\s*(\d+)\s*學分")
FREE_RE = re.compile(r"自由學分\s*(\d+)\s*學分")
COHORT_RE = re.compile(r"本表\s*[（(]?\s*(\d{3})\s*[）)]?\s*學年度")

YEAR_ORDER = ["一上", "一下", "二上", "二下", "三上", "三下", "四上", "四下"]


def load_manifest():
    """讀取出處清單,回傳 {檔名: {網址, 下載時間, sha}}。"""
    info = {}
    if not os.path.exists(MANIFEST):
        return info
    with io.open(MANIFEST, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            info[row["檔名"]] = row
    return info


def pdf_text(path):
    return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)


def _chinese_name(raw):
    """課名欄可能夾雜英文譯名與換行,只留中文行。"""
    lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
    zh = [l for l in lines if re.search(r"[一-鿿]", l)]
    return "".join(zh) if zh else (lines[0] if lines else "")


def provenance_block(filename, meta):
    lines = ["【本檔案出處】", f"來源檔案：docs_source/{filename}"]
    if meta:
        lines += [
            f"原始網址：{meta['來源網址']}",
            f"下載時間：{meta['下載時間']}",
            f"SHA-256：{meta['SHA256']}...",
        ]
    else:
        lines.append("原始網址：(MANIFEST.tsv 中查無此檔)")
    lines.append("轉檔工具：scripts/build_docs.py")
    return "\n".join(lines)


def build_catalog(path, filename, meta, program, cohort_label):
    """學士/碩士/博士班必選修科目一覽表 -> 保留課程代號、學分、開課年級。"""
    text = pdf_text(path)
    flat = re.sub(r"\s+", " ", text)

    cohort = COHORT_RE.search(flat)
    cohort_text = cohort.group(1) if cohort else cohort_label

    out = [
        f"國立暨南國際大學資訊工程學系{program}必選修科目一覽表"
        f"（{cohort_label}學年度入學新生適用）",
        "",
        f"【適用對象】{cohort_label} 學年度入學新生。"
        f"本系每一入學年度各發布一份科目表,不同屆別的規定可能不同,查詢時務必確認入學學年度。",
        "",
        provenance_block(filename, meta),
        "",
    ]

    ov = OVERVIEW_RE.search(flat)
    if ov:
        dept = DEPT_RE.search(flat)
        elec = ELECTIVE_RE.search(flat)
        free = FREE_RE.search(flat)
        out += [
            "【畢業學分總覽】",
            f"最低畢業學分：{ov.group('total')} 學分",
            f"- 全校共同課程：{ov.group('common')} 學分",
            f"- 通識領域課程：{ov.group('general')} 學分",
        ]
        if dept:
            out.append(f"- 系必修：{dept.group(1)} 學分")
        if elec:
            out.append(f"- 專業選修：{elec.group(1)} 學分")
        if free:
            out.append(f"- 自由學分：{free.group(1)} 學分")
        out.append("")

    required = [m for m in COURSE_RE.finditer(text) if m.group("type") == "必"]
    if required:
        total = sum(float(m.group("credits")) for m in required)
        out.append(f"【系必修科目（共 {len(required)} 門、{total:g} 學分，含開課年級）】")
        out.append("課程代號　課程名稱　學分　開課年級")
        for m in required:
            out.append(
                f"{m.group('code')}　{_chinese_name(m.group('name'))}　"
                f"{m.group('credits')}學分　{m.group('year')}"
            )
        out.append("")

        # 依年級整理,讓「大三必修有哪些」這種問法可以直接命中。
        #
        # 每個年級各自成段而不是全部列在一起:檢索是切塊比對的,四個年級擠在同一塊
        # 時,查「大三」會被其他年級的內容稀釋 —— 實測排到第 14 名,輸給只是檔名
        # 有「資工系」的課程地圖。拆開之後每一塊都短而集中,該年級的關鍵字密度才高。
        by_year = {}
        for m in required:
            by_year.setdefault(m.group("year"), []).append(
                f"{_chinese_name(m.group('name'))}({m.group('credits')}學分)"
            )
        # 年級稱呼要看學制。科目表裡的「一上」在學士班是大一上,在碩士班是碩一上、
        # 博士班是博一上 —— 一律叫「大一」的話,博士班文件會去搶「大一必修」這種
        # 明顯是問學士班的查詢(實測博士班科目表確實排到第一)。
        prefix = {"學士班": "大", "碩士班": "碩", "博士班": "博"}.get(program, "大")
        grades = {n: f"{prefix}{n}" for n in ("一", "二", "三", "四")}
        for grade_key, grade_name in grades.items():
            years = [y for y in YEAR_ORDER if y.startswith(grade_key) and y in by_year]
            if not years:
                continue
            # 標題與每一行都帶上系名與屆別。塊是被單獨檢索出來餵給模型的,
            # 不能假設它看得到檔名 —— 而且不寫進內文的話,查「資工系大三必修」時
            # 這一塊只命中「大三」「必修」,會輸給 PDF 圖表被抽成關鍵字湯的課程地圖
            # (那一塊「大一大二大三大四必修」全都有,命中面積大但沒有實質內容)。
            out.append(
                f"【{grade_name}必修科目】資訊工程學系（資工系）{program}"
                f"，{cohort_label} 學年度入學新生適用"
            )
            for y in years:
                sem = "上學期" if y[1] == "上" else "下學期"
                out.append(
                    f"資工系{grade_name}{sem}（{y}）必修：" + "、".join(by_year[y])
                )
            total = sum(
                float(m.group("credits")) for m in required
                if m.group("year").startswith(grade_key)
            )
            out.append(f"{grade_name}必修合計 {total:g} 學分。")
            out.append("")

    out.append(
        "【原始 PDF 全文】未經整理的 PDF 逐字內容另存於 docs_raw/,不納入檢索索引。"
    )
    raw = "\n".join([
        f"{filename} 的 PDF 逐字內容(未經整理,僅供查核)",
        "",
        re.sub(r"\n{3,}", "\n\n", text).strip(),
    ]) + "\n"
    return "\n".join(out) + "\n", raw


def build_handbook(path, filename, meta):
    """《學務處學生手冊》236 頁、16 萬字,是現有最大文件(學則)的 9 倍。

    整份當成一個檔案會壟斷檢索 —— 密度排序下它要嘛永遠命中、要嘛永遠沉底,
    兩種都沒用。因此依手冊自己的「篇」切開,篇內再以約 12 頁為一塊分段,
    讓每塊的長度落在跟其他法規文件相當的量級。

    回傳 [(輸出檔名, 內容), ...]。
    """
    reader = PdfReader(path)
    pages = [(i + 1, reader.pages[i].extract_text() or "") for i in range(len(reader.pages))]

    # 找出各篇起始頁。前幾頁是目錄,會連續出現多個篇名,取最後一次出現者為準。
    marks = []
    for pno, text in pages:
        m = re.search(r"([一-鿿]{2,8}篇)", text)
        if m and (not marks or marks[-1][1] != m.group(1)):
            marks.append((pno, m.group(1)))
    starts = []
    for pno, name in marks:
        starts = [(p, n) for p, n in starts if n != name]
        starts.append((pno, name))
    starts.sort()

    sections = []
    for idx, (pno, name) in enumerate(starts):
        end = starts[idx + 1][0] - 1 if idx + 1 < len(starts) else len(pages)
        if end - pno >= 3:      # 略過目錄裡的零星出現
            sections.append((name, pno, end))

    out = []
    CHUNK = 12
    for name, first, last in sections:
        for start in range(first, last + 1, CHUNK):
            stop = min(start + CHUNK - 1, last)
            body = "\n".join(t for p, t in pages if start <= p <= stop)
            body = re.sub(r"[ \t]+", " ", body)
            body = re.sub(r"\n{3,}", "\n\n", body).strip()
            if len(body) < 200:
                continue
            head = "\n".join([
                f"國立暨南國際大學學務處學生手冊 —— {name}（第 {start}-{stop} 頁）",
                "",
                f"【說明】本檔案為《學務處學生手冊》的一部分。原始 PDF 共 {len(pages)} 頁、"
                f"約 {sum(len(t) for _, t in pages) // 1000} 千字,依手冊自身的分篇與頁段切開,"
                "避免單一超長文件壓過其他文件的檢索排序。完整內容請見原始 PDF。",
                "",
                provenance_block(filename, meta),
                "",
                "【內容】",
            ])
            out.append((f"學務處學生手冊_{name}_p{start}-{stop}.txt", head + "\n" + body + "\n"))
    return out


# 學雜費一覽表是四欄的表格。PDF 直接抽文字時欄名與數值會分家 ——「資訊工程學系」
# 落在第一段、「學分費 1,636」落在第二段,中間隔著三十幾個別系的系名,任何切法都
# 不可能讓兩者同塊,所以「資工系學分費多少」在架構上無解(實測 BM25 排第 34~57 名)。
#
# pypdf 的 layout 模式會保留欄位對齊,四個欄群因此可以明確認定(下面的名單即由
# layout 輸出逐欄讀出,並在轉檔時逐一驗證確實存在於 PDF 原文)。
# 注意:layout 模式只適合表格。同一份 PDF 的第 4 頁是散文,layout 模式反而會把
# 「（1）97 學年度以前入學學生」拆開與別行交錯,所以散文部分仍走一般抽取。
TUITION_COLUMNS = [
    ("科技學院",
     ["資訊工程學系", "土木工程學系", "電機工程學系", "應用化學系",
      "應用材料及光電工程學系", "科技學院學士班", "人工智慧與機器人碩士學位學程",
      "智慧精準農業產學研發博士學位學程", "光電材料產業碩士專班",
      "智慧半導體及綠色科技國際碩士學位學程", "資訊管理學系（比照科技學院標準計收）",
      "智慧暨永續農業學士學位學程"]),
    ("管理學院",
     ["經濟學系", "國際企業學系", "財務金融學系", "觀光休閒與餐旅管理學系",
      "管理學院學士班", "新興產業策略與發展碩士學位學程", "區域發展重點產業碩士專班",
      "新興產業策略與發展博士學位學程",
      "管理學院商業管理及資訊科技創新應用全英語碩士學位學程"]),
    ("人文學院、教育學院、水沙連學院",
     ["中國語文學系", "社會政策與社會工作學系", "外國語文學系", "歷史學系",
      "公共行政與政策學系", "東南亞學系", "原住民文化產業與社會工作學士學位學程原住民族專班",
      "華語文教學碩士學位學程", "文化創意與社會行銷碩士學位學程",
      "國際文教與比較教育學系", "教育政策與行政學系", "諮商心理與人力資源發展學系",
      "教育學院學士班", "課程教學與科技研究所", "國際文教管理人才博士學位學程",
      "地方創生與跨域治理碩士學位學程"]),
    ("護理暨健康福祉學院",
     ["護理學系", "護理學系原住民族專班",
      "高齡健康與長期照顧管理學士學位學程原住民族專班", "學士後護理學系"]),
]

_MONEY = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+|-)")


def _fee_row(layout_text, label, count=4):
    """從 layout 模式的表格文字裡抓一列的四個欄位值。找不到或欄數不符就回 None。"""
    for line in layout_text.splitlines():
        stripped = re.sub(r"\s+", " ", line).strip()
        if label not in stripped:
            continue
        values = _MONEY.findall(stripped.split(label, 1)[1])
        if len(values) == count:
            return values
    return None


def build_tuition(path, filename, meta, title):
    """學雜費收費標準 -> 每一列自帶欄名(哪個學院、哪個學制、哪個費目)。"""
    reader = PdfReader(path)
    flat = pdf_text(path)
    domestic = reader.pages[0].extract_text(extraction_mode="layout")
    foreign = reader.pages[2].extract_text(extraction_mode="layout")

    # 名單是人工從 layout 輸出讀出的,必須逐一確認真的在 PDF 裡,
    # 否則哪天學校改版、系所增刪,這份檔案會安靜地留著錯的內容。
    flat_nospace = re.sub(r"\s+", "", flat)
    for _, members in TUITION_COLUMNS:
        for member in members:
            bare = re.sub(r"（.*?）", "", member)
            if bare not in flat_nospace:
                raise SystemExit(f"[學雜費] 系所名稱「{bare}」不在 PDF 原文中,名單需重新核對")

    out = [title, "", provenance_block(filename, meta), "", "【條文】"]

    # 每一項寫成 (顯示名稱, 表中標籤, 第幾次出現)。同一個標籤會在表裡出現多次 ——
    # 「學分費」學士班一次、碩博士班一次;外國學生表的「學雜費基數」碩士班一次、
    # 博士班一次 —— 不指定第幾次的話兩者會拿到同一列(博士班曾被寫成碩士班的數字)。
    for scope, layout, grades in [
        ("本國學生及僑生（日間學制）", domestic,
         [("學士班", [("學費", "學費", 0), ("雜費", "雜費", 0),
                    ("全額學雜費總計", "總計", 0),
                    ("延長修業年限學分費（每學分）", "學分費", 0)]),
          ("碩士班、博士班", [("學雜費基數", "學雜費基數", 0),
                        ("學分費（每學分）", "學分費", 1)])]),
        ("100 學年度以後入學外國學生及大陸地區學生", foreign,
         [("學士班", [("學費", "學費", 0), ("雜費", "雜費", 0),
                    ("全額學雜費總計", "總計", 0)]),
          ("碩士班", [("學雜費基數", "學雜費基數", 0), ("學分費（每學分）", "學分費", 0)]),
          ("博士班", [("學雜費基數", "學雜費基數", 1), ("學分費（每學分）", "學分費", 1)])]),
    ]:
        rows = {key: _all_fee_rows(layout, key)
                for key in ("學費", "雜費", "總計", "學分費", "學雜費基數")}

        for column, (college, members) in enumerate(TUITION_COLUMNS):
            for grade, items in grades:
                lines = []
                for label, key, nth in items:
                    values = rows.get(key) or []
                    if nth >= len(values):
                        continue
                    pick = values[nth]
                    if pick[column] == "-":
                        continue
                    lines.append(f"{label} {pick[column]} 元")
                if not lines:
                    continue
                out.append("")
                out.append(f"【{college} {grade} 學雜費收費標準】{scope}")
                out.append(f"適用系所：{'、'.join(members)}。")
                out.append("；".join(lines) + "。")

    # 學分費的算法寫在最後一頁的附註裡,跟數值隔了三頁。算式與結果放同一塊,
    # 「這個數字怎麼來的」才答得出來。
    base = _fee_row(domestic, "學費") or []
    fee = _fee_row(domestic, "雜費") or []
    credit = _fee_row(domestic, "學分費") or []
    if base and fee and credit:
        out.append("")
        out.append("【學士班學分費計算基準】")
        # 算式逐字照抄 PDF 附註(半形括號),避免同一條規定在庫裡出現兩種寫法。
        out.append("學士班學分費計算基準：(每學分)＝(學費＋雜費)×8(學期)÷128(學分)。"
                   "(89.08.31 第 132 次行政會議通過)")
        for column, (college, _) in enumerate(TUITION_COLUMNS):
            a = int(base[column].replace(",", ""))
            b = int(fee[column].replace(",", ""))
            out.append(
                f"{college}：({base[column]}＋{fee[column]})×8÷128 ＝ "
                f"{a + b:,}×8÷128 ≈ {(a + b) * 8 / 128:.1f}，"
                f"故學分費為每學分 {credit[column]} 元。"
            )

    # 在職專班與收費方式是散文/窄表,一般抽取就讀得通,原樣保留以免漏掉內容。
    tail = flat
    for marker in ("二、在職專班", "三、收費方式"):
        index = tail.find(marker)
        if index >= 0:
            tail = tail[index:]
            break
    out.append("")
    out.append("【在職專班收費標準與收費方式（原文）】")
    out.append(re.sub(r"\n{3,}", "\n\n", tail).strip())
    return "\n".join(out) + "\n"


def _all_fee_rows(layout_text, label):
    """同一個標籤可能在表中出現多次(學士班一次、碩博士班一次),全部回傳。"""
    found = []
    for line in layout_text.splitlines():
        stripped = re.sub(r"\s+", " ", line).strip()
        if label not in stripped:
            continue
        values = _MONEY.findall(stripped.split(label, 1)[1])
        if len(values) == 4:
            found.append(values)
    return found


def build_regulation(path, filename, meta, title):
    """法規類 PDF -> 條文文字。以「第N條」斷段,其餘壓成連續段落。"""
    text = pdf_text(path)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # 讓每一條自成一段,檢索片段時比較不會把兩條黏在一起
    text = re.sub(r"\s*(第\s*[一二三四五六七八九十百]+\s*條)", r"\n\n\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    return "\n".join([
        f"{title}",
        "",
        provenance_block(filename, meta),
        "",
        "【條文】",
        text,
    ]) + "\n"


# 檔名 -> (輸出檔名, 類型, 參數)
CATALOGS = {
    "資工系學士班必選修科目一覽表_%s.pdf": ("資工系學士班必選修科目表_%s.txt", "學士班"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    meta = load_manifest()
    os.makedirs(OUT, exist_ok=True)
    written = []
    raw_written = []

    # 1) 學士班五屆
    for cohort in ("111", "112", "113", "114", "115"):
        fn = f"資工系學士班必選修科目一覽表_{cohort}.pdf"
        src = os.path.join(SRC, fn)
        if not os.path.exists(src):
            print(f"  跳過(找不到) {fn}")
            continue
        body, raw = build_catalog(src, fn, meta.get(fn), "學士班", cohort)
        written.append((f"資工系學士班必選修科目表_{cohort}.txt", body))
        raw_written.append((f"資工系學士班必選修科目表_{cohort}.rawtext.txt", raw))

    # 2) 碩博士班
    for fn, program, label in [
        ("資工系碩士班必選修科目一覽表_115.pdf", "碩士班", "115"),
        ("資工系碩士班必選修科目一覽表_110-114.pdf", "碩士班", "110-114"),
        ("資工系博士班必選修科目一覽表_115.pdf", "博士班", "115"),
        ("資工系博士班必選修科目一覽表_110-114.pdf", "博士班", "110-114"),
    ]:
        src = os.path.join(SRC, fn)
        if not os.path.exists(src):
            continue
        body, raw = build_catalog(src, fn, meta.get(fn), program, label)
        written.append((f"資工系{program}必選修科目表_{label}.txt", body))
        raw_written.append((f"資工系{program}必選修科目表_{label}.rawtext.txt", raw))

    # 3) 其餘一律當法規處理
    handled = {w[0] for w in written}
    for fn in sorted(os.listdir(SRC)):
        if not fn.lower().endswith(".pdf"):
            continue
        if fn.startswith("資工系學士班必選修") or fn.startswith("資工系碩士班必選修") \
                or fn.startswith("資工系博士班必選修"):
            continue
        if fn == "學務處學生手冊.pdf":
            written.extend(build_handbook(os.path.join(SRC, fn), fn, meta.get(fn)))
            continue
        title = "國立暨南國際大學" + re.sub(r"_\d[\d.\-]*(修正|核定)?$", "",
                                       os.path.splitext(fn)[0])
        if fn == "學雜費收費標準.pdf":
            body = build_tuition(os.path.join(SRC, fn), fn, meta.get(fn), title)
            written.append(("學雜費收費標準.txt", body))
            continue
        body = build_regulation(os.path.join(SRC, fn), fn, meta.get(fn), title)
        written.append((os.path.splitext(fn)[0] + ".txt", body))

    # 部分官方 PDF 的標題含 CJK 相容字(U+F9F7 等),正規化成標準碼位,
    # 否則同一個字會有兩種表示,檔名比對與檢索都可能對不上。
    written = [(unicodedata.normalize("NFC", n), b) for n, b in written]

    raw_written = [(unicodedata.normalize("NFC", n), b) for n, b in raw_written]

    print(f"將產生 {len(written)} 個檔案(docs/)、{len(raw_written)} 個查核檔(docs_raw/)")
    for name, body in written:
        size = len(body)
        print(f"  {name:<52} {size:>7} 字")
        if not args.dry_run:
            with io.open(os.path.join(OUT, name), "w", encoding="utf-8", newline="") as f:
                f.write(body)
    if not args.dry_run and raw_written:
        os.makedirs(RAW_OUT, exist_ok=True)
    for name, body in raw_written:
        if not args.dry_run:
            with io.open(os.path.join(RAW_OUT, name), "w", encoding="utf-8", newline="") as f:
                f.write(body)
    if args.dry_run:
        print("\n(--dry-run,未寫入任何檔案)")


if __name__ == "__main__":
    main()
