"""資料庫管理介面（Flask）：查看、修改學生資料、登入帳號、獎懲紀錄和通行證申請

    python -m admin        # 開在 http://127.0.0.1:7001

沒有登入機制，所以只綁本機，區網連不到。要從別台電腦用，開 SSH 通道再用瀏覽器打開 http://127.0.0.1:7001：

    ssh -L 7001:127.0.0.1:7001 <帳號>@<伺服器>
"""

from admin.web import app

HOST, PORT = "127.0.0.1", 7001

if __name__ == "__main__":
    print(f"\n  管理介面：http://{HOST}:{PORT}（只有這台電腦連得到）\n")
    app.run(host=HOST, port=PORT)
