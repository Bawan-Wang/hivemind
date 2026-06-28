"""
P2P 測試 - 機器 1（先執行）
角色：bootstrap 節點 + Averager
"""
import torch
from hivemind.dht import DHT
from hivemind.averaging import DecentralizedAverager

PORT = 7777

dht = DHT(
    start=True,
    host_maddrs=[f"/ip4/0.0.0.0/tcp/{PORT}"],
)

print("\n========== 複製下面的地址給機器 2 ==========")
for addr in dht.get_visible_maddrs():
    # 只印 IPv4 地址（過濾掉 127.0.0.1）
    if "/ip4/127." not in str(addr):
        print(f"  {addr}")
print("=============================================\n")

tensors = [torch.ones(4) * 1.0, torch.ones(3) * 2.0]
print(f"Peer1 平均前: tensors[0]={tensors[0].tolist()}, tensors[1]={tensors[1].tolist()}")
print(f"理論平均後:   tensors[0]=[2.0,...], tensors[1]=[3.0,...]")
print("\n等待 Peer2 連線... (timeout 60s)\n")

avg = DecentralizedAverager(
    averaged_tensors=tensors,
    dht=dht,
    prefix="p2p_test",
    min_matchmaking_time=5.0,
    request_timeout=4.0,
    start=True,
)

try:
    result = avg.step(timeout=60.0)
    print(f"\nAllReduce 成功! peers: {result}")
    print(f"Peer1 平均後: tensors[0]={tensors[0].tolist()}, tensors[1]={tensors[1].tolist()}")
except Exception as e:
    print(f"\nAllReduce 失敗: {e}")
finally:
    avg.shutdown()
    dht.shutdown()
    print("節點已關閉")
