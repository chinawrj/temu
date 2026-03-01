#!/usr/bin/env python3
"""
站斧浏览器 Xray 代理扫描器 v2
自动发现所有 xray 实例，主动触发新连接以抓取认证凭据，获取出口 IP

策略：
1. 找到所有 xray 进程和端口
2. 对每个端口启动 tcpdump 抓包
3. 短暂 STOP/CONT xray 进程，让已有浏览器连接断开重连
4. 从 tcpdump 捕获的新握手中解析凭据
5. 验证凭据并获取出口 IP
"""

import subprocess
import re
import socket
import struct
import time
import sys
import base64
import os
import signal
import json
from datetime import datetime

# ============================================================
# Step 1: 发现所有 xray 进程及其监听端口
# ============================================================

def find_xray_instances():
    """查找所有 xray 进程及其 SOCKS5/HTTP 监听端口"""
    result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
    
    instances = []
    for line in result.stdout.split('\n'):
        if 'xray' in line.lower() and '-c stdin:' in line and 'grep' not in line:
            parts = line.split()
            pid = int(parts[1])
            instances.append({"pid": pid})
    
    if not instances:
        print("❌ 未发现运行中的 xray 进程")
        return []
    
    for inst in instances:
        result = subprocess.run(
            ["lsof", "-i", "-n", "-P", "-a", "-p", str(inst["pid"])],
            capture_output=True, text=True
        )
        
        listen_ports = []
        remote_conns = []
        local_conns = 0
        for line in result.stdout.split('\n'):
            if 'LISTEN' in line:
                match = re.search(r':(\d+)\s+\(LISTEN\)', line)
                if match:
                    listen_ports.append(int(match.group(1)))
            elif 'ESTABLISHED' in line:
                if '->' in line:
                    right = line.split('->')[-1]
                    if '127.0.0.1' not in right:
                        match = re.search(r'->(\S+):(\d+)', line)
                        if match:
                            remote_conns.append(f"{match.group(1)}:{match.group(2)}")
                    else:
                        local_conns += 1
        
        listen_ports.sort()
        if len(listen_ports) >= 2:
            inst["socks5_port"] = listen_ports[0]
            inst["http_port"] = listen_ports[1]
        elif len(listen_ports) == 1:
            inst["socks5_port"] = listen_ports[0]
            inst["http_port"] = None
        else:
            inst["socks5_port"] = None
            inst["http_port"] = None
        
        inst["remote_servers"] = list(set(remote_conns))
        inst["local_conns"] = local_conns
    
    return instances


# ============================================================
# Step 2: 主动触发新连接 + tcpdump 抓取凭据  
# ============================================================

def capture_credentials_for_port(socks5_port, xray_pid, timeout=10):
    """
    1. 启动 tcpdump 监听
    2. 短暂 SIGSTOP xray → 浏览器连接断开
    3. SIGCONT 恢复 → 浏览器重连产生新 SOCKS5 握手
    4. 解析凭据
    """
    # 启动 tcpdump
    tcpdump_proc = subprocess.Popen(
        ["tcpdump", "-i", "lo0", "-X", "-c", "500",
         f"port {socks5_port}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    
    time.sleep(0.5)  # 等 tcpdump 就绪
    
    # 短暂暂停 xray 使连接超时
    print(f"    ⏸  暂停 PID {xray_pid} (2秒)...")
    try:
        os.kill(xray_pid, signal.SIGSTOP)
        time.sleep(2.0)
        os.kill(xray_pid, signal.SIGCONT)
        print(f"    ▶  已恢复 PID {xray_pid}，等待重连...")
    except Exception as e:
        print(f"    ⚠️  信号发送失败: {e}")
        tcpdump_proc.kill()
        return None, None
    
    # 等待浏览器重连
    try:
        output, stderr = tcpdump_proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        tcpdump_proc.kill()
        output, stderr = tcpdump_proc.communicate()
    
    # 调试: 报告捕获数量
    cap_match = re.search(r'(\d+) packets captured', stderr)
    pkt_count = cap_match.group(1) if cap_match else '?'
    print(f"    📦 捕获了 {pkt_count} 个数据包")
    
    username, password = parse_socks5_auth_from_tcpdump(output)
    return username, password


def parse_socks5_auth_from_tcpdump(tcpdump_output):
    """从 tcpdump 十六进制输出中解析 SOCKS5 用户名/密码"""
    
    # 重组每个数据包的原始字节
    packets = []
    current_hex = bytearray()
    current_header = ""
    
    for line in tcpdump_output.split('\n'):
        # 检测新包 (时间戳行)
        if re.match(r'\d{2}:\d{2}:\d{2}\.\d+', line.strip()):
            if current_hex:
                packets.append((current_header, bytes(current_hex)))
            current_hex = bytearray()
            current_header = line.strip()
            continue
        
        # 解析 hex 行: "  0x0030:  abcd ef01 2345 6789  ASCII..."
        hex_match = re.match(r'\s+0x[\da-fA-F]+:\s+(.*)', line)
        if hex_match:
            hex_part = hex_match.group(1)
            # hex 和 ASCII 用双空格分隔
            parts = hex_part.split('  ')
            if parts:
                hex_str = parts[0].replace(' ', '')
                hex_str = re.sub(r'[^0-9a-fA-F]', '', hex_str)
                if len(hex_str) % 2 == 0 and hex_str:
                    try:
                        current_hex.extend(bytes.fromhex(hex_str))
                    except ValueError:
                        pass
    
    if current_hex:
        packets.append((current_header, bytes(current_hex)))
    
    # 在每个包中搜索 SOCKS5 auth pattern
    # BSD loopback: 4 bytes + IP header (20) + TCP header (20-40) = payload 在 ~44+ 开始
    # Auth packet: \x01 + ulen(1) + username(ulen) + plen(1) + password(plen)
    
    for header, pkt in packets:
        if len(pkt) < 48:
            continue
        
        # 搜索范围: 从偏移 36 开始 (保守的 TCP payload 起始位置)
        for i in range(36, len(pkt) - 10):
            if pkt[i] != 0x01:
                continue
            
            ulen = pkt[i + 1]
            if not (4 <= ulen <= 32):
                continue
            if i + 2 + ulen >= len(pkt):
                continue
            
            username_bytes = pkt[i + 2: i + 2 + ulen]
            if not all(33 <= b <= 126 for b in username_bytes):
                continue
            
            plen_off = i + 2 + ulen
            if plen_off >= len(pkt):
                continue
            
            plen = pkt[plen_off]
            if not (8 <= plen <= 64):
                continue
            
            end = plen_off + 1 + plen
            if end > len(pkt):
                continue
            
            password_bytes = pkt[plen_off + 1: end]
            if not all(33 <= b <= 126 for b in password_bytes):
                continue
            
            username = username_bytes.decode('ascii')
            password = password_bytes.decode('ascii')
            
            # 排除误报: 不能是 HTTP 方法或常见字符串
            if username in ('GET ', 'POST', 'PUT ', 'HEAD', 'HTTP', 'Host',
                            'Date', 'Conn', 'Cont', 'Acce', 'User'):
                continue
            
            return (username, password)
    
    return (None, None)


# ============================================================
# Step 3: 验证凭据并获取出口 IP
# ============================================================

def verify_socks5(host, port, username, password):
    """通过 SOCKS5 代理测试并获取出口 IP"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(15)
    try:
        sock.connect((host, port))
        sock.send(b'\x05\x01\x02')
        resp = sock.recv(2)
        if resp != b'\x05\x02':
            return None
        
        uname = username.encode()
        passwd = password.encode()
        sock.send(b'\x01' + bytes([len(uname)]) + uname + bytes([len(passwd)]) + passwd)
        resp = sock.recv(2)
        if resp != b'\x01\x00':
            return None
        
        target = b'httpbin.org'
        sock.send(b'\x05\x01\x00\x03' + bytes([len(target)]) + target + struct.pack('!H', 80))
        resp = sock.recv(10)
        if resp[1] != 0x00:
            return None
        
        sock.send(b'GET /ip HTTP/1.1\r\nHost: httpbin.org\r\nConnection: close\r\n\r\n')
        data = b''
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            except:
                break
        
        match = re.search(r'"origin":\s*"([\d.]+)"', data.decode('utf-8', errors='replace'))
        return match.group(1) if match else "unknown"
    except:
        return None
    finally:
        sock.close()


# ============================================================
# 主流程
# ============================================================

def main():
    if os.geteuid() != 0:
        print("⚠️  此脚本需要 root 权限 (tcpdump + 进程信号)")
        print("   请使用: sudo python3 zhanfu_proxy_scanner.py")
        sys.exit(1)
    
    print("=" * 60)
    print("  站斧浏览器 Xray 代理扫描器 v2")
    print("=" * 60)
    
    # Step 1
    print("\n🔍 Step 1: 扫描 xray 进程...")
    instances = find_xray_instances()
    if not instances:
        sys.exit(1)
    
    print(f"  ✅ 发现 {len(instances)} 个 xray 实例:")
    for inst in instances:
        ports_str = []
        if inst.get("socks5_port"):
            ports_str.append(f"SOCKS5={inst['socks5_port']}")
        if inst.get("http_port"):
            ports_str.append(f"HTTP={inst['http_port']}")
        conns = inst.get('local_conns', 0)
        print(f"     PID {inst['pid']}: {', '.join(ports_str)} ({conns} 本地连接)")
    
    # Step 2
    print(f"\n🔐 Step 2: 主动触发重连并抓取凭据...")
    
    for inst in instances:
        sp = inst.get("socks5_port")
        if not sp:
            continue
        
        pid = inst["pid"]
        has_conns = inst.get("local_conns", 0) > 0 or len(inst.get("remote_servers", [])) > 0
        
        if not has_conns:
            print(f"\n  ⏭  PID {pid} 端口 {sp}: 无活跃连接，跳过")
            continue
        
        print(f"\n  📡 处理 PID {pid}, 端口 {sp}...")
        username, password = capture_credentials_for_port(sp, pid)
        
        if username and password:
            inst["username"] = username
            inst["password"] = password
            print(f"  ✅ 抓取成功!")
        else:
            print(f"  ⚠️  首次未成功，重试...")
            time.sleep(1)
            username, password = capture_credentials_for_port(sp, pid, timeout=12)
            if username and password:
                inst["username"] = username
                inst["password"] = password
                print(f"  ✅ 重试成功!")
            else:
                print(f"  ❌ 未能捕获凭据")
    
    # Step 3
    print(f"\n🌐 Step 3: 验证凭据并获取出口 IP...")
    
    for inst in instances:
        if not inst.get("username"):
            continue
        
        sp = inst["socks5_port"]
        print(f"  🔄 验证端口 {sp}...")
        exit_ip = verify_socks5("127.0.0.1", sp, inst["username"], inst["password"])
        
        if exit_ip:
            inst["exit_ip"] = exit_ip
            inst["verified"] = True
            print(f"  ✅ 出口 IP: {exit_ip}")
        else:
            inst["verified"] = False
            print(f"  ❌ SOCKS5 验证失败")
    
    # 汇总
    print("\n" + "=" * 60)
    print("  📋 扫描结果汇总")
    print("=" * 60)
    
    working = [r for r in instances if r.get("verified")]
    pending = [r for r in instances if not r.get("username")]
    failed = [r for r in instances if r.get("username") and not r.get("verified")]
    
    for i, inst in enumerate(instances, 1):
        print(f"\n  {'─' * 50}")
        print(f"  实例 #{i} (PID: {inst['pid']})")
        print(f"  {'─' * 50}")
        
        if inst.get("verified"):
            print(f"  状态:      ✅ 可用")
            print(f"  SOCKS5:    127.0.0.1:{inst['socks5_port']}")
            if inst.get("http_port"):
                print(f"  HTTP:      127.0.0.1:{inst['http_port']}")
            print(f"  用户名:    {inst['username']}")
            print(f"  密码:      {inst['password']}")
            print(f"  出口 IP:   {inst.get('exit_ip', 'N/A')}")
            if inst.get("remote_servers"):
                print(f"  远程服务器: {', '.join(inst['remote_servers'][:3])}")
            print(f"\n  curl 命令:")
            print(f"    curl -x socks5://{inst['username']}:{inst['password']}@127.0.0.1:{inst['socks5_port']} httpbin.org/ip")
            if inst.get("http_port"):
                print(f"    curl -x http://{inst['username']}:{inst['password']}@127.0.0.1:{inst['http_port']} httpbin.org/ip")
        elif inst.get("username"):
            print(f"  状态:      ⚠️  凭据已获取但验证失败")
            print(f"  SOCKS5:    127.0.0.1:{inst.get('socks5_port', 'N/A')}")
            print(f"  用户名:    {inst['username']}")
            print(f"  密码:      {inst['password']}")
        else:
            sp = inst.get('socks5_port', 'N/A')
            hp = inst.get('http_port', 'N/A')
            print(f"  状态:      ❌ 无活跃连接/未捕获凭据")
            print(f"  SOCKS5:    127.0.0.1:{sp}")
            if hp:
                print(f"  HTTP:      127.0.0.1:{hp}")
    
    print(f"\n{'=' * 60}")
    print(f"  共 {len(instances)} 个实例: {len(working)} 可用, {len(pending)} 无连接, {len(failed)} 失败")
    print(f"{'=' * 60}\n")

    # --- 保存代理缓存文件 ---
    if working:
        cache_data = {
            "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "instances": []
        }
        for inst in working:
            cache_data["instances"].append({
                "pid": inst["pid"],
                "socks5_port": inst["socks5_port"],
                "http_port": inst.get("http_port"),
                "username": inst["username"],
                "password": inst["password"],
                "exit_ip": inst.get("exit_ip", "unknown")
            })
        cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_cache.json")
        with open(cache_path, "w") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
        print(f"💾 代理缓存已保存: {cache_path}")


if __name__ == "__main__":
    main()
