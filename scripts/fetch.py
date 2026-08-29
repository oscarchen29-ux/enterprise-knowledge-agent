"""下載官方 PDF 並登記出處。用 Python 而非 shell,因為 Git Bash 在 Windows 下
會把 heredoc 裡的中文檔名寫壞(曾把「教育」寫成「教灶」)。"""
import hashlib, io, os, sys, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs_source")
MAN = os.path.join(SRC, "MANIFEST.tsv")

def fetch(url, name):
    os.makedirs(SRC, exist_ok=True)
    path = os.path.join(SRC, name)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=60).read()
    except Exception as e:
        print(f"FAIL  {name}  ({type(e).__name__}: {e})"); return False
    if not data.startswith(b"%PDF"):
        print(f"FAIL  {name}  (不是 PDF,前 4 bytes={data[:4]!r})"); return False
    with open(path, "wb") as f:
        f.write(data)
    with io.open(MAN, "a", encoding="utf-8", newline="") as f:
        f.write("\t".join([name, url,
                           datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                           hashlib.sha256(data).hexdigest()[:16], str(len(data))]) + "\n")
    print(f"OK    {name}  ({len(data)}B)")
    return True

if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, name = line.split("|", 1)
        fetch(url.strip(), name.strip())
