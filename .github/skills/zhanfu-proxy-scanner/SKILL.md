---
name: zhanfu-proxy-scanner
description: >
  Scan and extract proxy credentials from running 站斧 (ZhanFu) anti-detect browser's
  xray proxy instances. Discovers all xray processes, captures SOCKS5/HTTP authentication
  credentials by forcing browser reconnection, verifies proxy connectivity and retrieves
  exit IPs. Use this skill when the user wants to reuse ZhanFu browser's proxy in other
  browsers or tools, or needs to inspect ZhanFu's xray proxy configuration.
argument-hint: "[run|help]"
---

# 站斧浏览器 Xray 代理扫描器

## 能力

此 skill 能够自动完成以下任务：

1. **发现代理实例** — 扫描系统中所有运行的站斧浏览器 xray 进程，获取 SOCKS5/HTTP 监听端口
2. **抓取认证凭据** — 通过短暂暂停 xray 进程触发浏览器重连，从 SOCKS5 握手中解析用户名/密码
3. **验证并获取出口 IP** — 用捕获的凭据建立代理连接，返回每个实例的出口 IP 地址
4. **输出可用配置** — 提供可直接使用的 curl 命令和浏览器代理配置

## 何时使用

- 用户想在其他浏览器（Chrome/Firefox/Edge）中复用站斧浏览器的代理
- 用户想查看当前站斧浏览器的代理连接信息（端口、凭据、出口 IP）
- 用户需要用 curl 或其他工具通过站斧的代理发送请求

## 使用方式

运行扫描脚本（需要 root 权限，因为要使用 tcpdump 和发送进程信号）：

```bash
sudo python3 .github/skills/zhanfu-proxy-scanner/zhanfu_proxy_scanner.py
```

## 工作原理

### 技术细节

站斧浏览器内嵌了 xray 代理，每个浏览器窗口对应一个独立的 xray 实例：
- xray 通过 `stdin:` 接收加密配置，配置文件不可直接读取
- 每个实例监听两个本地端口：SOCKS5（偶数端口）和 HTTP（SOCKS5 + 1）
- 代理需要用户名/密码认证（RFC 1929 SOCKS5 Username/Password Authentication）

### 凭据抓取流程

1. 启动 `tcpdump -i lo0` 监听 xray 的 SOCKS5 端口
2. 发送 `SIGSTOP` 暂停 xray 进程 2 秒 → 浏览器的 TCP 连接超时断开
3. 发送 `SIGCONT` 恢复 → 浏览器自动重连产生新的 SOCKS5 认证握手
4. 从抓包数据中解析 RFC 1929 认证数据包：`\x01 <ulen> <username> <plen> <password>`

### SOCKS5 认证包格式

```
+----+------+----------+------+----------+
|VER | ULEN |  UNAME   | PLEN |  PASSWD  |
+----+------+----------+------+----------+
| 1  |  1   | 1 to 255 |  1   | 1 to 255 |
+----+------+----------+------+----------+
```

- VER: 0x01
- ULEN: 用户名长度（战斧通常为 8 字节）
- PLEN: 密码长度（战斧通常为 16 字节）

## 输出示例

```
============================================================
  📋 扫描结果汇总
============================================================

  实例 #1 (PID: 47835)
  ──────────────────────────────────────────────────
  状态:      ✅ 可用
  SOCKS5:    127.0.0.1:12627
  HTTP:      127.0.0.1:12628
  用户名:    Nv6iwhvG
  密码:      lLBh5LitxNDU9vkX
  出口 IP:   121.4.37.161

  curl 命令:
    curl -x socks5://Nv6iwhvG:lLBh5LitxNDU9vkX@127.0.0.1:12627 httpbin.org/ip
    curl -x http://Nv6iwhvG:lLBh5LitxNDU9vkX@127.0.0.1:12628 httpbin.org/ip
```

## 在其他浏览器中配置

获取到凭据后，按以下方式配置：

### Firefox
1. 设置 → 网络设置 → 手动代理配置
2. HTTP 代理: `127.0.0.1`，端口: `<HTTP端口>`
3. 勾选 "也将此代理用于 HTTPS"
4. 访问网页时弹出认证框，输入用户名和密码

### Chrome (通过命令行)
```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --proxy-server="socks5://127.0.0.1:<SOCKS5端口>"
```
Chrome 原生不支持命令行传递 SOCKS5 认证，建议使用 SwitchyOmega 扩展。

### curl
```bash
curl -x socks5://<用户名>:<密码>@127.0.0.1:<SOCKS5端口> <目标URL>
curl -x http://<用户名>:<密码>@127.0.0.1:<HTTP端口> <目标URL>
```

## 注意事项

- **凭据是动态的** — 每次站斧浏览器重启或新开窗口后凭据会变，需重新扫描
- **依赖站斧运行** — 代理仅在站斧浏览器运行时可用
- **需要 root 权限** — tcpdump 抓包和 SIGSTOP/SIGCONT 都需要 sudo
- **仅支持 macOS** — 使用了 BSD loopback 接口和 macOS 特有工具

## 代理缓存

扫描成功后会自动将可用实例信息保存到 `proxy_cache.json` 文件：

```json
{
  "updated_at": "2026-02-20T10:30:00",
  "instances": [
    {
      "pid": 6919,
      "socks5_port": 12631,
      "http_port": 12632,
      "username": "rkw8puvj",
      "password": "3TG0yPu7nWK5h16Y",
      "exit_ip": "66.80.56.14"
    }
  ]
}
```

缓存文件用于 Temu 卖家助手 Agent 的自动化工作流，避免每次会话都需要重新扫描。
缓存失效条件：更新时间超过 24 小时，或 xray 进程端口发生变化。
