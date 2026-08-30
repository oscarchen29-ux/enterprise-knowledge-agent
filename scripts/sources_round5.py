"""第五輪:住宿服務組與衛生保健組。

目標是「資工系學生問系上或學校的事都查得到」,而宿舍是學生最常問卻完全沒被
涵蓋的一塊 —— 床位怎麼申請、住宿費怎麼退、寒暑假能不能住、可不可以帶小冰箱。

住宿組的法規頁只列標題,每個標題連到一個內頁,PDF 掛在內頁裡,所以要追兩層。
衛保組則是直接掛 downloadfile 連結。

非本校訂定的中央法規(學校衛生法及其施行細則等)不收,維持知識庫只放本校規定
的一致性;但由本校轉載、直接規範學生的(健康檢查、緊急傷病處理)則收。
"""

import re
import urllib.request

HOUSING_INDEX = "https://housing.ncnu.edu.tw/p/412-1081-109.php?Lang=zh-tw"
HEALTH_INDEX = "https://health.ncnu.edu.tw/p/406-1077-593,r39.php?Lang=zh-tw"

# 中央法規,非本校訂定,不納入
SKIP_TITLES = ("學校衛生法", "學校餐廳廚房員生消費合作社衛生管理辦法",
               "教育部112年大專校院弱勢學生助學計畫")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "ignore")


def housing_targets():
    """住宿組:法規頁 -> 各內頁 -> 內頁裡的 PDF。"""
    index = get(HOUSING_INDEX)
    subs = re.findall(r'href="(/p/405-1081-\d+,c109\.php\?Lang=zh-tw)"[^>]*>\s*([^<]{6,60})', index)
    seen, out = set(), []
    for path, title in subs:
        title = " ".join(title.split()).split("National")[0].split("Guidelines")[0].strip()
        if title in seen or any(s in title for s in SKIP_TITLES):
            continue
        seen.add(title)
        page = get("https://housing.ncnu.edu.tw" + path)
        m = re.search(r'href="([^"]*Action=downloadfile[^"]*)"', page)
        if not m:
            print(f"  (內頁沒有附件) {title}")
            continue
        url = m.group(1).replace("&amp;", "&")
        if url.startswith("/"):
            url = "https://housing.ncnu.edu.tw" + url
        name = title.replace("國立暨南國際大學", "").strip() or title
        out.append((url, f"{name}.pdf"))
    return out


def health_targets():
    index = get(HEALTH_INDEX)
    pairs = re.findall(r'href="([^"]*Action=downloadfile[^"]*)"[^>]*>\s*([^<]{6,60}\.pdf)', index)
    out = []
    for url, title in pairs:
        title = " ".join(title.split())
        if any(s in title for s in SKIP_TITLES):
            continue
        url = url.replace("&amp;", "&")
        if url.startswith("/"):
            url = "https://health.ncnu.edu.tw" + url
        name = title.replace("國立暨南國際大學", "").strip()
        out.append((url, name if name.endswith(".pdf") else name + ".pdf"))
    return out


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fetch import fetch

    targets = []
    print("=== 住宿服務組 ===")
    try:
        targets += housing_targets()
    except Exception as e:
        print("  住宿組列舉失敗:", e)
    print("=== 衛生保健組 ===")
    try:
        targets += health_targets()
    except Exception as e:
        print("  衛保組列舉失敗:", e)

    print(f"\n共 {len(targets)} 個目標\n")
    ok = sum(fetch(u, n) for u, n in targets)
    print(f"\n成功 {ok}/{len(targets)}")
