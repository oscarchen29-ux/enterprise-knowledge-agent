"""第四輪:以「資工系學生實際會問什麼」為準,補系上課程相關文件。

系網 /course 底下的 minor、doublemajor、map 三個子頁都有文件,先前只掃了
bachelor/master/doctor 而漏掉。

課程地圖有 100~115 共 14 屆,只收目前在學學士生涵蓋的 112-115;更早的屆別
早已畢業,收進來只會增加檢索雜訊。雙主修科目表最新僅到 111,取 110、111 兩屆。
"""

C = "https://www.csie.ncnu.edu.tw/coursedata"
U = "https://www.csie.ncnu.edu.tw/upload"

SOURCES = [
    # 輔系
    (f"{C}/%E8%B3%87%E5%B7%A5%E7%B3%BB-115%E8%B5%B7%E8%BC%94%E7%B3%BB%E5%BF%85%E9%81%B8"
     "%E4%BF%AE%E7%A7%91%E7%9B%AE%E4%B8%80%E8%A6%BD%E8%A1%A8.pdf",
     "資工系輔系必選修科目一覽表_115起.pdf"),
    (f"{C}/108%E9%96%8B%E5%A7%8B%E8%BC%94%E7%B3%BB%E5%BF%85%E9%81%B8%E4%BF%AE%E7%A7%91"
     "%E7%9B%AE%E4%B8%80%E8%A6%BD%E8%A1%A8%E6%A0%BC%E5%BC%8F(%E4%B8%AD%E8%8B%B1%E7%89%88).pdf",
     "資工系輔系必選修科目一覽表_108-114.pdf"),

    # 雙主修(系網最新僅到 111)
    (f"{C}/%E9%9B%99%E4%B8%BB%E4%BF%AE/111%E9%9B%99%E4%B8%BB%E4%BF%AE%E5%BF%85%E9%81%B8"
     "%E4%BF%AE%E7%A7%91%E7%9B%AE%E4%B8%80%E8%A6%BD%E8%A1%A8.pdf",
     "資工系雙主修必選修科目一覽表_111.pdf"),
    (f"{C}/%E9%9B%99%E4%B8%BB%E4%BF%AE/110%E9%9B%99%E4%B8%BB%E4%BF%AE%E5%BF%85%E9%81%B8"
     "%E4%BF%AE%E7%A7%91%E7%9B%AE%E4%B8%80%E8%A6%BD%E8%A1%A8.pdf",
     "資工系雙主修必選修科目一覽表_110.pdf"),

    # 課程地圖 —— 回答「想走某個方向該修哪些課」
    (f"{U}/115%E6%9A%A8%E5%A4%A7%E8%B3%87%E5%B7%A5%E7%B3%BB%E8%AA%B2%E7%A8%8B%E5%9C%B0"
     "%E5%9C%96%E4%BF%AE%E6%AD%A31150814.pdf", "資工系課程地圖_115.pdf"),
    (f"{U}/114%E6%9A%A8%E5%A4%A7%E8%B3%87%E5%B7%A5%E7%B3%BB%E8%AA%B2%E7%A8%8B%E5%9C%B0"
     "%E5%9C%96.pdf", "資工系課程地圖_114.pdf"),
    (f"{U}/113%E6%9A%A8%E5%A4%A7%E8%B3%87%E5%B7%A5%E7%B3%BB%E8%AA%B2%E7%A8%8B%E5%9C%B0"
     "%E5%9C%96%E4%BF%AE%E6%AD%A3.pdf", "資工系課程地圖_113.pdf"),
    (f"{U}/112%E6%9A%A8%E5%A4%A7%E8%B3%87%E5%B7%A5%E7%B3%BB%E8%AA%B2%E7%A8%8B%E5%9C%B0"
     "%E5%9C%96.pdf", "資工系課程地圖_112.pdf"),
]

if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fetch import fetch
    ok = sum(fetch(u, n) for u, n in SOURCES)
    print(f"\n成功 {ok}/{len(SOURCES)}")
