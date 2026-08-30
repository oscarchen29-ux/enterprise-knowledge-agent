"""住宿服務組法規。

宿舍是學生最常問、原本知識庫完全沒涵蓋的一塊:床位怎麼申請、住宿費怎麼退、
寒暑假能不能住、可不可以帶小冰箱、冷氣怎麼算錢。

住宿組的法規索引頁是 JS 渲染的(curl 抓不到連結),但各法規的內頁是伺服器
渲染,PDF 連結就在 HTML 裡。因此這裡直接列出內頁編號,由程式追進去取附件,
不必把那些帶著長 base64 參數的下載網址硬寫進原始碼。
"""

import re
import urllib.request

BASE = "https://housing.ncnu.edu.tw"

# (內頁編號, 存檔名) —— 編號取自法規索引頁
PAGES = [
    (1154,  "學生住宿輔導辦法"),
    (1153,  "學生宿舍公約及違規處理要點"),
    (23527, "學生入住宿舍性別友善處理要點"),
    (18345, "學生宿舍床位申請作業要點"),
    (1144,  "學生宿舍住宿費收退費標準"),
    (17917, "寒暑假學生宿舍管理實施細則"),
    (1143,  "大學部學生宿舍冷暖氣設備管理要點"),
    (1151,  "研究生學生宿舍冷氣設備管理要點"),
    (1145,  "研究生學生宿舍自備小冰箱申請要點"),
    (1142,  "大學部學生宿舍簡易廚房使用管理規定"),
    (1146,  "學生宿舍申復評議委員會設置規定"),
    (1148,  "學生宿舍修繕標準作業流程"),
    (1149,  "學生宿舍自治幹部組織及評分要點"),
    (1150,  "學生宿舍公益服務生積點規則"),
]


def pdf_url(page_id):
    url = f"{BASE}/p/405-1081-{page_id},c109.php?Lang=zh-tw"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "ignore")
    m = re.search(r'href="([^"]*Action=downloadfile[^"]*)"', html)
    if not m:
        return None
    link = m.group(1).replace("&amp;", "&")
    return link if link.startswith("http") else BASE + link


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fetch import fetch

    ok = 0
    for page_id, name in PAGES:
        try:
            url = pdf_url(page_id)
        except Exception as e:
            print(f"FAIL  {name}  (內頁讀取失敗: {e})"); continue
        if not url:
            print(f"FAIL  {name}  (內頁沒有附件連結)"); continue
        ok += fetch(url, f"{name}.pdf")
    print(f"\n成功 {ok}/{len(PAGES)}")
