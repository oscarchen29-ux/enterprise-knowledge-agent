"""語文教學中心(ltrc.ncnu.edu.tw)法規表單頁上的官方 PDF。

清單寫在 Python 原始碼裡而不是用 shell 傳參,因為 Git Bash 在 Windows 下
會把命令列與 stdin 的中文字弄成 surrogate,寫檔名時直接炸掉。
"""

L = "https://ltrc.ncnu.edu.tw/uploads/root/hswang"

SOURCES = [
    (f"{L}/07.%E5%9C%8B%E7%AB%8B%E6%9A%A8%E5%8D%97%E5%9C%8B%E9%9A%9B%E5%A4%A7%E5%AD%B8"
     "%E5%AD%B8%E5%A3%AB%E7%8F%AD%E5%AD%B8%E7%94%9F%E7%95%A2%E6%A5%AD%E5%A4%96%E8%AA%9E"
     "%E8%83%BD%E5%8A%9B%E5%9F%BA%E6%9C%AC%E8%A6%81%E6%B1%82%E5%AF%A6%E6%96%BD%E8%A6%81"
     "%E9%BB%9E1150610%E4%BF%AE%E6%AD%A3.pdf",
     "學士班學生畢業外語能力基本要求實施要點_1150610修正.pdf"),

    (f"{L}/11.%E5%9C%8B%E7%AB%8B%E6%9A%A8%E5%8D%97%E5%9C%8B%E9%9A%9B%E5%A4%A7%E5%AD%B8"
     "%E5%AD%B8%E7%94%9F%E7%AC%AC%E4%BA%8C%E5%A4%96%E5%9C%8B%E8%AA%9E%E8%83%BD%E5%8A%9B"
     "%E6%AA%A2%E6%B8%AC%E7%8D%8E%E5%8B%B5%E8%A6%81%E9%BB%9E1120418.pdf",
     "學生第二外國語能力檢測獎勵要點_1120418.pdf"),

    (f"{L}/10.%E5%9C%8B%E7%AB%8B%E6%9A%A8%E5%8D%97%E5%9C%8B%E9%9A%9B%E5%A4%A7%E5%AD%B8"
     "%E5%AD%B8%E7%94%9F%E5%8F%83%E8%88%87%E8%8B%B1%E8%AA%9E%E8%83%BD%E5%8A%9B%E6%AA%A2"
     "%E6%B8%AC%E7%8D%8E%E5%8B%B5%E8%A6%81%E9%BB%9E1150106%E4%BF%AE%E6%AD%A3.pdf",
     "學生參與英語能力檢測獎勵要點_1150106修正.pdf"),

    (f"{L}/01.%E5%9C%8B%E7%AB%8B%E6%9A%A8%E5%8D%97%E5%9C%8B%E9%9A%9B%E5%A4%A7%E5%AD%B8"
     "%E8%8B%B1%E6%96%87%E5%AD%B8%E7%BF%92%E6%AD%B7%E7%A8%8B%E5%AF%A6%E6%96%BD%E8%A6%81"
     "%E9%BB%9E1150610%E4%BF%AE%E6%AD%A3.pdf",
     "英文學習歷程實施要點_1150610修正.pdf"),
]

if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fetch import fetch
    for url, name in SOURCES:
        fetch(url, name)
