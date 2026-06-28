"""
P2P 測試 - 機器 2（在機器 1 印出地址後執行）
WSL2 Mirrored 模式：直接用 LAN IP，不需要 port forwarding
用法: python p2p_peer2.py /ip4/<機器1 IP>/tcp/7777/p2p/<PeerID>
"""
import sys
import torch
from hivemind.dht import DHT
from hivemind.averaging import DecentralizedAverager

if len(sys.argv) < 2:
    print("用法: python p2p_peer2.py <機器1的multiaddr>")
    print("範例: python p2p_peer2.py /ip4/192.168.1.10/tcp/7777/p2p/12D3KooW...")
    sys.exit(1)

PEER1_ADDR = sys.argv[1]
print(f"連線到 Peer1: {PEER1_ADDR}\n")

dht = DHT(
    initial_peers=[PEER1_ADDR],
    start=True,
    host_maddrs=["/ip4/0.0.0.0/tcp/17778"],
)

tensors = [torch.ones(4) * 3.0, torch.ones(3) * 4.0]
print(f"Peer2 平均前: tensors[0]={tensors[0].tolist()}, tensors[1]={tensors[1].tolist()}")
print(f"理論平均後:   tensors[0]=[2.0,...], tensors[1]=[3.0,...]")
print("\n發起 AllReduce... (timeout 300s)\n")

avg = DecentralizedAverager(
    averaged_tensors=tensors,
    dht=dht,
    prefix="p2p_test",
    min_matchmaking_time=5.0,
    request_timeout=4.0,
    start=True,
)

try:
    result = avg.step(timeout=300.0)
    print(f"\nAllReduce 成功! peers: {result}")
    print(f"Peer2 平均後: tensors[0]={tensors[0].tolist()}, tensors[1]={tensors[1].tolist()}")
except Exception as e:
    print(f"\nAllReduce 失敗: {e}")
finally:
    avg.shutdown()
    dht.shutdown()
    print("節點已關閉")
