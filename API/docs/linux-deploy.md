# 搬到 Linux

這份文件說明怎麼把 API 從 Windows 搬到 Linux 伺服器，以 Ubuntu 24.04 為例，其他發行版把 `apt` 換成自己的套件管理工具即可。

以下假設專案放在 `/opt/school-portal`，用專門的 `school` 帳號執行。

| | Windows | Linux |
|---|---|---|
| 啟動 API | `start-api.cmd` | `./start-api.sh` |
| 開機自動啟動 | 手動開視窗 | `deploy/systemd/school-api.service` |
| 防火牆 | `New-NetFirewallRule`（見 README） | `ufw` |

`start-api.sh` 會優先用專案裡的 `.venv`，沒有的話用系統的 `python3`。

## 1. 在 Windows 打包

先關掉 API，資料庫才會完整寫進檔案。在專案資料夾開 PowerShell：

```powershell
tar -czf ..\school-portal.tgz --exclude=__pycache__ --exclude=./.venv .
scp ..\school-portal.tgz <帳號>@<伺服器>:/tmp/
```

- 壓縮檔裡有 `data/`（學生資料、帳號、獎懲紀錄、申請紀錄、證件圖片），**當作機密處理**，傳完兩邊都刪掉。不想帶舊資料的話可以多加 `--exclude=./data`，第一次啟動會自動建立空的
- 用 git 搬也可以，專案的 `.gitattributes` 會讓 `.sh` 維持 LF 換行。如果在 Windows 用編輯器改過 `.sh` 而存成 CRLF，bash 會出現 `$'\r': command not found`，用 `sed -i 's/\r$//' *.sh` 修好

## 2. 解開、建立執行帳號

```bash
sudo useradd --system --home-dir /opt/school-portal --shell /usr/sbin/nologin school
sudo mkdir -p /opt/school-portal
sudo tar -xzf /tmp/school-portal.tgz -C /opt/school-portal
rm /tmp/school-portal.tgz
sudo chown -R school:school /opt/school-portal
sudo chmod 750 /opt/school-portal                      # 證件圖片、密碼雜湊不給其他帳號看
sudo chmod +x /opt/school-portal/*.sh
```

之後在伺服器上的操作都用 `sudo -u school` 執行，新產生的檔案才會是 `school` 的，服務才寫得進去。

## 3. 安裝 Python 套件

需要 Python 3.10 以上（Ubuntu 22.04 以後都符合）：

```bash
sudo apt install -y python3 python3-venv
cd /opt/school-portal
sudo -u school python3 -m venv .venv
sudo -u school .venv/bin/pip install -r requirements.txt
```

## 4. 時區

申請時間和年齡的計算用的是伺服器的時區，雲端主機常常預設是 UTC：

```bash
timedatectl | grep "Time zone"
sudo timedatectl set-timezone Asia/Taipei
```

## 5. 試跑

```bash
cd /opt/school-portal && sudo -u school ./start-api.sh
```

再開一個終端機確認：

```bash
curl -i http://127.0.0.1:7000/auth/me    # 401 {"detail":"請先登入"} 就是通了
```

從區網的電腦打開 `start-api.sh` 印出的網址（`http://<伺服器 IP>:7000/docs`）試試。都正常就 Ctrl+C 停掉，改用下面的 systemd。

## 6. 開機自動啟動（systemd）

`deploy/systemd/school-api.service` 會執行 `start-api.sh`，程式掛掉會自動重新啟動。專案不在 `/opt/school-portal`、或不是用 `school` 帳號執行的話，先改 service 檔裡的路徑和 `User=`。

```bash
sudo cp /opt/school-portal/deploy/systemd/school-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now school-api
systemctl status school-api
```

| 要做的事 | 指令 |
|---|---|
| 看 log | `journalctl -u school-api -f` |
| 改了程式之後 | `sudo systemctl restart school-api` |
| 停止 | `sudo systemctl stop school-api` |
| 管理學生資料、密碼、獎懲 | `cd /opt/school-portal && sudo -u school .venv/bin/python -m app.manage -h` |
| 資料庫管理介面 | `cd /opt/school-portal && sudo -u school ./start-admin.sh`，再從自己的電腦開 SSH 通道 `ssh -L 7001:127.0.0.1:7001 <帳號>@<伺服器>`，瀏覽器打開 http://127.0.0.1:7001 |

## 7. 防火牆

API 直接相信 `X-Student-ID`，所以 7000 **只開放給信任的網段**。用 ufw 的話：

```bash
sudo ufw allow OpenSSH                                          # 用 SSH 管理的話先放行，免得把自己擋在外面
sudo ufw allow from 192.168.1.0/24 to any port 7000 proto tcp   # 換成實驗室 / 學校區網的網段
sudo ufw enable
```
