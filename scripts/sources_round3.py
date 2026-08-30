"""第三輪:學務處法規彙編(學生手冊)與第一輪漏掉的獎補助文件。

學務處的「法規彙編」頁實際上只掛一份《學生手冊》PDF —— 那份是全校學生相關
規定的彙整本,對「學生實際會問什麼」的覆蓋率可能比逐條法規還高。

其餘是第一輪掃 assistance 法規一覽時列出、但當時沒有下載的項目。
非本校發布的文件(內政部租屋手冊等)不收,維持「來源必須是本校官方頁面」的標準。
"""

A = "https://assistance.ncnu.edu.tw/var/file/79/1079/img"

SOURCES = [
    ("https://b027.ncnu.edu.tw/var/file/41/1041/img/717380239.pdf",
     "學務處學生手冊.pdf"),

    (f"{A}/151/412537027.pdf", "中台禪寺獎助學金實施要點.pdf"),
    (f"{A}/151/759984810.pdf", "教育部學產基金設置急難慰問金實施要點.pdf"),
    (f"{A}/151/166290922.pdf", "大學校院各院系減免學雜費金額標準表.pdf"),
    (f"{A}/411825667.pdf", "大專校院弱勢學生助學計畫.pdf"),
]

if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fetch import fetch
    ok = sum(fetch(u, n) for u, n in SOURCES)
    print(f"\n成功 {ok}/{len(SOURCES)}")
