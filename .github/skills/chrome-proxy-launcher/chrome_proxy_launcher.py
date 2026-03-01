#!/usr/bin/env python3
"""
Chrome Proxy Launcher — 通过站斧 xray 代理启动 Chrome

用法:
    # 指定出口 IP（自动查找匹配的 xray 实例）
    python3 chrome_proxy_launcher.py --exit-ip 66.80.56.14

    # 直接指定 xray SOCKS5 端口和凭据
    python3 chrome_proxy_launcher.py --socks5-port 12631 --user rkw8puvj --pass 3TG0yPu7nWK5h16Y

    # 自定义端口
    python3 chrome_proxy_launcher.py --exit-ip 66.80.56.14 --local-port 11080 --cdp-port 9222

    # 停止（清理转发器进程）
    python3 chrome_proxy_launcher.py --stop
"""

import argparse
import asyncio
import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(SCRIPT_DIR, "profiles")
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PID_FILE = os.path.join(SCRIPT_DIR, ".launcher_pids.json")

# ════════════════════════════════════════════════════════════
# SOCKS5 Forwarder (no-auth local → authenticated upstream)
# ════════════════════════════════════════════════════════════

async def _forward(reader, writer):
    try:
        while True:
            data = await reader.read(8192)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def _handle_client(client_reader, client_writer, upstream_host, upstream_port,
                          upstream_user, upstream_pass):
    try:
        header = await client_reader.readexactly(2)
        ver, nmethods = struct.unpack("!BB", header)
        if ver != 5:
            client_writer.close()
            return
        await client_reader.readexactly(nmethods)
        client_writer.write(b"\x05\x00")
        await client_writer.drain()

        req_header = await client_reader.readexactly(4)
        ver, cmd, _, atyp = struct.unpack("!BBBB", req_header)
        if cmd != 1:
            client_writer.write(b"\x05\x07\x00\x01" + b"\x00" * 6)
            await client_writer.drain()
            client_writer.close()
            return

        if atyp == 1:
            dst_addr = await client_reader.readexactly(4)
            dst_port_bytes = await client_reader.readexactly(2)
            connect_req = b"\x05\x01\x00\x01" + dst_addr + dst_port_bytes
        elif atyp == 3:
            dlen = (await client_reader.readexactly(1))[0]
            domain = await client_reader.readexactly(dlen)
            dst_port_bytes = await client_reader.readexactly(2)
            connect_req = b"\x05\x01\x00\x03" + bytes([dlen]) + domain + dst_port_bytes
        elif atyp == 4:
            dst_addr = await client_reader.readexactly(16)
            dst_port_bytes = await client_reader.readexactly(2)
            connect_req = b"\x05\x01\x00\x04" + dst_addr + dst_port_bytes
        else:
            client_writer.close()
            return

        up_reader, up_writer = await asyncio.open_connection(upstream_host, upstream_port)

        up_writer.write(b"\x05\x01\x02")
        await up_writer.drain()
        up_resp = await up_reader.readexactly(2)
        if up_resp[1] != 0x02:
            client_writer.write(b"\x05\x01\x00\x01" + b"\x00" * 6)
            await client_writer.drain()
            client_writer.close()
            up_writer.close()
            return

        u = upstream_user.encode() if isinstance(upstream_user, str) else upstream_user
        p = upstream_pass.encode() if isinstance(upstream_pass, str) else upstream_pass
        auth_msg = b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p
        up_writer.write(auth_msg)
        await up_writer.drain()
        auth_resp = await up_reader.readexactly(2)
        if auth_resp[1] != 0x00:
            client_writer.write(b"\x05\x01\x00\x01" + b"\x00" * 6)
            await client_writer.drain()
            client_writer.close()
            up_writer.close()
            return

        up_writer.write(connect_req)
        await up_writer.drain()
        up_connect_resp = await up_reader.readexactly(4)
        if up_connect_resp[1] != 0x00:
            client_writer.write(up_connect_resp + b"\x00" * 6)
            await client_writer.drain()
            client_writer.close()
            up_writer.close()
            return

        resp_atyp = up_connect_resp[3]
        if resp_atyp == 1:
            bind_rest = await up_reader.readexactly(6)
        elif resp_atyp == 3:
            blen = (await up_reader.readexactly(1))[0]
            bind_rest = bytes([blen]) + await up_reader.readexactly(blen + 2)
        elif resp_atyp == 4:
            bind_rest = await up_reader.readexactly(18)
        else:
            bind_rest = await up_reader.readexactly(6)

        client_writer.write(up_connect_resp + bind_rest)
        await client_writer.drain()

        t1 = asyncio.ensure_future(_forward(client_reader, up_writer))
        t2 = asyncio.ensure_future(_forward(up_reader, client_writer))
        await asyncio.gather(t1, t2)
    except (asyncio.IncompleteReadError, ConnectionRefusedError, OSError):
        pass
    finally:
        client_writer.close()


def start_forwarder(local_port, upstream_host, upstream_port, upstream_user, upstream_pass):
    """Fork a SOCKS5 forwarder as a background process. Returns child PID."""
    pid = os.fork()
    if pid > 0:
        # Parent: wait a moment and verify child is alive
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return pid

    # Child process
    os.setsid()
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    async def _run():
        def factory(r, w):
            return _handle_client(r, w, upstream_host, upstream_port,
                                  upstream_user, upstream_pass)
        server = await asyncio.start_server(factory, "127.0.0.1", local_port)
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(_run())
    except Exception:
        pass
    os._exit(0)


# ════════════════════════════════════════════════════════════
# Port helpers
# ════════════════════════════════════════════════════════════

def is_port_free(port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def find_free_port(start=11080):
    for p in range(start, start + 100):
        if is_port_free(p):
            return p
    return None


# ════════════════════════════════════════════════════════════
# Proxy verification
# ════════════════════════════════════════════════════════════

def verify_socks5(host, port, username, password):
    """Test SOCKS5 proxy and return exit IP."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(15)
    try:
        sock.connect((host, port))
        sock.send(b'\x05\x01\x02')
        resp = sock.recv(2)
        if resp != b'\x05\x02':
            return None

        u = username.encode() if isinstance(username, str) else username
        p = password.encode() if isinstance(password, str) else password
        sock.send(b'\x01' + bytes([len(u)]) + u + bytes([len(p)]) + p)
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
        return match.group(1) if match else None
    except:
        return None
    finally:
        sock.close()


# ════════════════════════════════════════════════════════════
# xray instance discovery (lightweight, no sudo needed)
# ════════════════════════════════════════════════════════════

def find_xray_instances():
    """Find running xray instances and their listening ports."""
    result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
    instances = []
    for line in result.stdout.split('\n'):
        if 'xray' in line.lower() and '-c stdin:' in line and 'grep' not in line:
            parts = line.split()
            pid = int(parts[1])
            instances.append({"pid": pid})

    for inst in instances:
        result = subprocess.run(
            ["lsof", "-i", "-n", "-P", "-a", "-p", str(inst["pid"])],
            capture_output=True, text=True
        )
        listen_ports = []
        for line in result.stdout.split('\n'):
            if 'LISTEN' in line:
                m = re.search(r':(\d+)\s+\(LISTEN\)', line)
                if m:
                    listen_ports.append(int(m.group(1)))
        listen_ports.sort()
        inst["socks5_port"] = listen_ports[0] if len(listen_ports) >= 1 else None
        inst["http_port"] = listen_ports[1] if len(listen_ports) >= 2 else None

    return instances


# ════════════════════════════════════════════════════════════
# PID file management
# ════════════════════════════════════════════════════════════

def save_pids(exit_ip, forwarder_pid, chrome_pid, local_port, cdp_port):
    data = {}
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                data = json.load(f)
        except:
            pass
    data[exit_ip] = {
        "forwarder_pid": forwarder_pid,
        "chrome_pid": chrome_pid,
        "local_port": local_port,
        "cdp_port": cdp_port,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(PID_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_pids():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}


def _kill_pid(pid, label=""):
    """Kill a process by PID with SIGTERM, then SIGKILL fallback."""
    if not pid:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    # Wait up to 3 seconds for graceful exit
    for _ in range(30):
        if not _pid_alive(pid):
            if label:
                print(f"  ⏹  已停止 {label} (PID {pid})")
            return True
        time.sleep(0.1)
    # Force kill
    try:
        os.kill(pid, signal.SIGKILL)
        if label:
            print(f"  ⏹  已强制停止 {label} (PID {pid})")
        return True
    except ProcessLookupError:
        return True


def _find_chrome_pids_by_profile(profile_dir):
    """Find all Chrome process PIDs using a specific user-data-dir."""
    pids = []
    try:
        # Use "--" to prevent pgrep from interpreting the pattern as its own option
        result = subprocess.run(
            ["pgrep", "-f", f"user-data-dir={profile_dir}"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))
    except:
        pass
    return pids


def _kill_chrome_by_profile(profile_dir, silent=False):
    """Kill all Chrome processes using a specific user-data-dir."""
    pids = _find_chrome_pids_by_profile(profile_dir)
    if not pids:
        return False
    if not silent:
        print(f"  🔍 发现 {len(pids)} 个使用相同 profile 的 Chrome 进程")
    # Kill the main Chrome process (lowest PID is usually the parent)
    main_pid = min(pids)
    _kill_pid(main_pid, "Chrome 主进程" if not silent else "")
    # Wait for children to exit
    time.sleep(1)
    # Kill any remaining
    remaining = _find_chrome_pids_by_profile(profile_dir)
    for pid in remaining:
        _kill_pid(pid)
    if not silent:
        print(f"  ✅ Chrome 进程已全部终止")
    return True


def stop_all():
    """Stop all forwarders and Chrome instances launched by this tool."""
    data = load_pids()
    if not data:
        print("没有正在运行的实例")
        return

    for ip, info in data.items():
        # Kill forwarder
        fwd_pid = info.get("forwarder_pid")
        _kill_pid(fwd_pid, f"forwarder ({ip})")

        # Kill Chrome — first try by profile dir (most reliable),
        # then fall back to saved PID
        profile_dir = os.path.join(PROFILES_DIR, ip)
        if not _kill_chrome_by_profile(profile_dir):
            chrome_pid = info.get("chrome_pid")
            _kill_pid(chrome_pid, f"Chrome ({ip})")

    os.remove(PID_FILE)
    print("✅ 已清理所有实例")


# ════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="通过站斧 xray 代理启动 Chrome（每个出口 IP 独立 profile）"
    )
    parser.add_argument("--no-proxy", action="store_true",
                        help="不使用代理，仅启动带持久化存储和 CDP 的 Chrome")
    parser.add_argument("--profile-name", default=None,
                        help="profile 目录名（默认: 出口 IP 或 no-proxy）")
    parser.add_argument("--exit-ip", help="目标出口 IP（自动匹配 xray 实例）")
    parser.add_argument("--socks5-port", type=int, help="xray SOCKS5 端口（直接指定）")
    parser.add_argument("--user", help="SOCKS5 用户名")
    parser.add_argument("--pass", dest="password", help="SOCKS5 密码")
    parser.add_argument("--local-port", type=int, default=0,
                        help="本地转发端口（默认自动分配）")
    parser.add_argument("--cdp-port", type=int, default=9222,
                        help="Chrome CDP 远程调试端口（默认 9222）")
    parser.add_argument("--url", default=None,
                        help="Chrome 启动时打开的 URL（默认: 代理模式 httpbin.org/ip, 无代理模式 about:blank）")
    parser.add_argument("--also-open", nargs="+", default=None,
                        help="额外打开的 URL 列表")
    parser.add_argument("--stop", action="store_true", help="停止所有已启动的实例")
    parser.add_argument("--status", action="store_true", help="查看已启动实例的状态")
    args = parser.parse_args()

    if args.stop:
        stop_all()
        return

    if args.status:
        data = load_pids()
        if not data:
            print("没有正在运行的实例")
            return
        for ip, info in data.items():
            alive = lambda pid: pid and (os.kill(pid, 0) or True) if pid else False
            fw_ok = "✅" if _pid_alive(info.get("forwarder_pid")) else "❌"
            ch_ok = "✅" if _pid_alive(info.get("chrome_pid")) else "❌"
            print(f"  {ip}: 转发器{fw_ok}(PID {info.get('forwarder_pid')})  "
                  f"Chrome{ch_ok}(PID {info.get('chrome_pid')})  "
                  f"CDP=:{info.get('cdp_port')}  Local=:{info.get('local_port')}")
        return

    # --- No-proxy mode ---
    if args.no_proxy:
        profile_name = args.profile_name or "no-proxy"
        profile_dir = os.path.join(PROFILES_DIR, profile_name)
        os.makedirs(profile_dir, exist_ok=True)

        cdp_port = args.cdp_port
        if not is_port_free(cdp_port):
            # Kill existing Chrome with same profile first
            existing_pids = _find_chrome_pids_by_profile(profile_dir)
            if existing_pids:
                print(f"⚠️  检测到已有 Chrome 使用相同 profile（PID: {min(existing_pids)}），正在关闭...")
                _kill_chrome_by_profile(profile_dir, silent=True)
                time.sleep(2)
            if not is_port_free(cdp_port):
                print(f"⚠️  CDP 端口 {cdp_port} 已占用，自动分配...")
                cdp_port = find_free_port(cdp_port + 1)

        if not cdp_port:
            print("❌ 无法找到可用端口")
            sys.exit(1)

        # Kill existing Chrome with same profile
        existing_pids = _find_chrome_pids_by_profile(profile_dir)
        if existing_pids:
            print(f"⚠️  检测到已有 Chrome 使用相同 profile（PID: {min(existing_pids)}），正在关闭...")
            _kill_chrome_by_profile(profile_dir, silent=True)
            time.sleep(2)
            if is_port_free(args.cdp_port):
                cdp_port = args.cdp_port

        url = args.url or "about:blank"
        urls = [url]
        if args.also_open:
            urls.extend(args.also_open)

        print()
        print("🚀 Chrome Launcher (no proxy)")
        print("═" * 44)
        print(f"  Profile:     {profile_name}")
        print(f"  CDP 端口:     {cdp_port}")
        print(f"  User Data:   {profile_dir}")
        print("═" * 44)

        chrome_proc = subprocess.Popen([
            CHROME_PATH,
            f"--user-data-dir={profile_dir}",
            f"--remote-debugging-port={cdp_port}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            *urls,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        print(f"✅ Chrome 已启动 (PID: {chrome_proc.pid})")
        print(f"   CDP: http://127.0.0.1:{cdp_port}")

        save_pids(profile_name, None, chrome_proc.pid, None, cdp_port)

        time.sleep(2)
        try:
            import urllib.request
            resp = urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/version", timeout=5)
            info = json.loads(resp.read())
            print(f"   Browser: {info.get('Browser', 'unknown')}")
        except:
            print("   ⚠️  CDP 端口尚未响应（Chrome 可能还在启动）")

        print(f"\n💡 停止命令: python3 {os.path.basename(__file__)} --stop")
        return

    # --- Resolve proxy info ---
    socks5_port = args.socks5_port
    username = args.user
    password = args.password
    exit_ip = args.exit_ip

    if exit_ip and not (socks5_port and username and password):
        # 需要扫描找到匹配的 xray 实例
        print(f"🔍 扫描 xray 实例，匹配出口 IP: {exit_ip}...")
        instances = find_xray_instances()
        matched = None
        for inst in instances:
            sp = inst.get("socks5_port")
            if not sp:
                continue
            # 试用已知凭据（如果命令行提供了的话）
            if username and password:
                ip = verify_socks5("127.0.0.1", sp, username, password)
                if ip and ip == exit_ip:
                    matched = inst
                    matched["username"] = username
                    matched["password"] = password
                    matched["exit_ip"] = ip
                    break
        if not matched:
            print(f"❌ 未找到出口 IP 为 {exit_ip} 的代理实例")
            print("   请先运行: sudo python3 zhanfu_proxy_scanner.py")
            print("   然后使用 --socks5-port / --user / --pass 手动指定")
            sys.exit(1)
        socks5_port = matched["socks5_port"]
        username = matched["username"]
        password = matched["password"]

    if not (socks5_port and username and password):
        print("❌ 缺少代理信息。请指定以下参数之一:")
        print("   --no-proxy                (无代理模式)")
        print("   --exit-ip <IP>            (自动匹配，需已扫描)")
        print("   --socks5-port <PORT> --user <USER> --pass <PASS>")
        sys.exit(1)

    # --- Verify proxy first ---
    if not exit_ip:
        print(f"🔄 验证代理 127.0.0.1:{socks5_port} ...")
        exit_ip = verify_socks5("127.0.0.1", socks5_port, username, password)
        if not exit_ip:
            print("❌ 代理验证失败，无法获取出口 IP")
            sys.exit(1)
    print(f"✅ 出口 IP: {exit_ip}")

    # --- Determine ports ---
    local_port = args.local_port
    if local_port == 0:
        local_port = find_free_port(11080)
    elif not is_port_free(local_port):
        print(f"⚠️  端口 {local_port} 已占用，自动分配...")
        local_port = find_free_port(local_port + 1)

    cdp_port = args.cdp_port
    if not is_port_free(cdp_port):
        print(f"⚠️  CDP 端口 {cdp_port} 已占用，自动分配...")
        cdp_port = find_free_port(cdp_port + 1)

    if not local_port or not cdp_port:
        print("❌ 无法找到可用端口")
        sys.exit(1)

    # --- Prepare Chrome user data dir ---
    profile_dir = os.path.join(PROFILES_DIR, args.profile_name or exit_ip)
    os.makedirs(profile_dir, exist_ok=True)

    # --- Kill existing Chrome with same profile (avoid reuse) ---
    existing_pids = _find_chrome_pids_by_profile(profile_dir)
    if existing_pids:
        print(f"⚠️  检测到已有 Chrome 使用相同 profile（PID: {min(existing_pids)}），正在关闭...")
        _kill_chrome_by_profile(profile_dir, silent=True)
        # Wait for port to be released
        time.sleep(2)
        # Re-check CDP port — it may now be free
        if not is_port_free(args.cdp_port):
            # Still occupied by something else, keep the auto-assigned port
            pass
        else:
            # Original port is now free, use it
            cdp_port = args.cdp_port

    # --- Print config ---
    print()
    print("🚀 Chrome Proxy Launcher")
    print("═" * 44)
    print(f"  出口 IP:     {exit_ip}")
    print(f"  SOCKS5 上游:  127.0.0.1:{socks5_port}")
    print(f"  本地转发:     127.0.0.1:{local_port}")
    print(f"  CDP 端口:     {cdp_port}")
    print(f"  User Data:   {profile_dir}")
    print("═" * 44)

    # --- Start forwarder ---
    forwarder_pid = start_forwarder(local_port, "127.0.0.1", socks5_port, username, password)
    if not forwarder_pid:
        print("❌ SOCKS5 转发器启动失败")
        sys.exit(1)
    print(f"✅ SOCKS5 转发器已启动 (PID: {forwarder_pid})")

    # --- Start Chrome ---
    # 构建要打开的 URL 列表
    url = args.url or "https://httpbin.org/ip"
    urls = [url]
    if args.also_open:
        urls.extend(args.also_open)
    chrome_proc = subprocess.Popen([
        CHROME_PATH,
        f"--user-data-dir={profile_dir}",
        f"--proxy-server=socks5://127.0.0.1:{local_port}",
        "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
        f"--remote-debugging-port={cdp_port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        *urls,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"✅ Chrome 已启动 (PID: {chrome_proc.pid})")
    print(f"   CDP: http://127.0.0.1:{cdp_port}")

    # --- Save state ---
    save_pids(exit_ip, forwarder_pid, chrome_proc.pid, local_port, cdp_port)

    # --- Wait for verification ---
    time.sleep(2)
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/version", timeout=5)
        info = json.loads(resp.read())
        print(f"   Browser: {info.get('Browser', 'unknown')}")
    except:
        print("   ⚠️  CDP 端口尚未响应（Chrome 可能还在启动）")

    print(f"\n💡 停止命令: python3 {os.path.basename(__file__)} --stop")


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


if __name__ == "__main__":
    main()
