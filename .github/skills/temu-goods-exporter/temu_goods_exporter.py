#!/usr/bin/env python3
"""
Temu Agent Seller 商品列表抓取工具 v2
======================================
通过 Chrome DevTools Protocol (CDP) 从已登录的 Temu 卖家后台抓取商品列表。
直接从 React 组件的 dataSource 提取数据，确保获取所有商品（自动切换到每页500条）。
每个 SKU 独立一行输出到终端和 Excel (.xlsx) 文件。

依赖: 无外部依赖，纯 Python 标准库实现。
前提: Chrome 需以 --remote-debugging-port=9222 启动并已登录 Temu 卖家后台。

用法:
    python3 temu_goods_exporter.py [--output goods.xlsx] [--cdp-port 9222]
"""

import socket, json, base64, os, struct, time, sys, argparse
import zipfile, io, datetime
from xml.sax.saxutils import escape as xml_escape


# ============================================================
# Part 1: Minimal XLSX Writer (pure stdlib, no openpyxl needed)
# ============================================================

class SimpleXlsxWriter:
    """Minimal xlsx writer using only stdlib (zipfile + XML)."""

    def __init__(self):
        self.rows = []
        self.col_widths = []

    def add_row(self, values, is_header=False):
        self.rows.append([(v, is_header) for v in values])
        for i, v in enumerate(values):
            w = len(str(v)) + 2
            if i >= len(self.col_widths):
                self.col_widths.append(w)
            else:
                self.col_widths[i] = max(self.col_widths[i], w)

    def _cell_ref(self, row, col):
        letters = ""
        c = col
        while c >= 0:
            letters = chr(65 + c % 26) + letters
            c = c // 26 - 1
        return f"{letters}{row + 1}"

    def save(self, filepath):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('[Content_Types].xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>''')

            zf.writestr('_rels/.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>''')

            zf.writestr('xl/_rels/workbook.xml.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>''')

            zf.writestr('xl/workbook.xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="商品列表" sheetId="1" r:id="rId1"/></sheets>
</workbook>''')

            zf.writestr('xl/styles.xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/></font>
  </fonts>
  <fills count="3">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/></patternFill></fill>
  </fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf/></cellStyleXfs>
  <cellXfs count="2">
    <xf fontId="0" fillId="0" borderId="0"/>
    <xf fontId="1" fillId="2" borderId="0" applyFont="1" applyFill="1"/>
  </cellXfs>
</styleSheet>''')

            strings = []
            string_map = {}
            for row in self.rows:
                for val, _ in row:
                    s = str(val)
                    if s not in string_map:
                        string_map[s] = len(strings)
                        strings.append(s)

            ss_xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            ss_xml += f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{sum(len(r) for r in self.rows)}" uniqueCount="{len(strings)}">\n'
            for s in strings:
                ss_xml += f'  <si><t>{xml_escape(s)}</t></si>\n'
            ss_xml += '</sst>'
            zf.writestr('xl/sharedStrings.xml', ss_xml)

            sheet = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            sheet += '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
            sheet += '<cols>\n'
            for i, w in enumerate(self.col_widths):
                w = min(w, 60)
                sheet += f'  <col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>\n'
            sheet += '</cols>\n'
            sheet += '<sheetData>\n'
            for r_idx, row in enumerate(self.rows):
                sheet += f'  <row r="{r_idx+1}">\n'
                for c_idx, (val, is_hdr) in enumerate(row):
                    ref = self._cell_ref(r_idx, c_idx)
                    style = ' s="1"' if is_hdr else ''
                    si = string_map[str(val)]
                    sheet += f'    <c r="{ref}" t="s"{style}><v>{si}</v></c>\n'
                sheet += '  </row>\n'
            sheet += '</sheetData>\n'
            if self.rows:
                ncols = len(self.rows[0])
                last_col_letter = self._cell_ref(0, ncols - 1).rstrip('0123456789')
                sheet += f'<autoFilter ref="A1:{last_col_letter}{len(self.rows)}"/>\n'
            sheet += '</worksheet>'
            zf.writestr('xl/worksheets/sheet1.xml', sheet)

        with open(filepath, 'wb') as f:
            f.write(buf.getvalue())


# ============================================================
# Part 2: CDP WebSocket Client (pure stdlib)
# ============================================================

class CDPClient:
    def __init__(self, host="127.0.0.1", port=9222):
        self.host = host
        self.port = port
        self.sock = None
        self._msg_id = 0

    def connect(self, page_id=None):
        if page_id is None:
            page_id = self._find_page()
        path = f"/devtools/page/{page_id}"
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))
        self.sock.settimeout(30)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\n"
               f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.sock.recv(4096)

    def _find_page(self):
        import urllib.request
        data = urllib.request.urlopen(f"http://{self.host}:{self.port}/json/list").read()
        tabs = json.loads(data)
        for t in tabs:
            if 'agentseller.temu.com/goods' in t.get('url', ''):
                return t['id']
        for t in tabs:
            if 'temu.com' in t.get('url', ''):
                return t['id']
        for t in tabs:
            if t.get('type') == 'page' and t.get('url', '').startswith('http'):
                return t['id']
        raise RuntimeError("No suitable page found")

    def _ws_send(self, data):
        payload = data.encode("utf-8")
        frame = bytearray([0x81])
        mask_key = os.urandom(4)
        length = len(payload)
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack("!H", length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack("!Q", length))
        frame.extend(mask_key)
        masked = bytearray(payload)
        for i in range(len(masked)):
            masked[i] ^= mask_key[i % 4]
        frame.extend(masked)
        self.sock.sendall(frame)

    def _ws_recv(self):
        def read_exact(n):
            buf = b""
            while len(buf) < n:
                chunk = self.sock.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError("WebSocket closed")
                buf += chunk
            return buf
        all_data = b""
        while True:
            header = read_exact(2)
            fin = header[0] & 0x80
            opcode = header[0] & 0x0F
            length = header[1] & 0x7F
            masked = header[1] & 0x80
            if length == 126:
                length = struct.unpack("!H", read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", read_exact(8))[0]
            if masked:
                mk = read_exact(4)
                data = bytearray(read_exact(length))
                for i in range(len(data)):
                    data[i] ^= mk[i % 4]
                data = bytes(data)
            else:
                data = read_exact(length)
            if opcode == 0x08:
                raise ConnectionError("WebSocket closed by peer")
            if opcode == 0x09:
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
                continue
            all_data += data
            if fin:
                break
        return all_data.decode("utf-8", errors="replace")

    def evaluate(self, expression):
        self._msg_id += 1
        msg = {"id": self._msg_id, "method": "Runtime.evaluate",
               "params": {"expression": expression, "returnByValue": True, "awaitPromise": True}}
        self._ws_send(json.dumps(msg))
        while True:
            resp = json.loads(self._ws_recv())
            if resp.get("id") == self._msg_id:
                result = resp.get("result", {}).get("result", {})
                if "exceptionDetails" in resp.get("result", {}):
                    desc = resp["result"]["exceptionDetails"].get("exception", {}).get("description", "Unknown JS error")
                    raise RuntimeError(f"JS Error: {desc}")
                return result.get("value", result.get("description", ""))

    def close(self):
        if self.sock:
            self.sock.close()


# ============================================================
# Part 3: JavaScript
# ============================================================

JS_SET_PAGESIZE_500 = r"""
(function() {
    const pagerEl = document.querySelector('[data-testid="beast-core-pagination"]');
    if (!pagerEl) return JSON.stringify({action: 'no_pager'});
    const fiberKey = Object.keys(pagerEl).find(k => k.startsWith('__reactFiber'));
    if (!fiberKey) return JSON.stringify({action: 'no_fiber'});
    let fiber = pagerEl[fiberKey]; let d = 0;
    while (fiber && d < 20) {
        const props = fiber.memoizedProps || {};
        if (props.pageSize !== undefined && props.total !== undefined) {
            if (props.pageSize >= 500) {
                return JSON.stringify({action: 'already_500', pageSize: props.pageSize, total: props.total});
            }
            const hdr = document.querySelector('[class*=PGT_sizeChanger] [data-testid="beast-core-select-header"]');
            if (hdr) { hdr.click(); return JSON.stringify({action: 'opened_dropdown', pageSize: props.pageSize, total: props.total}); }
            return JSON.stringify({action: 'no_dropdown', pageSize: props.pageSize, total: props.total});
        }
        fiber = fiber.return; d++;
    }
    return JSON.stringify({action: 'no_props'});
})()
"""

JS_CLICK_500_OPTION = r"""
(function() {
    const opts = document.querySelectorAll('[data-testid="beast-core-select-option"], [role="option"]');
    for (const o of opts) { if (o.innerText.trim() === '500') { o.click(); return JSON.stringify({ok: true}); } }
    const allLi = document.querySelectorAll('li');
    for (const li of allLi) { if (li.innerText.trim() === '500') { li.click(); return JSON.stringify({ok: true, m: 'li'}); } }
    return JSON.stringify({ok: false, count: opts.length});
})()
"""

JS_EXTRACT_ALL = r"""
(function() {
    const table = document.querySelector('table');
    if (!table) return JSON.stringify({error: 'no_table'});
    const fk = Object.keys(table).find(k => k.startsWith('__reactFiber'));
    if (!fk) return JSON.stringify({error: 'no_fiber'});
    let fiber = table[fk]; let d = 0; let ds = null;
    while (fiber && d < 30) {
        if (fiber.memoizedProps && fiber.memoizedProps.dataSource) { ds = fiber.memoizedProps.dataSource; break; }
        fiber = fiber.return; d++;
    }
    if (!ds) return JSON.stringify({error: 'no_datasource'});

    const rows = ds.map(item => {
        const sku = item.productSkuSummaryItem || {};
        const specList = sku.productSkuSpecList || [];
        let color = '-', size = '-';
        for (const spec of specList) {
            const pn = (spec.parentSpecName || '').toLowerCase();
            if (pn.includes('颜色') || pn.includes('color')) color = spec.specName || '-';
            else size = spec.specName || '-';
        }
        if (specList.length === 1 && size === '-') { /* only color */ }

        let price = null;
        const sp = sku.siteSupplierPrices || [];
        if (sp.length > 0) price = sp[0].supplierPrice;
        if (price == null) price = sku.supplierPrice;
        const priceYuan = price != null ? (price / 100).toFixed(2) : '-';

        let stock = 0;
        const ss = sku.productSkuSemiManagedStock || {};
        if (ss.skuStockQuantity != null) stock = ss.skuStockQuantity;

        const cats = item.categories || {};
        const cp = [];
        for (let i = 1; i <= 4; i++) { const c = cats['cat' + i]; if (c && c.catName) cp.push(c.catName); }

        const leafCat = item.leafCat ? item.leafCat.catName : '-';
        const sm = {0:'已上架',1:'已下架',2:'审核中',3:'审核失败',7:'待提交',4:'部分上架'};
        const status = sm[item.skcStatus] || ('状态'+item.skcStatus);

        let shipMode = '-';
        const se = sku.productSkuSaleExtAttr || {};
        if (se.productSkuShippingMode === 1) shipMode = '卖家自发货';
        else if (se.productSkuShippingMode === 2) shipMode = '平台发货';

        const wh = sku.productSkuWhExtAttr || {};
        const vol = wh.productSkuVolume || {};
        const wt = wh.productSkuWeight || {};
        const volStr = vol.len && vol.width && vol.height ? `${vol.len/10}x${vol.width/10}x${vol.height/10}cm` : '-';
        const wtStr = wt.value ? `${wt.value/1000}g` : '-';

        return {
            productId: item.productId,
            productSkcId: item.productSkcId,
            productSkuId: sku.productSkuId || item.productSkuId,
            productName: (item.productName || '').trim(),
            leafCat, catPath: cp.join(' > '),
            color, size, stock, priceYuan, status, shipMode,
            extCode: sku.extCode || '-',
            volume: volStr, weight: wtStr,
            rowSpan: item.rowSpan,
        };
    });

    return JSON.stringify({total: ds.length, rows});
})()
"""

JS_COUNT_DATASOURCE = r"""
(function() {
    const table = document.querySelector('table');
    if (!table) return '0';
    const fk = Object.keys(table).find(k => k.startsWith('__reactFiber'));
    if (!fk) return '0';
    let f = table[fk]; let d = 0;
    while (f && d < 30) {
        if (f.memoizedProps && f.memoizedProps.dataSource) return String(f.memoizedProps.dataSource.length);
        f = f.return; d++;
    }
    return '0';
})()
"""

JS_CLICK_ALL_TAB = r"""
(function() {
    // Find tab container and click "全部" tab
    var tabs = document.querySelectorAll('[role="tab"], [class*="tabItem"], [class*="TabItem"]');
    for (var i = 0; i < tabs.length; i++) {
        var text = tabs[i].innerText.trim();
        if (/^全部/.test(text)) {
            var isActive = tabs[i].classList.toString().includes('active') ||
                           tabs[i].classList.toString().includes('Active') ||
                           tabs[i].getAttribute('aria-selected') === 'true';
            if (isActive) return JSON.stringify({action: 'already_active', text: text});
            tabs[i].click();
            return JSON.stringify({action: 'clicked', text: text});
        }
    }
    return JSON.stringify({action: 'not_found'});
})()
"""


# ============================================================
# Part 4: Logic
# ============================================================

def ensure_all_tab(cdp):
    """Ensure the '全部' (All) tab is selected so all products are shown."""
    result = json.loads(cdp.evaluate(JS_CLICK_ALL_TAB))
    action = result.get('action')
    if action == 'already_active':
        print(f"  已选中 \"{result.get('text')}\" 标签")
    elif action == 'clicked':
        print(f"  已切换到 \"{result.get('text')}\" 标签，等待数据加载...")
        time.sleep(3)
        for _ in range(10):
            cnt = cdp.evaluate(JS_COUNT_DATASOURCE)
            if cnt and int(cnt) > 0:
                print(f"  ✓ 数据加载完成: {cnt} 条 SKU 记录")
                return
            time.sleep(1)
        print("  ⚠ 等待数据加载超时")
    else:
        print("  ⚠ 未找到 \"全部\" 标签，将使用当前筛选")


def ensure_pagesize_500(cdp):
    result = json.loads(cdp.evaluate(JS_SET_PAGESIZE_500))
    if result.get('action') == 'already_500':
        print(f"  页面已设置为每页 {result['pageSize']} 条 (共 {result['total']} 条商品)")
        return
    if result.get('action') == 'opened_dropdown':
        print(f"  当前每页 {result['pageSize']} 条，正在切换为 500...")
        time.sleep(0.5)
        click = json.loads(cdp.evaluate(JS_CLICK_500_OPTION))
        if click.get('ok'):
            print("  ✓ 已选择每页 500 条，等待数据加载...")
            time.sleep(3)
            for _ in range(15):
                cnt = int(cdp.evaluate(JS_COUNT_DATASOURCE))
                if cnt > int(result.get('pageSize', 20)):
                    print(f"  ✓ 数据加载完成: {cnt} 条 SKU 记录")
                    return
                time.sleep(1)
            print(f"  ⚠ 等待超时，当前 {cnt} 条")
        else:
            print("  ⚠ 未找到500选项")
    elif result.get('action') == 'no_pager':
        print("  未检测到分页控件")
    else:
        print(f"  分页状态: {result}")


def ensure_on_goods_page(cdp):
    """Ensure we are on the goods list page with table loaded."""
    url = cdp.evaluate("window.location.href")
    if 'goods/list' not in str(url):
        print(f"  当前页面: {url}")
        print("  正在导航到商品列表...")
        cdp.evaluate("window.location.href = 'https://agentseller.temu.com/goods/list'")
        time.sleep(5)
    else:
        print(f"  已在商品列表页面")

    # Wait for table to load
    for _ in range(15):
        cnt = cdp.evaluate("document.querySelectorAll('table').length")
        if cnt and int(cnt) > 0:
            print("  ✓ 表格已加载")
            return
        time.sleep(1)
    raise RuntimeError("表格未加载")


def extract_goods(cdp):
    """Extract all goods data from React dataSource."""
    raw = cdp.evaluate(JS_EXTRACT_ALL)
    data = json.loads(raw)
    if 'error' in data:
        raise RuntimeError(f"提取失败: {data['error']}")
    return data['rows']


# ============================================================
# Part 5: Terminal Output
# ============================================================

STATUS_COLORS = {'待提交':'\033[33m','已上架':'\033[32m','已下架':'\033[90m','审核中':'\033[36m','审核失败':'\033[91m'}

def print_goods_table(rows):
    if not rows:
        print("  未找到商品数据"); return

    products = {}
    for r in rows:
        pid = r['productId']
        if pid not in products: products[pid] = []
        products[pid].append(r)

    total_products = len(products)
    total_skus = len(rows)
    non_zero = sum(1 for r in rows if r['stock'] > 0)

    print(f"\n{'═'*110}")
    print(f"  商品总数: {total_products} 个SPU, {total_skus} 个SKU")
    print(f"  非0库存: {non_zero} 个SKU")
    print(f"{'═'*110}\n")

    hdr = f"{'#':>3} | {'商品名称':<42} | {'类目':<10} | {'SPU ID':<12} | {'SKU ID':<12} | {'颜色/属性':<22} | {'库存':>4} | {'价格(¥)':>10} | {'状态':<6}"
    print(hdr)
    print("─" * 130)

    idx = 0
    for pid, skus in products.items():
        for si, sku in enumerate(skus):
            idx += 1
            name = sku['productName'][:42] if si == 0 else '  └─'
            cat = sku['leafCat'][:10] if si == 0 else ''
            spu = str(sku['productId']) if si == 0 else ''
            sku_id = str(sku['productSkuId'])
            color = sku['color'][:22]
            stock_str = str(sku['stock'])
            if sku['stock'] > 0:
                stock_str = f"\033[91m{sku['stock']}\033[0m"
            price = f"¥{sku['priceYuan']}" if sku['priceYuan'] != '-' else '-'
            status = sku['status'][:6]
            sc = STATUS_COLORS.get(sku['status'], '')
            if sc: status = f"{sc}{status}\033[0m"
            print(f"{idx:>3} | {name:<42} | {cat:<10} | {spu:<12} | {sku_id:<12} | {color:<22} | {stock_str:>4} | {price:>10} | {status}")
        if skus:
            print("─" * 130)


# ============================================================
# Part 6: Excel Export
# ============================================================

def export_to_xlsx(rows, filepath):
    xlsx = SimpleXlsxWriter()
    headers = ['序号', '商品名称', '类目(叶子)', '类目路径',
               'SPU ID', 'SKC ID', 'SKU ID', '供应商编码',
               '颜色', '尺码/规格', '库存', '价格(¥)',
               '发货模式', '状态', '体积', '重量']
    xlsx.add_row(headers, is_header=True)
    for i, r in enumerate(rows):
        xlsx.add_row([
            str(i + 1), r['productName'], r['leafCat'], r['catPath'],
            str(r['productId']), str(r['productSkcId']), str(r['productSkuId']), r['extCode'],
            r['color'], r['size'], str(r['stock']), r['priceYuan'],
            r['shipMode'], r['status'], r['volume'], r['weight'],
        ])
    xlsx.save(filepath)
    return len(rows)


# ============================================================
# Part 7: Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='Temu Agent Seller 商品列表导出工具 v2')
    parser.add_argument('--output', '-o', default='temu_goods.xlsx', help='Excel输出路径 (默认: temu_goods.xlsx)')
    parser.add_argument('--cdp-port', type=int, default=9222, help='CDP端口 (默认: 9222)')
    parser.add_argument('--cdp-host', default='127.0.0.1', help='CDP主机 (默认: 127.0.0.1)')
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════╗")
    print("║   Temu Agent Seller 商品列表导出工具 v2          ║")
    print("╚══════════════════════════════════════════════════╝\n")

    cdp = CDPClient(args.cdp_host, args.cdp_port)
    try:
        print(f"[1/6] 连接 Chrome CDP (port {args.cdp_port})...")
        cdp.connect()
        print("  ✓ 已连接\n")

        print("[2/6] 确认商品列表页面...")
        ensure_on_goods_page(cdp)
        print()

        print("[3/6] 切换到 \"全部\" 标签...")
        ensure_all_tab(cdp)
        print()

        print("[4/6] 设置每页显示 500 条...")
        ensure_pagesize_500(cdp)
        print()

        print("[5/6] 从 React 数据源提取商品数据...")
        rows = extract_goods(cdp)
        print(f"  ✓ 提取完成: {len(rows)} 条 SKU 记录\n")

        print("[6/6] 输出结果...\n")
        print_goods_table(rows)

        xlsx_path = os.path.abspath(args.output)
        count = export_to_xlsx(rows, xlsx_path)
        print(f"\n✅ Excel 已导出: {xlsx_path} ({count} 行数据)")
        print(f"\n导出时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    finally:
        cdp.close()

if __name__ == "__main__":
    main()
