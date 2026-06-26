# Hivemind 學習筆記

> 版本：`1.2.0.dev0`
> 專案定位：PyTorch 函式庫，讓數百台分散在世界各地的電腦協作訓練單一大型模型，無需中心化 master node。
> 知名用戶：[Petals](https://petals.dev)（百億參數 LLM 分散推論與 fine-tuning）

---

## 整體架構

```
hivemind/
├── p2p/          # 底層 P2P 網路層（libp2p）
├── dht/          # 分散式雜湊表（節點發現 & 鍵值儲存）
├── averaging/    # 去中心化參數平均
├── compression/  # 傳輸壓縮
├── moe/          # Mixture-of-Experts 分散推論
├── optim/        # 協作訓練 Optimizer
└── utils/        # multiaddr、async 工具、序列化等
```

---

## 各層詳解

### 1. P2P 網路層 (`hivemind/p2p/p2p_daemon.py`)

- 在背景啟動 **go-libp2p-daemon** binary，負責所有底層 P2P 通訊
- 解決 NAT 穿透、relay 轉發、peer 發現等問題
- `PeerID` = 節點唯一身份（Ed25519 公鑰 hash）

**兩種通訊介面：**

| 介面 | 說明 |
|------|------|
| `add_protobuf_handler` | Request / Response，訊息格式為 Protobuf |
| `add_binary_stream_handler` | 雙向 streaming，用於大量資料傳輸 |

**特殊類別：**
- `ServicerBase` — 定義 RPC handler 的基礎類，子類只要實作方法就能被遠端呼叫
- `StubBase` — 呼叫遠端 RPC 的客戶端代理

---

### 2. DHT 分散式雜湊表 (`hivemind/dht/`)

基於 **Kademlia** 協定的分散式鍵值儲存，是整個網路的「目錄服務」。

**核心類別：`DHT`（繼承 `mp.Process`）**

```python
dht = hivemind.DHT(initial_peers=[...], start=True)
dht.store(key, value, expiration_time=get_dht_time() + 600)
result = dht.get(key)
```

**設計重點：**
- `DHT` 本身跑在獨立背景行程，主行程透過 `mp.Pipe` + `MPFuture` 溝通
- 所有資料都有 **TTL（expiration）**，節點離線後資料自動過期，不留 stale 紀錄
- 支援 `subkey`：同一個 key 下可有多個子條目（用於多個 server 宣告同類型 experts）
- `run_coroutine(coro)` — 在 DHT 行程內執行自訂 async 函數，可直接存取底層 `DHTNode`

**子模組：**

| 檔案 | 功能 |
|------|------|
| `node.py` | 底層 DHTNode，管理 routing table 與網路請求 |
| `routing.py` | Kademlia k-bucket routing table |
| `storage.py` | 本地 key-value 儲存（含 TTL 管理） |
| `protocol.py` | DHT RPC 協定實作（store / find_node / find_value） |
| `crypto.py` | RSA / Ed25519 簽章驗證 |
| `schema.py` | 資料 schema 驗證（用 Pydantic） |
| `validation.py` | `RecordValidatorBase` — 可插拔的資料驗證器 |
| `traverse.py` | 迭代式最近節點搜尋 |

---

### 3. 去中心化 Averaging (`hivemind/averaging/`)

把分散在各 peer 的 tensors 做全局平均，是協作訓練的核心機制。

**核心類別：`DecentralizedAverager`（繼承 `mp.Process` + `ServicerBase`）**

**AllReduce 流程：**

```
1. Matchmaking — 透過 DHT 找到同 group 的 peers
2. 各 peer 切分自己的 tensors（partition）
3. Butterfly AllReduce — 每個 peer 負責平均不同的 tensor 分片
4. 各 peer 收集所有分片，重建完整的平均結果
```

**子模組：**

| 檔案 | 功能 |
|------|------|
| `averager.py` | `DecentralizedAverager` 主類，管理背景行程 |
| `allreduce.py` | `AllReduceRunner`，執行 Butterfly AllReduce 協定 |
| `matchmaking.py` | 透過 DHT 找同 epoch 的 peers 組成 group |
| `partition.py` | 將 tensor 切分給各 peer，支援 load balancing |
| `load_balancing.py` | 依據 bandwidth 分配各 peer 的工作量 |
| `group_info.py` | AllReduce group 的 metadata |
| `key_manager.py` | 管理 DHT 上的 group key（含 bit-prefix 分桶） |
| `control.py` | `StepControl`，控制 averaging 的各個階段 |

**重要參數：**
- `target_group_size` — 每次 AllReduce 的 peer 數（建議 2 的冪次）
- `averaging_alpha` — 平均的學習率（預設直接設為全局平均值）
- `client_mode` — True 時只加入別人發起的 group，不接受 inbound 連線
- `auxiliary` — 只協助計算，不貢獻自己的 tensors

---

### 4. Compression (`hivemind/compression/`)

傳輸前壓縮 tensors，節省頻寬。

| 類別 | 說明 |
|------|------|
| `NoCompression` | 不壓縮（預設） |
| `Float16Compression` | 轉為 FP16 |
| `BlockwiseQuantization` | 8-bit 量化（需安裝 bitsandbytes） |
| `AdaptiveCompression` | 自動選擇最適壓縮方式 |

介面統一為 `CompressionBase`：`compress(tensor)` / `decompress(serialized)` 

---

### 5. Mixture-of-Experts (`hivemind/moe/`)

把神經網路的 layers 分散到不同機器上，允許訓練超大模型。

#### Server 端

| 類別/檔案 | 功能 |
|-----------|------|
| `Server` | 主機 experts，透過 DHT 定期廣播自己的位置和狀態 |
| `ModuleBackend` | 包裝單個 expert module，管理前向/後向、優化器 |
| `Runtime` | 批次合併請求，最大化 GPU 利用率 |
| `ConnectionHandler` | 處理 inbound RPC 請求 |
| `DHTHandlerThread` | 定期向 DHT 更新 expert 的 alive 狀態 |

#### Client 端

| 類別 | 功能 |
|------|------|
| `RemoteExpert` | 代理物件，`forward()` 時遠端執行，`backward()` 時遠端求梯度 |
| `RemoteMixtureOfExperts` | Beam search 找最合適的 experts 組合 |
| `RemoteSwitchMixtureOfExperts` | Switch Transformer 風格的 top-1 路由 |

#### Expert UID 格式

```
"prefix.expert_name.0.2.1"
```
- 用 DHT 的 subkey 機制，多個 server 可以宣告相同 expert name

---

### 6. Optimizer (`hivemind/optim/`)

可直接替換 PyTorch Optimizer，讓模型協作訓練。

```python
dht = hivemind.DHT(initial_peers=INITIAL_PEERS, start=True)
opt = hivemind.Optimizer(
    dht=dht,
    run_id="experiment_name",
    batch_size_per_step=4,
    target_batch_size=4096,
    params=model.parameters(),
    optimizer=lambda params: torch.optim.Adam(params),
)
# 之後直接當作普通 optimizer 使用
loss.backward()
opt.step()
```

**訓練流程（預設同步模式）：**

```
1. 各 peer 累積本地梯度（不立即 update）
2. 全網梯度總量達到 target_batch_size
3. AllReduce 平均梯度 → optimizer.step() → 更新參數
4. 落後的 peer 自動從他人下載最新 checkpoint
```

**子模組：**

| 檔案 | 功能 |
|------|------|
| `optimizer.py` | `Optimizer` 主類，整合所有子元件 |
| `grad_averager.py` | `GradientAverager`，梯度的去中心化平均 |
| `state_averager.py` | `TrainingStateAverager`，完整 optimizer state 平均 |
| `progress_tracker.py` | `ProgressTracker`，透過 DHT 追蹤全網訓練進度（epoch/step） |
| `grad_scaler.py` | `GradScaler`，協作版 FP16 混合精度 scaler |
| `power_sgd_averager.py` | PowerSGD 低秩梯度壓縮 averager |

**重要概念：**
- **Epoch** = 全網累積 `target_batch_size` 個樣本的區間，不等同於過完一遍資料集
- `optimizer.local_epoch` — 應用於 LR Scheduler，而非呼叫次數

**非同步模式選項（進階）：**

| 參數 | 效果 |
|------|------|
| `delay_optimizer_step=True` | 用上一輪的梯度做 update，換取不等待 AllReduce |
| `delay_gradient_averaging=True` | AllReduce 在背景進行，不阻塞訓練 |
| `use_local_updates=True` | 完全非同步，定期平均參數而非梯度 |

---

## 關鍵設計模式

| 模式 | 說明 |
|------|------|
| **背景行程隔離** | DHT、Averager 都跑在獨立 `mp.Process`，主行程透過 `mp.Pipe` + `MPFuture` 非同步溝通 |
| **AsyncIO + uvloop** | 所有網路 I/O 都是 async，切換到 uvloop 提升效能 |
| **Protobuf** | 所有 RPC 訊息格式都用 `.proto` 定義（`hivemind/proto/`） |
| **TTL 鍵值** | DHT 中所有資料都有過期時間，stale 節點資訊自動消失 |
| **無 master node** | 完全對等（P2P），任意節點離線不影響整體運作 |
| **可插拔壓縮** | `CompressionBase` 介面，壓縮策略可依 tensor 類型個別設定 |

---

## 協作訓練資料流

```
各 peer 的訓練 loop
       │
       ▼
  opt.step()
       │
       ├─ 本地梯度累積 (GradientAverager)
       │
       ├─ ProgressTracker 透過 DHT 檢查全網進度
       │         └─ 若未達 target_batch_size → 繼續累積
       │
       ├─ Matchmaking：找到同 epoch 的 peers
       │
       ├─ Butterfly AllReduce（分片 + 壓縮傳輸）
       │
       ├─ 全局平均梯度 → optimizer.step() → 更新參數
       │
       └─ ProgressTracker 更新 DHT 上的 epoch 進度
```

---

## Petals 如何使用 Hivemind

| Hivemind 元件 | Petals 用途 |
|---------------|-------------|
| `DHT` | Server 廣播自己持有哪些 blocks，Client 透過 DHT 找到對應 server |
| `P2P` | Client 與 server 之間直接傳輸 activations（inference pipeline） |
| `DecentralizedAverager` | Fine-tuning 時的梯度同步 |
| `ServicerBase` / `StubBase` | 定義 Petals 自己的 RPC（`rpc_forward`、`rpc_backward` 等） |

---

## 訓練 vs 推論

Hivemind 是**通用的分散式深度學習基礎設施**，訓練和推論都在它的設計範圍內。

| 功能 | 支援 | 使用元件 |
|------|------|---------|
| 協作訓練（梯度同步） | ✅ | `Optimizer` + `DecentralizedAverager` |
| 分散推論（MoE forward） | ✅ | `RemoteExpert` + `Server` |
| 分散 fine-tuning | ✅ | 兩者結合（Petals 的做法） |

`RemoteExpert` 可以只做 forward，不做 backward：

```python
with torch.no_grad():
    output = remote_expert(input_tensor)  # 送到遠端機器計算，拿回結果
```

對比普通 PyTorch 訓練：

| 項目 | 普通訓練 | Hivemind 協作訓練 |
|------|---------|-----------------|
| optimizer.step() | 每個 batch | 累積到 `target_batch_size` 才真正 update |
| 機器同步要求 | torchrun 需全員同時上線 | 機器隨時可加入或離開 |
| 網路要求 | 同一內網 | 可跨網際網路 |
| 容錯性 | master node 掛掉全停 | 任一節點離線其他人繼續 |
| LR Scheduler | 依 step 計數 | 依 `optimizer.local_epoch` 計數 |

---

## 執行方式

### 安裝

```bash
# 1. 安裝 PyTorch（依 CUDA 版本調整）
pip install torch

# 2. 從原始碼安裝（自動編譯 proto + 下載 p2pd binary）
cd hivemind
pip install -e .

# 3. 驗證
python -c "import hivemind; print(hivemind.__version__)"
```

> **注意**：建議使用 Python 3.10 或 3.11 的虛擬環境，Python 3.13 在部分依賴（如 uvloop）可能有相容性問題。

### 需要幾台機器？

**1 台**就可以跑（開發 / 測試）：

```python
# 同一台機器，兩個 process 互相連線
dht1 = hivemind.DHT(start=True)
dht2 = hivemind.DHT(initial_peers=dht1.get_visible_maddrs(), start=True)
```

實際用途的建議最低規格：

| 用途 | 最少機器數 | 說明 |
|------|-----------|------|
| 開發 / 測試 | 1 台 | 多個 process 模擬多節點 |
| 模型太大放不下（MoE） | 1 台多卡 | 不同 GPU 托管不同 layers |
| 跨機器 MoE 推論 | 2 台+ | A 跑前半 layers，B 跑後半 layers |
| 協作訓練（有意義的加速） | 2 台+ | 1 台訓練沒有「協作」可言 |

### 2 台的標準配置

**機器 A — 啟動 Bootstrap 節點並訓練**

```bash
# 啟動 DHT bootstrap
hivemind-dht --host_maddrs /ip4/0.0.0.0/tcp/31337
# 輸出：/ip4/140.112.x.x/tcp/31337/p2p/QmXkVz3...（把這個地址給機器 B）

# 啟動訓練
python run_trainer.py --run_id my_experiment
```

**機器 B — 加入網路並訓練**

```bash
python run_trainer.py \
    --initial_peers /ip4/140.112.x.x/tcp/31337/p2p/QmXkVz3... \
    --run_id my_experiment
```

各機器的輸出類似：

```
[INFO] Loading state from peers
[INFO] Step #1
[INFO] Your current contribution: 256 samples
[INFO] Performance: 12.340 samples/sec
[INFO] Local loss: 2.34512
[INFO] Averaging with 2 peers...
[INFO] Step #2
```

2 台的限制：

| 項目 | 說明 |
|------|------|
| AllReduce | 2 個 peer 完全可以運作 |
| 容錯性 | 一台掛掉，另一台就孤立（沒有其他 peer 可找） |
| 網路需求 | 同內網直接用；跨網際網路需有公網 IP 或開 `--use_auto_relay` |

### 協作訓練節點互動圖

```
機器 A (Bootstrap + Trainer)     機器 B (Trainer)
─────────────────────────        ────────────────
hivemind-dht (port 31337)
  │ 廣播 multiaddr
  │◄────────────────────────────── 連線加入
  │
  │     (各自獨立訓練，累積本地梯度)
  │
  │     全網 samples 達到 target_batch_size
  │◄──────────────────────────────────────────────
  │            Butterfly AllReduce
  │──────────────────────────────────────────────►
  │         (同步梯度，各自 optimizer.step())
  │
  │     (繼續下一輪訓練...)
```

### MoE 分散推論節點互動圖（Petals 風格）

```
使用者機器                 機器 A                    機器 B
──────────                 ──────                    ──────
input tokens               blocks 0~11               blocks 12~23
     │                          │                         │
     └── forward ──────────────►│                         │
                                │── activations ─────────►│
                                │                         │── output tokens
                                │◄── output tokens ───────┘
     ◄── output tokens ─────────┘
```

---

## 快速參考

```python
import hivemind

# 啟動 DHT 節點
dht = hivemind.DHT(initial_peers=["/ip4/..."], start=True)

# 去中心化梯度平均
averager = hivemind.DecentralizedAverager(
    averaged_tensors=list(model.parameters()),
    dht=dht, prefix="my_run", start=True
)
averager.step()  # 與 peers 做一次 AllReduce

# 協作訓練（最簡用法）
opt = hivemind.Optimizer(
    dht=dht, run_id="my_run",
    batch_size_per_step=LOCAL_BATCH,
    target_batch_size=GLOBAL_BATCH,
    params=model.parameters(),
    optimizer=lambda p: torch.optim.AdamW(p),
)
```
