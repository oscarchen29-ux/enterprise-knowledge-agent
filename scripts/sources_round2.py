"""第二輪蒐集:招生組獎助學金、課務組抵免作業、系上表單。

第一輪只掃了少數幾頁,漏掉不少。這一輪補上:
- admission.ncnu.edu.tw 獎助學金頁(碩士班優秀學生獎勵辦法在這裡,而且分屆別)
- curriculum.ncnu.edu.tw 115學年度申請抵免學分公告(作業日程與申請流程)
- www.csie.ncnu.edu.tw/regulation 系上表單中的 PDF
"""

ADM = "https://admission.ncnu.edu.tw/var/file/55/1055/img"
CUR = "https://curriculum.ncnu.edu.tw/app/index.php?Action=downloadfile&file="
CSIE = "https://www.csie.ncnu.edu.tw/upload"

SOURCES = [
    # 招生組 —— 獎勵辦法依入學學年度分版本,與科目表同樣的分屆模式
    (f"{ADM}/331646904.pdf", "碩士班優秀學生獎勵辦法_112學年度含以前入學適用.pdf"),
    (f"{ADM}/230255827.pdf", "碩士班優秀學生獎勵辦法_113學年度起入學適用.pdf"),
    (f"{ADM}/396547111.pdf", "學士班新生入學獎勵辦法_114學年度含以前入學適用.pdf"),
    (f"{ADM}/368982119.pdf", "學士班新生入學獎勵辦法_115學年度起入學適用.pdf"),

    # 課務組 —— 抵免的實際作業日程與流程(抵免辦法本文仍未在官網找到)
    (CUR + "WVhSMFlXTm9MekV6TDNCMFlWOHlOakl4TUY4M01UZ3pNamN3WHpNd01UYzRMbkJrWmc9PQ==",
     "115學年度抵免學分作業日程公告.pdf"),
    (CUR + "WVhSMFlXTm9MekV6TDNCMFlWOHlOakl4TVY4eU5qTXlNRFF5WHpNd01UYzRMbkJrWmc9PQ==",
     "新世代校務系統抵免學分申請流程_114起適用.pdf"),

    # 系上表單
    (f"{CSIE}/%E5%AD%B8%E7%A2%A9%E5%8D%9A%E4%BF%AE%E7%BF%92%E5%AD%B8%E5%88%86"
     "%E7%A2%BA%E8%AA%8D%E5%96%AE20210220.pdf",
     "資工系學碩博修習學分確認單_20210220.pdf"),
    (f"{CSIE}/%E6%9A%A8%E5%8D%97%E5%A4%A7%E5%AD%B8%E8%B3%87%E8%A8%8A%E5%B7%A5%E7%A8%8B"
     "%E5%AD%B8%E7%B3%BB%E7%A8%8B%E5%BC%8F%E7%AB%B6%E8%B3%BD%E7%B3%BB%E9%9A%8A"
     "%E7%94%B3%E8%AB%8B%E8%BE%A6%E6%B3%95.pdf",
     "資工系程式競賽系隊申請辦法及申請表.pdf"),
    (f"{CSIE}/%E8%B3%87%E5%B7%A5%E7%B3%BB%E8%8B%B1%E6%96%87%E7%8D%8E%E5%8B%B5"
     "%E8%BE%A6%E6%B3%95%E7%94%B3%E8%AB%8B%E8%A1%A820190221.pdf",
     "資工系英文獎勵辦法申請表_20190221.pdf"),
    (f"{CSIE}/%E5%AD%B8%E5%A3%AB%E7%8F%AD%E6%9B%B4%E6%8F%9B%E5%B0%8E%E5%B8%AB"
     "%E7%94%B3%E8%AB%8B%E8%A1%A81110221.pdf",
     "資工系學士班更換導師申請表_1110221.pdf"),
]

if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fetch import fetch
    ok = sum(fetch(u, n) for u, n in SOURCES)
    print(f"\n成功 {ok}/{len(SOURCES)}")
