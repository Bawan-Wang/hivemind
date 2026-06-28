# Hivemind 單機版快速啟動

## 環境需求

- Python 3.10（建議，3.13 有相容性問題）
- conda 環境

## 安裝步驟

### 1. 建立 conda 環境

```bash
conda create -n hivemind-test python=3.10 -y
conda activate hivemind-test
```

### 2. 安裝 PyTorch（CPU 版，單機測試用）

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### 3. 從原始碼安裝 hivemind

```bash
cd /home/bawanwang/workplace/hivemind
pip install -e .
```

這一步會自動：
- 編譯 `hivemind/proto/*.proto` → `*_pb2.py`
- 下載 `p2pd`（go-libp2p-daemon binary）到 `hivemind/hivemind_cli/p2pd`

### 4. 安裝其餘依賴

```bash
pip install -r requirements.txt
```

### 5. 驗證安裝

```bash
python -c "from hivemind.dht import DHT; print('OK')"
# 輸出：OK
```

---

## 單機測試腳本

將以下程式碼存成 `test_hivemind.py` 並執行：

```python
"""
單機版 hivemind 測試：
1. 啟動兩個 DHT 節點，互相連線
2. Node 1 存一個值到 DHT，Node 2 讀取
3. 兩個 DecentralizedAverager 互相做 AllReduce
"""
import time
import threading
import torch
from hivemind.dht import DHT
from hivemind.averaging import DecentralizedAverager
from hivemind.utils.timed_storage import get_dht_time

# ──────────────────────────────────────────────
print("=" * 50)
print("Step 1: 啟動兩個 DHT 節點")
print("=" * 50)

dht1 = DHT(start=True, host_maddrs=["/ip4/127.0.0.1/tcp/0"])
maddrs = dht1.get_visible_maddrs()
print(f"DHT1 peer ID: {dht1.peer_id}")
print(f"DHT1 地址:    {maddrs[0]}")

dht2 = DHT(initial_peers=maddrs, start=True, host_maddrs=["/ip4/127.0.0.1/tcp/0"])
print(f"DHT2 peer ID: {dht2.peer_id}")
time.sleep(1)

# ──────────────────────────────────────────────
print("\n" + "=" * 50)
print("Step 2: DHT 鍵值存取測試")
print("=" * 50)

key = "test_key"
value = {"message": "hello from dht1", "number": 42}
success = dht1.store(key, value, get_dht_time() + 60)
print(f"DHT1 存值: {value}  → {'成功' if success else '失敗'}")

result = dht2.get(key)
print(f"DHT2 讀值: {result.value}  ← 跨節點讀取成功!")

# ──────────────────────────────────────────────
print("\n" + "=" * 50)
print("Step 3: 兩個 Averager 互相 AllReduce")
print("=" * 50)

tensors1 = [torch.ones(4), torch.ones(3) * 2]       # 值為 1.0 和 2.0
tensors2 = [torch.ones(4) * 3, torch.ones(3) * 4]   # 值為 3.0 和 4.0

print(f"Averager1 平均前: {[t.tolist() for t in tensors1]}")
print(f"Averager2 平均前: {[t.tolist() for t in tensors2]}")
print(f"理論平均結果:     {[(a+b)/2 for a, b in [(1.0, 3.0), (2.0, 4.0)]]}")

avg1 = DecentralizedAverager(
    averaged_tensors=tensors1,
    dht=dht1,
    prefix="test_avg",
    min_matchmaking_time=1.0,
    request_timeout=1.0,
    start=True,
)
avg2 = DecentralizedAverager(
    averaged_tensors=tensors2,
    dht=dht2,
    prefix="test_avg",
    min_matchmaking_time=1.0,
    request_timeout=1.0,
    start=True,
)
time.sleep(1)

# 兩個 averager 同時發起 step
results = {}

def run_step(name, averager, result_dict):
    try:
        r = averager.step(timeout=10.0)
        result_dict[name] = r
    except Exception as e:
        result_dict[name] = f"ERROR: {e}"

t1 = threading.Thread(target=run_step, args=("avg1", avg1, results))
t2 = threading.Thread(target=run_step, args=("avg2", avg2, results))
t1.start(); t2.start()
t1.join(); t2.join()

print(f"\nAllReduce 完成!")
print(f"Averager1 平均後: {[t.tolist() for t in tensors1]}")
print(f"Averager2 平均後: {[t.tolist() for t in tensors2]}")

# ──────────────────────────────────────────────
print("\n" + "=" * 50)
print("清理並結束")
print("=" * 50)
avg1.shutdown()
avg2.shutdown()
dht1.shutdown()
dht2.shutdown()
print("全部正常關閉！")
```

```bash
python test_hivemind.py
```

---

## 預期輸出

```
==================================================
Step 1: 啟動兩個 DHT 節點
==================================================
DHT1 peer ID: 12D3KooWAm8p31R8iTGPDWf887sogFSLNvRjQkvWfAVZV3PuuFHc
DHT1 地址:    /ip4/127.0.0.1/tcp/39001/p2p/12D3KooWAm8p31R8iTGPDWf887sogFSLNvRjQkvWfAVZV3PuuFHc
DHT2 peer ID: 12D3KooWAmwGNkFEaFA2sjj4u78ANqQnFnYe2FydrSHzgv4hSQuT

==================================================
Step 2: DHT 鍵值存取測試
==================================================
DHT1 存值: {'message': 'hello from dht1', 'number': 42}  → 成功
DHT2 讀值: {'message': 'hello from dht1', 'number': 42}  ← 跨節點讀取成功!

==================================================
Step 3: 兩個 Averager 互相 AllReduce
==================================================
Averager1 平均前: [[1.0, 1.0, 1.0, 1.0], [2.0, 2.0, 2.0]]
Averager2 平均前: [[3.0, 3.0, 3.0, 3.0], [4.0, 4.0, 4.0]]
理論平均結果:     [2.0, 3.0]

AllReduce 完成!
Averager1 平均後: [[2.0, 2.0, 2.0, 2.0], [3.0, 3.0, 3.0]]
Averager2 平均後: [[2.0, 2.0, 2.0, 2.0], [3.0, 3.0, 3.0]]

==================================================
清理並結束
==================================================
全部正常關閉！
```

---

## 注意事項

| 項目 | 說明 |
|------|------|
| `host_maddrs=["/ip4/127.0.0.1/tcp/0"]` | 限定 loopback，port=0 讓 OS 自動分配 |
| `min_matchmaking_time=1.0` | 等待 peer 的秒數，測試時調小，正式訓練建議 5s+ |
| `request_timeout` 需小於 `min_matchmaking_time` | 否則 matchmaking 可能 deadlock（會有 WARNING） |
| Warning 訊息 `[WARN] matchmaking` | 正常，不影響執行結果 |
| 單節點呼叫 `averager.step()` | 會拋 `AllreduceException`，需至少兩個 averager 才能成功 |

---

## 已驗證環境

| 項目 | 版本 |
|------|------|
| OS | Linux (WSL2) |
| Python | 3.10.20 |
| torch | 2.12.1+cpu |
| hivemind | 1.2.0.dev0 |
| conda env | `petals` |
