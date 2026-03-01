---
name: chrome-proxy-launcher
description: >
  Launch Google Chrome with a SOCKS5 proxy from 站斧 (ZhanFu) xray instances.
  Each exit IP gets its own isolated Chrome user-data directory and CDP debug port.
  Includes a built-in SOCKS5 auth forwarder so Chrome can use authenticated proxies.
  Use this skill when the user wants to open Chrome through a specific ZhanFu proxy,
  or needs a CDP-enabled browser session with a particular exit IP.
  Also supports no-proxy mode for launching Chrome with persistent storage and CDP only.
argument-hint: "[--no-proxy | --exit-ip IP | --socks5-port PORT] [--cdp-port PORT]"
---

# Chrome 启动器（代理 / 无代理）

## 能力

此 skill 能够：

1. **无代理启动 Chrome** — 使用持久化 profile 和 CDP 调试端口，不通过任何代理
2. **自动发现代理** — 结合 `zhanfu-proxy-scanner` skill 获取可用的 xray 代理实例
3. **启动 SOCKS5 转发器** — 在本地启动无认证的 SOCKS5 转发器，桥接到带认证的 xray 上游
4. **启动 Chrome** — 使用指定代理启动 Chrome，每个出口 IP 使用独立的 user-data 目录
5. **开启 CDP 调试** — 默认启用 `--remote-debugging-port`，支持自动化和 Tampermonkey 脚本开发

## 何时使用

- 用户想启动一个带 CDP 和持久化存储的 Chrome（无代理）
- 用户想通过特定出口 IP 启动 Chrome 浏览器（代理模式）
- 用户需要一个带 CDP 调试端口的浏览器会话
- 用户说 "启动浏览器" / "打开 Chrome" / "不需要代理"

## 使用方式

### 无代理模式（推荐用于本地开发/脚本调试）

```bash
# 基本用法：启动 Chrome，持久化存储，CDP 端口 9222
python3 chrome_proxy_launcher.py --no-proxy

# 指定 CDP 端口
python3 chrome_proxy_launcher.py --no-proxy --cdp-port 9223

# 指定 profile 名称
python3 chrome_proxy_launcher.py --no-proxy --profile-name my-dev

# 打开指定 URL
python3 chrome_proxy_launcher.py --no-proxy --url https://example.com --also-open https://erp.91miaoshou.com
```

无代理模式的 Chrome 启动参数：
- `--user-data-dir=profiles/no-proxy`（持久化存储）
- `--remote-debugging-port=9222`（CDP 调试端口）
- `--remote-allow-origins=*`（允许 CDP WebSocket 连接，**必须**）
- `--no-first-run --no-default-browser-check`

### 代理模式

```bash
# 通过出口 IP 启动（会自动扫描并匹配）
python3 chrome_proxy_launcher.py --exit-ip 66.80.56.14

# 通过 xray SOCKS5 端口直接启动（需提供凭据）
python3 chrome_proxy_launcher.py --socks5-port 12631 --user rkw8puvj --pass 3TG0yPu7nWK5h16Y

# 指定 CDP 端口
python3 chrome_proxy_launcher.py --exit-ip 66.80.56.14 --cdp-port 9333

# 指定本地转发端口
python3 chrome_proxy_launcher.py --exit-ip 66.80.56.14 --local-port 11080
```

### 管理命令

```bash
# 查看运行状态
python3 chrome_proxy_launcher.py --status

# 停止所有实例
python3 chrome_proxy_launcher.py --stop
```

### 自动模式（配合 zhanfu-proxy-scanner）

1. 先运行 `sudo python3 zhanfu_proxy_scanner.py` 获取代理信息
2. 再运行 `python3 chrome_proxy_launcher.py --exit-ip <目标IP>` 启动 Chrome

## 架构

### 代理模式
```
站斧 xray (127.0.0.1:12631, 需要认证)
        ↑
SOCKS5 转发器 (127.0.0.1:11080, 无认证)
        ↑
Chrome (--proxy-server=socks5://127.0.0.1:11080)
```

### 无代理模式
```
Chrome (直连, 使用持久化 profile + CDP)
```

## Chrome User Data 目录

每个启动模式使用独立的 user-data 目录，存储在 skill 目录下：

```
chrome-proxy-launcher/
├── profiles/
│   ├── no-proxy/            ← 无代理模式默认 profile
│   ├── 66.80.56.14/         ← 代理模式：按出口 IP 隔离
│   └── 121.4.37.161/        ← 代理模式：另一个 IP
├── chrome_proxy_launcher.py
└── SKILL.md
```

这样每个模式/IP 的 cookies、登录状态、扩展（如 Tampermonkey）等都是隔离的。

## 输出示例

### 无代理模式
```
🚀 Chrome Launcher (no proxy)
════════════════════════════════════════════
  Profile:     no-proxy
  CDP 端口:     9222
  User Data:   ./profiles/no-proxy
════════════════════════════════════════════
✅ Chrome 已启动 (PID: 12345)
   CDP: http://127.0.0.1:9222
   Browser: Chrome/131.0.6778.86
```

### 代理模式
```
🚀 Chrome Proxy Launcher
════════════════════════════════════════════
  出口 IP:     66.80.56.14
  SOCKS5 上游:  127.0.0.1:12631
  本地转发:     127.0.0.1:11080
  CDP 端口:     9222
  User Data:   ./profiles/66.80.56.14
════════════════════════════════════════════
✅ SOCKS5 转发器已启动 (PID: 12345)
✅ Chrome 已启动 (PID: 12346)
   CDP: http://127.0.0.1:9222
```

## 重要参数说明

| 参数 | 说明 |
|------|------|
| `--no-proxy` | 无代理模式，仅启动 Chrome + CDP |
| `--profile-name` | 自定义 profile 目录名 |
| `--exit-ip` | 目标出口 IP（代理模式） |
| `--cdp-port` | CDP 远程调试端口（默认 9222） |
| `--url` | 启动时打开的 URL |
| `--also-open` | 额外打开的 URL 列表 |
| `--stop` | 停止所有实例 |
| `--status` | 查看运行状态 |

## 注意事项

- **`--remote-allow-origins=*`** — 必须添加，否则 CDP WebSocket 连接会返回 403
- **端口冲突** — 如果 CDP 端口已占用，会自动选择其他端口；也会自动关闭使用相同 profile 的旧 Chrome
- **依赖 zhanfu-proxy-scanner** — 代理自动模式需要先运行代理扫描器（需 sudo）
- **凭据动态变化** — 站斧每次重启后凭据会变，需重新扫描
- **仅支持 macOS** — Chrome 路径和系统工具为 macOS 环境
