# Hivemind 雙機 P2P 測試指南

兩台 WSL2 電腦在**同一區域網路**下，建立真正的跨機器 P2P 連線，
完成 DHT 節點互連與 `DecentralizedAverager` 的跨機器 AllReduce。

---

## 1. 架構總覽

```
        機器 1 (Windows)                          機器 2 (Windows)
   ┌──────────────────────┐                ┌──────────────────────┐
   │  WSL2 (Mirrored 模式) │                │  WSL2 (Mirrored 模式) │
   │  ┌────────────────┐  │   區域網路 LAN  │  ┌────────────────┐  │
   │  │ p2p_peer1.py   │  │  192.168.1.x   │  │ p2p_peer2.py   │  │
   │  │ DHT :7777      │◄─┼────────────────┼─►│ DHT :7778      │  │
   │  │ Averager       │  │                │  │ Averager       │  │
   │  └────────────────┘  │                │  └────────────────┘  │
   └──────────────────────┘                └──────────────────────┘
```

- **Mirrored 網路模式**：WSL2 直接共用 Windows 的網卡，WSL 內就能拿到 `192.168.x.x` 的
  區域網路 IP，**不需要** `netsh portproxy` 轉發，也不需要 `announce_maddrs`。
- 機器 1 當 bootstrap 節點（先啟動），機器 2 用機器 1 的 multiaddr 加入。

---

## 2. 環境需求

| 項目 | 需求 |
|------|------|
| OS | Windows 11 22H2 (Build 22621) 以上，才支援 WSL Mirrored 模式 |
| WSL | 版本 2.x（本次驗證 `2.6.1.0`） |
| Python | 3.10（conda 環境 `hivemind-test`） |
| PyTorch | CPU 版即可 |
| hivemind | 從原始碼 `pip install -e .`（本次 `1.2.0.dev0`） |
| 網路 | 兩台在同一區域網路（同一台路由器 / 同一 Wi-Fi） |

hivemind 安裝細節見 [single_machine_quickstart.md](single_machine_quickstart.md)。

---

## 3. 環境架設步驟

### 3.1 兩台都啟用 WSL2 Mirrored 模式

在 **Windows** 建立 / 編輯 `C:\Users\<使用者>\.wslconfig`（PowerShell）：

```powershell
@"
[wsl2]
networkingMode=mirrored
"@ | Set-Content "$env:USERPROFILE\.wslconfig" -Encoding UTF8
```

完全重啟 WSL2 讓設定生效：

```powershell
wsl --shutdown
Start-Sleep -Seconds 5
wsl
```

進入 WSL 後用 `ifconfig` 確認：原本的 `eth0: 172.31.x.x` 會被取代，
出現 `192.168.x.x` 的區域網路 IP（代表 Mirrored 生效）。

### 3.2 清除舊的 portproxy 規則（重要！）

如果先前用過 NAT 模式 + `netsh portproxy` 轉發，**那些規則會在 Mirrored 模式下佔用
`0.0.0.0:7777`，導致 hivemind 報 `bind: address already in use`**。

在 WSL 內查詢並清除（會跳 UAC，按「是」）：

```bash
# 查詢現有規則
powershell.exe -NoProfile -Command "netsh interface portproxy show all"

# 機器 1 清除 7777
powershell.exe -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-Command','netsh interface portproxy delete v4tov4 listenport=7777 listenaddress=0.0.0.0'"

# 機器 2 清除 7778
powershell.exe -NoProfile -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-Command','netsh interface portproxy delete v4tov4 listenport=7778 listenaddress=0.0.0.0'"
```

### 3.3 Windows 防火牆放行（首次連線若被擋）

```powershell
New-NetFirewallRule -DisplayName "Hivemind-7777" -Direction Inbound -Protocol TCP -LocalPort 7777 -Action Allow  # 機器 1
New-NetFirewallRule -DisplayName "Hivemind-7778" -Direction Inbound -Protocol TCP -LocalPort 7778 -Action Allow  # 機器 2
```

---

## 4. 執行測試

### 機器 1（先執行）

```bash
conda activate hivemind-test
cd ~/workplace/hivemind
python p2p_peer1.py
```

會印出可連線地址，並停在「等待 Peer2 連線...」：

```
========== 複製下面的地址給機器 2 ==========
  /ip4/192.168.1.183/tcp/7777/p2p/12D3KooWLzDsiNuGjVbgUvis5fNKuAL5HVvih2T62ToB3yVrrgxE
=============================================
等待 Peer2 連線... (timeout 300s)
```

### 機器 2（複製上面那行地址後執行）

```bash
conda activate hivemind-test
cd ~/workplace/hivemind
python p2p_peer2.py /ip4/192.168.1.183/tcp/7777/p2p/12D3KooWLzDsiNuGjVbgUvis5fNKuAL5HVvih2T62ToB3yVrrgxE
```

> 順序：**先讓機器 1 停在「等待 Peer2 連線」，再到機器 2 貼地址執行**。
> `timeout=300s`（5 分鐘）是給你從容切換兩台、貼地址的緩衝時間。

---

## 5. 測試腳本內容說明

兩個腳本各自啟動一個 DHT 節點 + 一個 `DecentralizedAverager`，做一次跨機器 AllReduce。

| 項目 | 機器 1 (`p2p_peer1.py`) | 機器 2 (`p2p_peer2.py`) |
|------|------------------------|------------------------|
| DHT 角色 | bootstrap（無 initial_peers） | 用機器 1 的 multiaddr 加入 |
| 監聽 port | 7777 | 7778 |
| 初始 tensors | `[1,1,1,1]`、`[2,2,2]` | `[3,3,3,3]`、`[4,4,4]` |
| matchmaking 群組 | `prefix="p2p_test"`（兩邊相同才會配對） | 同左 |

### AllReduce 邏輯

兩台用**相同的 `prefix="p2p_test"`** 互相發現、組成一個 averaging group，
把各自的 tensors 做**逐元素平均**：

```
tensors[0]:  (1.0 + 3.0) / 2 = 2.0   →  兩台都變成 [2.0, 2.0, 2.0, 2.0]
tensors[1]:  (2.0 + 4.0) / 2 = 3.0   →  兩台都變成 [3.0, 3.0, 3.0]
```

### 關鍵參數

| 參數 | 值 | 說明 |
|------|----|----|
| `host_maddrs` | `/ip4/0.0.0.0/tcp/<port>` | 監聽所有介面（Mirrored 下即 LAN） |
| `prefix` | `"p2p_test"` | averaging 群組識別碼，兩邊必須一致 |
| `min_matchmaking_time` | `5.0` | 等待其他 peer 加入群組的秒數 |
| `request_timeout` | `4.0` | 需 **小於** matchmaking time，否則可能 deadlock |
| `avg.step(timeout=...)` | `300.0` | 整個 AllReduce 的總時限（含等待對方上線） |

> `min_matchmaking_time` 是「配對等待」，`avg.step(timeout)` 是「整個流程總時限」，
> 兩者是不同層次，配對成功後實際運算很快完成。

---

## 6. 預期輸出

**機器 1：**
```
Peer1 平均前: tensors[0]=[1.0, 1.0, 1.0, 1.0], tensors[1]=[2.0, 2.0, 2.0]
...
AllReduce 成功! peers: ...
Peer1 平均後: tensors[0]=[2.0, 2.0, 2.0, 2.0], tensors[1]=[3.0, 3.0, 3.0]
節點已關閉
```

**機器 2：**
```
Peer2 平均前: tensors[0]=[3.0, 3.0, 3.0, 3.0], tensors[1]=[4.0, 4.0, 4.0]
...
AllReduce 成功! peers: ...
Peer2 平均後: tensors[0]=[2.0, 2.0, 2.0, 2.0], tensors[1]=[3.0, 3.0, 3.0]
節點已關閉
```

兩台平均後的 tensors **完全一致** = 跨機器 AllReduce 成功。

---

## 7. 常見問題排查

| 症狀 | 原因 | 解法 |
|------|------|------|
| `bind: address already in use` | 殘留的 p2pd 行程或舊 portproxy 規則佔用 port | `pkill -9 -f p2pd`；清除 portproxy（見 3.2） |
| `could not find a group` | 對方沒連上 / IP 錯誤，等到 timeout 都沒配對成功 | 確認用對 multiaddr、兩邊 prefix 一致、防火牆放行 |
| `ifconfig` 仍是 `172.31.x.x` | Mirrored 模式未生效 | 確認 `.wslconfig` 內容正確、`wsl --shutdown` 完整重啟、Windows 版本 ≥ 22H2 |
| 印出的地址是 `172.x` 而非 `192.168.x` | Mirrored 未生效，仍在 NAT 模式 | 同上 |
| WSL 重啟後 IP 變動 | Mirrored 模式下跟隨 Windows，一般穩定；NAT 模式才會頻繁變動 | 重新跑 peer1 取得最新地址 |

---

## 8. 相關檔案

- [p2p_peer1.py](p2p_peer1.py) — 機器 1 腳本（bootstrap）
- [p2p_peer2.py](p2p_peer2.py) — 機器 2 腳本（join）
- [single_machine_quickstart.md](single_machine_quickstart.md) — 單機版測試與 hivemind 安裝
- [learning_note.md](learning_note.md) — hivemind 架構學習筆記
