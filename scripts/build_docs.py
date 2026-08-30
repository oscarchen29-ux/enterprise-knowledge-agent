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

from pypdf import PdfReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs_source")
OUT = os.path.join(ROOT, "docs")
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

        # 依年級整理一份,讓「大三必修有哪些」這種問法可以直接命中。
        by_year = {}
        for m in required:
            by_year.setdefault(m.group("year"), []).append(
                f"{_chinese_name(m.group('name'))}({m.group('credits')}學分)"
            )
        out.append("【系必修依開課年級整理】")
        for y in YEAR_ORDER:
            if y in by_year:
                grade = {"一": "大一", "二": "大二", "三": "大三", "四": "大四"}[y[0]]
                sem = "上學期" if y[1] == "上" else "下學期"
                out.append(f"{y}（{grade}{sem}）：" + "、".join(by_year[y]))
        out.append("")

    out.append("【原始 PDF 全文（未經整理，供查核用）】")
    out.append(re.sub(r"\n{3,}", "\n\n", text).strip())
    return "\n".join(out) + "\n"


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

    # 1) 學士班五屆
    for cohort in ("111", "112", "113", "114", "115"):
        fn = f"資工系學士班必選修科目一覽表_{cohort}.pdf"
        src = os.path.join(SRC, fn)
        if not os.path.exists(src):
            print(f"  跳過(找不到) {fn}")
            continue
        body = build_catalog(src, fn, meta.get(fn), "學士班", cohort)
        written.append((f"資工系學士班必選修科目表_{cohort}.txt", body))

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
        body = build_catalog(src, fn, meta.get(fn), program, label)
        written.append((f"資工系{program}必選修科目表_{label}.txt", body))

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
        body = build_regulation(os.path.join(SRC, fn), fn, meta.get(fn), title)
        written.append((os.path.splitext(fn)[0] + ".txt", body))

    print(f"將產生 {len(written)} 個檔案")
    for name, body in written:
        size = len(body)
        print(f"  {name:<52} {size:>7} 字")
        if not args.dry_run:
            with io.open(os.path.join(OUT, name), "w", encoding="utf-8", newline="") as f:
                f.write(body)
    if args.dry_run:
        print("\n(--dry-run,未寫入任何檔案)")


if __name__ == "__main__":
    main()
