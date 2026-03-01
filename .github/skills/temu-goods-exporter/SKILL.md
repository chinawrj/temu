---
name: temu-goods-exporter
description: >-
  Temu Agent Seller（跨境卖家后台）商品管理工具集。
  包含两个脚本：
  1) temu_goods_exporter.py — 导出完整商品列表到 Excel（自动切换"全部"标签、每页500条，从 React 组件提取全量数据）。
  2) temu_stock_setter.py — 设置指定 SKU 的库存数量（支持增加/减少/清零，自动滚动定位、弹窗交互、低库存警告确认）。
  纯 Python 标准库实现，通过 CDP 控制 Chrome，无需额外安装依赖。
argument-hint: >-
  导出商品列表时提供：CDP 端口号（默认 9222）、输出文件路径。
  设置库存时提供：目标 SKU ID、目标库存数量、CDP 端口号（默认 9222）。
---

# Temu 商品管理工具集

本 skill 包含两个工具，用于管理 Temu Agent Seller 后台的商品。

## 脚本列表

| 脚本 | 功能 |
|------|------|
| `temu_goods_exporter.py` | 导出全部商品列表到 Excel |
| `temu_stock_setter.py` | 设置指定 SKU 的库存数量 |

---

## 1. 商品列表导出 (`temu_goods_exporter.py`)

### 功能

从 Temu Agent Seller 后台导出完整商品列表（SPU + SKU 明细）到 Excel 文件。

### 自动化流程

1. 通过 CDP 连接 Chrome 浏览器
2. 确认/导航到商品列表页面
3. **自动切换到"全部"标签**（避免只导出部分筛选结果）
4. 设置每页显示 500 条（确保一次加载所有商品）
5. 从 React 组件的 `dataSource` 提取全量数据
6. 输出终端表格 + Excel 文件

### 导出字段

| 字段 | 说明 |
|------|------|
| SPU ID / SKC ID / SKU ID | 商品标识 |
| 商品名称 | 产品标题 |
| 类目 | 叶子类目 + 完整类目路径 |
| 颜色/规格 | SKU 规格属性 |
| 库存 | 当前库存数量 |
| 价格 | 供货价（元） |
| 状态 | 待提交/已上架/已下架/审核中等 |
| 发货模式 | 卖家自发货/平台发货 |
| 体积/重量 | 包装信息 |
| 供应商编码 | extCode |

### 用法

```bash
# 基本用法（CDP 端口 9222，输出到当前目录）
python3 temu_goods_exporter.py

# 指定输出路径
python3 temu_goods_exporter.py --output /tmp/temu_goods.xlsx

# 指定 CDP 端口
python3 temu_goods_exporter.py --cdp-port 9222

# 完整参数
python3 temu_goods_exporter.py --cdp-host 127.0.0.1 --cdp-port 9222 --output goods.xlsx
```

### 输出示例

```
商品总数: 78 个SPU, 197 个SKU
非0库存: 0 个SKU
✅ Excel 已导出: /tmp/temu_goods.xlsx (197 行数据)
```

---

## 2. 库存设置工具 (`temu_stock_setter.py`)

### 功能

设置指定 SKU 的库存为目标数量。自动处理：
- 虚拟滚动表格定位（支持大量商品翻页）
- 多 SKU 的 SPU 下精准匹配目标 SKU（通过颜色+规格组合）
- 修改库存弹窗交互（增加/减少方向、数量输入）
- 低库存警告自动确认
- 修改后验证结果

### 用法

```bash
# 将指定 SKU 库存设为 0
python3 temu_stock_setter.py --sku 60437653579 --stock 0

# 将指定 SKU 库存设为 100
python3 temu_stock_setter.py --sku 60437653579 --stock 100

# 指定 CDP 端口
python3 temu_stock_setter.py --sku 60437653579 --stock 0 --cdp-port 9222
```

### 参数说明

| 参数 | 必需 | 说明 |
|------|------|------|
| `--sku` | 是 | 目标 SKU ID |
| `--stock` | 是 | 目标库存数量 |
| `--cdp-port` | 否 | CDP 端口（默认 9222） |
| `--cdp-host` | 否 | CDP 主机（默认 127.0.0.1） |

### 执行流程

1. 连接 CDP → 确认在商品列表页
2. 设置每页 500 条 → 在 React dataSource 中查找目标 SKU
3. 计算当前库存与目标差值 → 确定增加/减少方向和数量
4. 滚动到对应行 → 点击「修改库存」按钮
5. 在弹窗中选择方向、输入数量 → 点击确认
6. 处理低库存警告弹窗 → 验证修改结果

### 输出示例

```
[3/8] 查找 SKU 60437653579...
  ✓ 找到: 4/8个装 15.8英寸壁挂式悬浮置物架...
    当前库存: 78
    操作: 减少 78
...
  ✓ 成功! SKU 60437653579 库存已从原值修改为 0
```

---

## 前提条件

- Chrome 浏览器已通过代理启动并开启 CDP 远程调试端口
- 浏览器已登录 Temu Agent Seller 后台
- 推荐配合 `chrome-proxy-launcher` skill 使用

## 常见配合使用

```bash
# 1. 先扫描代理
sudo python3 .github/skills/zhanfu-proxy-scanner/zhanfu_proxy_scanner.py

# 2. 启动 Chrome
python3 .github/skills/chrome-proxy-launcher/chrome_proxy_launcher.py \
  --socks5-port 12631 --user rkw8puvj --pass 3TG0yPu7nWK5h16Y

# 3. 导出商品列表
python3 .github/skills/temu-goods-exporter/temu_goods_exporter.py \
  --cdp-port 9222 --output /tmp/temu_goods.xlsx

# 4. 清零指定 SKU 库存
python3 .github/skills/temu-goods-exporter/temu_stock_setter.py \
  --sku 60437653579 --stock 0

# 5. 批量清零所有非零库存 SKU（结合导出结果）
# 先用 exporter 找出非零 SKU，再逐个调用 stock_setter
```
