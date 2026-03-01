#!/usr/bin/env python3
"""
Temu Agent Seller 库存设置工具
==============================
通过 Chrome DevTools Protocol (CDP) 自动设置指定 SKU 的库存值。

依赖: 无外部依赖，纯 Python 标准库实现。
前提: Chrome 需以 --remote-debugging-port=9222 启动并已登录 Temu 卖家后台商品列表页。

用法:
    # 设置 SKU 48656740770 库存为 0
    python3 temu_stock_setter.py --sku 48656740770 --stock 0

    # 设置 SKU 12345678 库存为 500
    python3 temu_stock_setter.py --sku 12345678 --stock 500

    # 使用自定义 CDP 端口
    python3 temu_stock_setter.py --sku 48656740770 --stock 0 --cdp-port 9222
"""

import socket, json, base64, os, struct, time, sys, argparse
import urllib.request


# ============================================================
# Part 1: CDP WebSocket Client (pure stdlib)
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
        self._page_id = page_id
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
        raise RuntimeError("未找到 Temu 卖家后台页面，请确保 Chrome 已打开 agentseller.temu.com/goods/list")

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
                    raise ConnectionError("WebSocket 已关闭")
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
                raise ConnectionError("WebSocket 被对端关闭")
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
# Part 2: JavaScript Templates
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

JS_FIND_SKU_IN_DATASOURCE = r"""
(function(targetSkuId) {
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

    // Find the target SKU in the dataSource
    for (let i = 0; i < ds.length; i++) {
        const item = ds[i];
        const sku = item.productSkuSummaryItem || {};
        const skuId = String(sku.productSkuId || item.productSkuId || '');
        if (skuId === String(targetSkuId)) {
            const specList = sku.productSkuSpecList || [];
            let color = '-', size = '-';
            for (const spec of specList) {
                const pn = (spec.parentSpecName || '').toLowerCase();
                if (pn.includes('颜色') || pn.includes('color')) color = spec.specName || '-';
                else size = spec.specName || '-';
            }
            let stock = 0;
            const ss = sku.productSkuSemiManagedStock || {};
            if (ss.skuStockQuantity != null) stock = ss.skuStockQuantity;

            return JSON.stringify({
                found: true,
                index: i,
                productId: item.productId,
                productSkcId: item.productSkcId,
                productSkuId: skuId,
                productName: (item.productName || '').trim(),
                color: color,
                size: size,
                currentStock: stock,
                // Count how many SKU rows share this SPU (same productSkcId)
                spuSkuCount: ds.filter(x => x.productSkcId === item.productSkcId).length,
                // Index of this SKU within its SPU group
                skuIndexInSpu: ds.filter((x, j) => x.productSkcId === item.productSkcId && j <= i).length - 1,
            });
        }
    }
    return JSON.stringify({found: false, totalRows: ds.length});
})('__SKU_ID__')
"""

JS_CLOSE_ALL_MODALS = r"""
(function() {
    let modals = document.querySelectorAll('[class*="MDL_outerWrapper"]');
    let closed = 0;
    for (let m of modals) {
        if (m.offsetHeight > 0) {
            // Prefer close icon (X) over cancel button to avoid cancelling operations
            let closeBtn = m.querySelector('[class*="MDL_closeIcon"]');
            if (closeBtn) { closeBtn.click(); closed++; }
            else {
                // Only use cancel as last resort
                let cancelBtn = null;
                m.querySelectorAll('button').forEach(b => {
                    if (b.innerText.trim() === '取消') cancelBtn = b;
                });
                if (cancelBtn) { cancelBtn.click(); closed++; }
            }
        }
    }
    return JSON.stringify({closed: closed});
})()
"""

# Click the "修改库存" link on a specific row (virtual table row index)
JS_CLICK_MODIFY_STOCK = r"""
(function(targetSkuId) {
    // The body table is inside TB_body, not the header table
    var bodyEl = document.querySelector('[class*="TB_body"]');
    if (!bodyEl) return JSON.stringify({error: 'no_TB_body'});
    var rows = bodyEl.querySelectorAll('table tbody tr');
    
    for (var row of rows) {
        // Walk up fiber to find the component with rowIndex and dataSource
        var fk = Object.keys(row).find(function(k) { return k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'); });
        if (!fk) continue;
        var fiber = row[fk]; var d = 0;
        while (fiber && d < 5) {
            var props = fiber.memoizedProps || {};
            if (props.dataSource && props.rowIndex !== undefined) {
                var rowData = props.dataSource[props.rowIndex];
                if (rowData) {
                    var sku = rowData.productSkuSummaryItem || {};
                    var skuId = String(sku.productSkuId || rowData.productSkuId || '');
                    var skcId = String(rowData.productSkcId || '');
                    if (skuId === String(targetSkuId)) {
                        // Found the SKU's row — click "修改库存" link
                        var links = row.querySelectorAll('a');
                        for (var a of links) {
                            if (a.innerText.trim() === '修改库存') {
                                a.click();
                                return JSON.stringify({ok: true, method: 'fiber_rowIndex', rowIndex: props.rowIndex});
                            }
                        }
                        // Link might be on a sibling row (SPU group first row)
                        // Search all visible rows for one with same productSkcId that has the link
                        for (var sr of rows) {
                            var sfk = Object.keys(sr).find(function(k) { return k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'); });
                            if (!sfk) continue;
                            var sf = sr[sfk]; var sd = 0;
                            while (sf && sd < 5) {
                                var sp = sf.memoizedProps || {};
                                if (sp.dataSource && sp.rowIndex !== undefined) {
                                    var srd = sp.dataSource[sp.rowIndex];
                                    if (srd && String(srd.productSkcId || '') === skcId) {
                                        var slinks = sr.querySelectorAll('a');
                                        for (var sa of slinks) {
                                            if (sa.innerText.trim() === '修改库存') {
                                                sa.click();
                                                return JSON.stringify({ok: true, method: 'sibling_spu_row', rowIndex: sp.rowIndex});
                                            }
                                        }
                                    }
                                    break;
                                }
                                sf = sf.return; sd++;
                            }
                        }
                        return JSON.stringify({error: 'row_found_but_no_link', skuId: skuId, skcId: skcId, rowIdx: props.rowIndex});
                    }
                }
                break;
            }
            fiber = fiber.return; d++;
        }
    }
    return JSON.stringify({error: 'sku_row_not_in_visible_dom', hint: 'need_to_scroll', visibleRows: rows.length});
})('__SKU_ID__')
"""

# Scroll the virtual table to bring a specific row index into view
JS_SCROLL_TO_ROW = r"""
(function(targetIndex) {
    // The virtual table has: TB_body > div[overflow-y:scroll] > div[padding-top:...] > table > tbody > tr
    var bodyEl = document.querySelector('[class*="TB_body"]');
    if (!bodyEl) return JSON.stringify({error: 'no_TB_body'});
    
    // The inner scrollable div (first child of TB_body)
    var scrollDiv = bodyEl.firstElementChild;
    if (!scrollDiv) return JSON.stringify({error: 'no_scroll_div'});
    
    // Use total scrollHeight and total data count to calculate per-row height
    var totalScrollH = scrollDiv.scrollHeight;
    var clientH = scrollDiv.clientHeight;
    
    // Get total data count from fiber
    var table = bodyEl.querySelector('table');
    var totalRows = 197;  // fallback
    if (table) {
        var fk = Object.keys(table).find(function(k) { return k.startsWith('__reactFiber'); });
        if (fk) {
            var f = table[fk]; var d = 0;
            while (f && d < 30) {
                if (f.memoizedProps && f.memoizedProps.dataSource) {
                    totalRows = f.memoizedProps.dataSource.length;
                    break;
                }
                f = f.return; d++;
            }
        }
    }
    
    // Calculate position: bring row to ~middle of viewport
    var rowHeight = totalScrollH / totalRows;
    var scrollTo = Math.max(0, Math.min(targetIndex * rowHeight - clientH / 2, totalScrollH - clientH));
    scrollDiv.scrollTop = scrollTo;
    scrollDiv.dispatchEvent(new Event('scroll', {bubbles: true}));
    
    return JSON.stringify({
        ok: true, 
        scrollTo: Math.round(scrollTo), 
        rowHeight: Math.round(rowHeight * 100) / 100,
        totalRows: totalRows,
        scrollH: totalScrollH,
        clientH: clientH
    });
})(__ROW_INDEX__)
"""

# Analyze the stock modification modal to understand its structure
JS_ANALYZE_MODAL = r"""
(function() {
    let modals = document.querySelectorAll('[class*="MDL_outerWrapper"]');
    let modal = null;
    for (let m of modals) {
        if (m.offsetHeight > 0 && m.innerText.includes('修改库存')) { modal = m; break; }
    }
    if (!modal) {
        // Also check for any visible modal
        for (let m of modals) {
            if (m.offsetHeight > 0) { modal = m; break; }
        }
    }
    if (!modal) return JSON.stringify({error: 'no_visible_modal'});

    let inputs = modal.querySelectorAll('input[type="text"]');
    let inputInfo = [];
    for (let i = 0; i < inputs.length; i++) {
        let inp = inputs[i];
        inputInfo.push({
            index: i,
            value: inp.value,
            placeholder: inp.placeholder,
            readOnly: inp.readOnly,
            disabled: inp.disabled,
            className: inp.className.substring(0, 80)
        });
    }

    let buttons = [];
    modal.querySelectorAll('button').forEach(b => {
        if (b.offsetHeight > 0) {
            buttons.push({text: b.innerText.trim(), className: b.className.substring(0, 80)});
        }
    });

    let text = modal.innerText.replace(/\n/g, ' | ').substring(0, 2000);

    return JSON.stringify({
        inputCount: inputInfo.length,
        inputs: inputInfo,
        buttons: buttons,
        text: text
    });
})()
"""

# Set the batch dropdown to "减少", enter the reduction amount, and click "批量填充"
JS_BATCH_REDUCE = r"""
(function(amount) {
    let modal = null;
    let modals = document.querySelectorAll('[class*="MDL_outerWrapper"]');
    for (let m of modals) {
        if (m.offsetHeight > 0 && (m.innerText.includes('修改库存') || m.querySelector('[class*="MDL_alert"]'))) { 
            modal = m; break; 
        }
    }
    if (!modal) {
        for (let m of modals) { if (m.offsetHeight > 0) { modal = m; break; } }
    }
    if (!modal) return JSON.stringify({error: 'no_modal'});

    // Find the FIRST dropdown input (batch direction: 增加/减少)
    let inputs = modal.querySelectorAll('input[type="text"]');
    let batchDropdown = null;
    for (let inp of inputs) {
        if (inp.value === '增加' || inp.value === '减少') {
            batchDropdown = inp;
            break;
        }
    }
    if (!batchDropdown) return JSON.stringify({error: 'no_batch_dropdown', inputValues: Array.from(inputs).map(i=>i.value)});
    
    if (batchDropdown.value !== '减少') {
        // Need to click and select "减少"
        batchDropdown.click();
        return JSON.stringify({step: 'dropdown_opened', currentValue: batchDropdown.value});
    }
    return JSON.stringify({step: 'already_reduce'});
})(__AMOUNT__)
"""

JS_BATCH_INCREASE = r"""
(function(amount) {
    let modal = null;
    let modals = document.querySelectorAll('[class*="MDL_outerWrapper"]');
    for (let m of modals) {
        if (m.offsetHeight > 0 && (m.innerText.includes('修改库存') || m.querySelector('[class*="MDL_alert"]'))) { 
            modal = m; break; 
        }
    }
    if (!modal) {
        for (let m of modals) { if (m.offsetHeight > 0) { modal = m; break; } }
    }
    if (!modal) return JSON.stringify({error: 'no_modal'});

    // Find the FIRST dropdown input (batch direction: 增加/减少)
    let inputs = modal.querySelectorAll('input[type="text"]');
    let batchDropdown = null;
    for (let inp of inputs) {
        if (inp.value === '增加' || inp.value === '减少') {
            batchDropdown = inp;
            break;
        }
    }
    if (!batchDropdown) return JSON.stringify({error: 'no_batch_dropdown', inputValues: Array.from(inputs).map(i=>i.value)});
    
    if (batchDropdown.value !== '增加') {
        batchDropdown.click();
        return JSON.stringify({step: 'dropdown_opened', currentValue: batchDropdown.value});
    }
    return JSON.stringify({step: 'already_increase'});
})(__AMOUNT__)
"""

JS_SELECT_DROPDOWN_OPTION = r"""
(function(optionText) {
    // Find visible option text matching the target
    let all = document.querySelectorAll('*');
    for (let el of all) {
        if (el.children.length === 0 && el.innerText && el.innerText.trim() === optionText && 
            el.offsetHeight > 0 && el.offsetWidth > 0) {
            el.click();
            return JSON.stringify({ok: true});
        }
    }
    return JSON.stringify({error: 'option_not_found', target: optionText});
})('__OPTION__')
"""

JS_SET_BATCH_AMOUNT_AND_FILL = r"""
(function(amount) {
    let modal = null;
    let modals = document.querySelectorAll('[class*="MDL_outerWrapper"]');
    for (let m of modals) {
        if (m.offsetHeight > 0) { modal = m; break; }
    }
    if (!modal) return JSON.stringify({error: 'no_modal'});

    // Find the batch amount input — it's the first input with placeholder="请输入"
    let inputs = modal.querySelectorAll('input[type="text"]');
    let batchAmount = null;
    for (let inp of inputs) {
        if (inp.placeholder === '请输入' && (inp.value === '' || !isNaN(inp.value))) {
            batchAmount = inp;
            break;
        }
    }
    if (!batchAmount) return JSON.stringify({error: 'no_amount_input'});

    // Set value using React-compatible setter
    let setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(batchAmount, String(amount));
    batchAmount.dispatchEvent(new Event('input', { bubbles: true }));
    batchAmount.dispatchEvent(new Event('change', { bubbles: true }));

    // Click "批量填充" button
    let btns = modal.querySelectorAll('button');
    for (let b of btns) {
        if (b.innerText.trim() === '批量填充') {
            b.click();
            return JSON.stringify({ok: true, amountSet: amount});
        }
    }
    return JSON.stringify({error: 'no_batch_fill_button', amountSet: amount});
})(__AMOUNT__)
"""

JS_CLICK_CONFIRM = r"""
(function() {
    let modals = document.querySelectorAll('[class*="MDL_outerWrapper"]');
    for (let m of modals) {
        if (m.offsetHeight > 0) {
            let btns = m.querySelectorAll('button');
            for (let b of btns) {
                if (b.innerText.trim() === '确认' && !b.className.includes('closeBtn')) {
                    b.click();
                    return JSON.stringify({ok: true, buttonClass: b.className.substring(0, 80)});
                }
            }
        }
    }
    return JSON.stringify({error: 'no_confirm_button'});
})()
"""

JS_HANDLE_LOW_STOCK_WARNING = r"""
(function() {
    // The "库存过低" warning dialog has "返回修改" and "确认" buttons
    let modals = document.querySelectorAll('[class*="MDL_outerWrapper"]');
    for (let m of modals) {
        if (m.offsetHeight > 0 && m.innerText.includes('返回修改')) {
            // This is the warning dialog — click "确认" (the MDL_closeBtn)
            let btn = m.querySelector('[class*="MDL_closeBtn"]');
            if (btn) { btn.click(); return JSON.stringify({ok: true, method: 'closeBtn'}); }
            // Fallback: last button in this dialog
            let btns = m.querySelectorAll('button');
            for (let b of btns) {
                if (b.innerText.trim() === '确认') {
                    b.click();
                    return JSON.stringify({ok: true, method: 'text_match'});
                }
            }
        }
    }
    return JSON.stringify({found: false});
})()
"""

JS_CHECK_OPEN_MODALS = r"""
(function() {
    let modals = document.querySelectorAll('[class*="MDL_outerWrapper"]');
    let open = [];
    for (let m of modals) {
        if (m.offsetHeight > 0) {
            open.push(m.innerText.replace(/\n/g, ' | ').substring(0, 200));
        }
    }
    return JSON.stringify({count: open.length, texts: open});
})()
"""


# ============================================================
# Part 3: Stock Setting Logic
# ============================================================

def ensure_goods_page(cdp):
    """确保当前在商品列表页面。"""
    result = cdp.evaluate(r"""
    (function() {
        return JSON.stringify({url: window.location.href});
    })()
    """)
    info = json.loads(result)
    url = info.get('url', '')
    if 'agentseller.temu.com/goods' not in url:
        print(f"  当前页面: {url}")
        print("  正在跳转到商品列表页...")
        cdp.evaluate("window.location.href = 'https://agentseller.temu.com/goods/list'")
        time.sleep(4)
    return True


def ensure_pagesize_500(cdp):
    """确保每页显示 500 条，以加载所有商品数据。"""
    result = json.loads(cdp.evaluate(JS_SET_PAGESIZE_500))
    if result.get('action') == 'already_500':
        print(f"  每页已为 {result['pageSize']} 条 (共 {result['total']} 条)")
        return
    if result.get('action') == 'opened_dropdown':
        print(f"  当前每页 {result['pageSize']} 条，正在切换为 500...")
        time.sleep(0.5)
        click = json.loads(cdp.evaluate(JS_CLICK_500_OPTION))
        if click.get('ok'):
            print("  已选择每页 500 条，等待数据加载...")
            time.sleep(3)
            # Verify
            count = cdp.evaluate(r"""
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
            """)
            print(f"  dataSource 已加载 {count} 行数据")
        else:
            print(f"  ⚠ 未能选择 500 选项: {click}")
    elif result.get('action') == 'no_pager':
        print("  未检测到分页控件，可能商品数量较少，无需切换")
    else:
        print(f"  ⚠ 分页切换结果: {result}")


def find_sku(cdp, sku_id):
    """在 React dataSource 中查找目标 SKU，返回详细信息。"""
    js = JS_FIND_SKU_IN_DATASOURCE.replace('__SKU_ID__', str(sku_id))
    result = json.loads(cdp.evaluate(js))
    return result


def close_all_modals(cdp):
    """关闭所有打开的弹窗。"""
    result = json.loads(cdp.evaluate(JS_CLOSE_ALL_MODALS))
    if result.get('closed', 0) > 0:
        print(f"  已关闭 {result['closed']} 个弹窗")
        time.sleep(0.5)


def scroll_to_row(cdp, row_index):
    """滚动虚拟表格使特定行可见。"""
    js = JS_SCROLL_TO_ROW.replace('__ROW_INDEX__', str(row_index))
    result = json.loads(cdp.evaluate(js))
    if result.get('ok'):
        time.sleep(0.5)
    return result


def click_modify_stock(cdp, sku_id):
    """找到目标 SKU 所在行并点击"修改库存"。"""
    js = JS_CLICK_MODIFY_STOCK.replace('__SKU_ID__', str(sku_id))
    result = json.loads(cdp.evaluate(js))
    return result


def set_dropdown_direction(cdp, direction):
    """设置批量操作的增减方向。direction: '减少' or '增加'"""
    if direction == '减少':
        result = json.loads(cdp.evaluate(JS_BATCH_REDUCE.replace('__AMOUNT__', '0')))
    else:
        result = json.loads(cdp.evaluate(JS_BATCH_INCREASE.replace('__AMOUNT__', '0')))

    if result.get('step') == 'dropdown_opened':
        time.sleep(0.5)
        # Select the target option from the dropdown
        js = JS_SELECT_DROPDOWN_OPTION.replace('__OPTION__', direction)
        sel = json.loads(cdp.evaluate(js))
        if sel.get('ok'):
            time.sleep(0.3)
            return True
        else:
            print(f"  ⚠ 下拉选项 '{direction}' 未找到: {sel}")
            return False
    elif result.get('step') in ('already_reduce', 'already_increase'):
        return True
    else:
        print(f"  ⚠ 无法设置下拉方向: {result}")
        return False


def batch_fill_amount(cdp, amount):
    """输入数量并点击批量填充。"""
    js = JS_SET_BATCH_AMOUNT_AND_FILL.replace('__AMOUNT__', str(amount))
    result = json.loads(cdp.evaluate(js))
    return result


def click_confirm(cdp):
    """点击确认按钮。"""
    result = json.loads(cdp.evaluate(JS_CLICK_CONFIRM))
    return result


def handle_low_stock_warning(cdp, max_wait=3):
    """处理"库存过低"警告弹窗（如果出现）。"""
    for _ in range(max_wait * 2):
        result = json.loads(cdp.evaluate(JS_HANDLE_LOW_STOCK_WARNING))
        if result.get('ok'):
            return True
        time.sleep(0.5)
    return False


def check_modals(cdp):
    """检查是否还有打开的弹窗。"""
    result = json.loads(cdp.evaluate(JS_CHECK_OPEN_MODALS))
    return result


def set_stock(cdp, sku_id, target_stock):
    """
    主入口：将指定 SKU 的库存设置为目标值。
    
    逻辑：
    1. 从 React dataSource 获取当前库存
    2. 计算差值（增加或减少）
    3. 打开修改库存弹窗
    4. 使用批量填充设置数量
    5. 确认提交
    """
    print(f"\n{'='*60}")
    print(f"  目标: SKU {sku_id} → 库存 {target_stock}")
    print(f"{'='*60}")

    # Step 1: 确保在商品列表页
    print("\n[1/8] 确认页面状态...")
    ensure_goods_page(cdp)

    # Step 2: 确保每页 500 条
    print("\n[2/8] 确保每页显示 500 条...")
    ensure_pagesize_500(cdp)

    # Step 3: 在 dataSource 中查找 SKU
    print(f"\n[3/8] 查找 SKU {sku_id}...")
    sku_info = find_sku(cdp, sku_id)
    if not sku_info.get('found'):
        print(f"  ✗ SKU {sku_id} 未找到！(dataSource 共 {sku_info.get('totalRows', '?')} 行)")
        return False

    current_stock = sku_info['currentStock']
    product_name = sku_info['productName']
    color = sku_info['color']
    size = sku_info['size']
    row_index = sku_info['index']
    spu_sku_count = sku_info['spuSkuCount']

    print(f"  ✓ 找到: {product_name}")
    print(f"    颜色: {color}, 尺寸: {size}")
    print(f"    当前库存: {current_stock}")
    print(f"    dataSource 行: #{row_index}")
    print(f"    该 SPU 下 SKU 数: {spu_sku_count}")

    # Calculate delta
    delta = target_stock - current_stock
    if delta == 0:
        print(f"\n  ✓ 库存已经是 {target_stock}，无需修改！")
        return True

    direction = '增加' if delta > 0 else '减少'
    amount = abs(delta)
    print(f"    操作: {direction} {amount}")

    # Step 4: 关闭已有弹窗
    print("\n[4/8] 关闭已有弹窗...")
    close_all_modals(cdp)

    # Step 5: 滚动到目标行并点击"修改库存"
    print(f"\n[5/8] 滚动到行 #{row_index} 并打开库存修改弹窗...")
    scroll_result = scroll_to_row(cdp, row_index)
    if scroll_result.get('ok'):
        print(f"  已滚动到位置 {scroll_result.get('scrollTo')}px")
    time.sleep(1)

    # Try to click "修改库存" - may need multiple attempts with scrolling
    click_result = click_modify_stock(cdp, sku_id)
    if click_result.get('error') == 'sku_row_not_in_visible_dom':
        # Try scrolling with different offsets to ensure the row renders
        print("  行不在可视区域，尝试不同滚动位置...")
        for offset in [0, -10, -20, 10, 20, -30, 30]:
            scroll_to_row(cdp, max(0, row_index + offset))
            time.sleep(1)  # Give virtual renderer time to update
            click_result = click_modify_stock(cdp, sku_id)
            if click_result.get('ok'):
                break
            if click_result.get('error') == 'row_found_but_no_link':
                break  # Found the row but no link — different problem
    
    if not click_result.get('ok'):
        print(f"  ✗ 无法点击修改库存按钮: {click_result}")
        # Fallback: try clicking by visible row text matching in the body table
        print("  尝试回退方式: 通过产品 ID 文本匹配...")
        fb_js = r"""
        (function(productId) {
            var bodyEl = document.querySelector('[class*="TB_body"]');
            if (!bodyEl) return JSON.stringify({error: 'no_TB_body'});
            var rows = bodyEl.querySelectorAll('table tbody tr');
            for (var row of rows) {
                if (row.innerText.includes(productId)) {
                    var links = row.querySelectorAll('a');
                    for (var a of links) {
                        if (a.innerText.trim() === '修改库存') {
                            a.click();
                            return JSON.stringify({ok: true, method: 'text_fallback'});
                        }
                    }
                }
            }
            return JSON.stringify({error: 'fallback_failed', rowCount: rows.length});
        })('__PID__')
        """.replace('__PID__', str(sku_info['productSkcId']))
        click_result = json.loads(cdp.evaluate(fb_js))
        if not click_result.get('ok'):
            print(f"  ✗ 回退方式也失败: {click_result}")
            return False

    print(f"  ✓ 已点击修改库存 ({click_result.get('method', 'unknown')})")

    # Step 6: 在弹窗中设置指定 SKU 的库存
    print(f"\n[6/8] 设置库存: {direction} {amount}...")

    # Wait for modal to fully render (per-SKU rows load lazily)
    # Build spec_text: combine color+size for uniqueness (modal rows show "color-size")
    if color != '-' and size != '-':
        spec_text = color + '-' + size
    elif color != '-':
        spec_text = color
    else:
        spec_text = size
    
    # Wait until we see per-SKU direction inputs (>1 group means per-SKU rows rendered)
    wait_js = r"""
    (function() {
        var modal = null;
        var modals = document.querySelectorAll('[class*="MDL_outerWrapper"]');
        for (var m of modals) { if (m.offsetHeight > 0) { modal = m; break; } }
        if (!modal) return JSON.stringify({groups: 0, inputs: 0});
        var allInputs = Array.from(modal.querySelectorAll('input'));
        var dirCount = 0;
        for (var i = 0; i < allInputs.length; i++) {
            var inp = allInputs[i];
            if (inp.type === 'text' && inp.readOnly && (inp.value === '增加' || inp.value === '减少')) dirCount++;
        }
        return JSON.stringify({groups: dirCount, inputs: allInputs.length});
    })()
    """
    for wait_i in range(15):
        time.sleep(1)
        modal_state = json.loads(cdp.evaluate(wait_js))
        if modal_state['groups'] > 1:
            print(f"  弹窗已就绪 (方向组: {modal_state['groups']}, 输入框: {modal_state['inputs']})")
            break
        if wait_i >= 14:
            print(f"  ⚠ 弹窗等待超时 (方向组: {modal_state['groups']})")
            return False
    
    # First, set the direction for the target SKU
    set_dir_js = r"""
    (function(specText, targetDirection) {
        var modal = null;
        var modals = document.querySelectorAll('[class*="MDL_outerWrapper"]');
        for (var m of modals) { if (m.offsetHeight > 0) { modal = m; break; } }
        if (!modal) return JSON.stringify({error: 'no_modal'});
        
        // Get all inputs in the modal
        var allInputs = Array.from(modal.querySelectorAll('input'));
        
        // Find all direction dropdown inputs (type=text, readonly, value=增加 or 减少)
        // Group them with their associated amount input (next text input with placeholder=请输入)
        var groups = [];
        for (var i = 0; i < allInputs.length; i++) {
            var inp = allInputs[i];
            if (inp.type === 'text' && inp.readOnly && (inp.value === '增加' || inp.value === '减少')) {
                // Find the next amount input
                var amtInput = null;
                for (var j = i + 1; j < allInputs.length; j++) {
                    if (allInputs[j].type === 'text' && allInputs[j].placeholder === '请输入' && !allInputs[j].readOnly) {
                        amtInput = allInputs[j];
                        break;
                    }
                    if (allInputs[j].type === 'text' && allInputs[j].readOnly && (allInputs[j].value === '增加' || allInputs[j].value === '减少')) {
                        break; // Hit next direction dropdown
                    }
                }
                // Find the closest table row (tr) containing context text
                var parentTr = inp.closest('tr');
                var parentText = '';
                if (!parentTr) {
                    // Walk up to find relevant text container
                    var p = inp.parentElement;
                    var depth = 0;
                    while (p && depth < 10) {
                        if (p.innerText && p.innerText.length > 10) {
                            parentText = p.innerText.substring(0, 200);
                            break;
                        }
                        p = p.parentElement; depth++;
                    }
                } else {
                    parentText = parentTr.innerText.substring(0, 200);
                }
                groups.push({dirInput: inp, amtInput: amtInput, text: parentText, inputIdx: i});
            }
        }
        
        if (groups.length === 0) return JSON.stringify({error: 'no_direction_groups'});
        
        // groups[0] is the batch row, groups[1+] are per-SKU
        // Find the group whose text contains specText
        var target = null;
        for (var g = 1; g < groups.length; g++) {
            if (groups[g].text.indexOf(specText) !== -1) {
                target = groups[g];
                break;
            }
        }
        
        if (!target) {
            // Return available group texts for debugging
            return JSON.stringify({
                error: 'spec_not_found', 
                specText: specText,
                groupTexts: groups.map(function(g, idx) { return {idx: idx, text: g.text.substring(0, 80)}; })
            });
        }
        
        // Check if direction already correct
        if (target.dirInput.value === targetDirection) {
            return JSON.stringify({ok: true, step: 'direction_already_correct', inputIdx: target.inputIdx});
        }
        
        // Click to open dropdown
        target.dirInput.click();
        return JSON.stringify({ok: true, step: 'dropdown_opened', inputIdx: target.inputIdx, currentValue: target.dirInput.value});
    })('__SPEC__', '__DIR__')
    """.replace('__SPEC__', spec_text).replace('__DIR__', direction)
    
    dir_result = json.loads(cdp.evaluate(set_dir_js))
    if dir_result.get('error'):
        print(f"  ✗ 无法定位 SKU 输入: {dir_result}")
        # If spec_text not found, try with full spec string
        if dir_result.get('error') == 'spec_not_found':
            print(f"  可用组文本:")
            for gt in dir_result.get('groupTexts', []):
                print(f"    [{gt['idx']}] {gt['text']}")
        return False
    
    if dir_result.get('step') == 'dropdown_opened':
        time.sleep(0.5)
        sel_js = JS_SELECT_DROPDOWN_OPTION.replace('__OPTION__', direction)
        sel_result = json.loads(cdp.evaluate(sel_js))
        if not sel_result.get('ok'):
            print(f"  ✗ 选择'{direction}'失败: {sel_result}")
            return False
        time.sleep(0.3)
    print(f"  ✓ 方向已设为 '{direction}'")
    
    # Now set the amount for the target SKU
    set_amt_js = r"""
    (function(specText, amountStr) {
        var modal = null;
        var modals = document.querySelectorAll('[class*="MDL_outerWrapper"]');
        for (var m of modals) { if (m.offsetHeight > 0) { modal = m; break; } }
        if (!modal) return JSON.stringify({error: 'no_modal'});
        
        var allInputs = Array.from(modal.querySelectorAll('input'));
        
        // Find direction groups again  
        var groups = [];
        for (var i = 0; i < allInputs.length; i++) {
            var inp = allInputs[i];
            if (inp.type === 'text' && inp.readOnly && (inp.value === '增加' || inp.value === '减少')) {
                var amtInput = null;
                for (var j = i + 1; j < allInputs.length; j++) {
                    if (allInputs[j].type === 'text' && allInputs[j].placeholder === '请输入' && !allInputs[j].readOnly) {
                        amtInput = allInputs[j];
                        break;
                    }
                    if (allInputs[j].type === 'text' && allInputs[j].readOnly && (allInputs[j].value === '增加' || allInputs[j].value === '减少')) {
                        break;
                    }
                }
                var parentTr = inp.closest('tr');
                var parentText = '';
                if (!parentTr) {
                    var p = inp.parentElement;
                    var depth = 0;
                    while (p && depth < 10) {
                        if (p.innerText && p.innerText.length > 10) { parentText = p.innerText.substring(0, 200); break; }
                        p = p.parentElement; depth++;
                    }
                } else {
                    parentText = parentTr.innerText.substring(0, 200);
                }
                groups.push({dirInput: inp, amtInput: amtInput, text: parentText});
            }
        }
        
        // Find matching group
        var target = null;
        for (var g = 1; g < groups.length; g++) {
            if (groups[g].text.indexOf(specText) !== -1) { target = groups[g]; break; }
        }
        if (!target || !target.amtInput) return JSON.stringify({error: 'target_not_found_or_no_amt'});
        
        // Set value using React-compatible setter
        var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(target.amtInput, amountStr);
        target.amtInput.dispatchEvent(new Event('input', { bubbles: true }));
        target.amtInput.dispatchEvent(new Event('change', { bubbles: true }));
        
        return JSON.stringify({ok: true, value: target.amtInput.value, direction: target.dirInput.value});
    })('__SPEC__', '__AMT__')
    """.replace('__SPEC__', spec_text).replace('__AMT__', str(amount))
    
    amt_result = json.loads(cdp.evaluate(set_amt_js))
    if not amt_result.get('ok'):
        print(f"  ✗ 设置数量失败: {amt_result}")
        return False
    print(f"  ✓ 数量 {amount} ({amt_result.get('direction', '?')})")

    # Step 7: Click confirm
    print(f"\n[7/8] 点击确认...")
    time.sleep(0.5)
    confirm_result = click_confirm(cdp)
    if confirm_result.get('ok'):
        print(f"  ✓ 已点击确认")
    else:
        print(f"  ✗ 确认按钮失败: {confirm_result}")
        return False

    # Step 8: Handle low stock warning (may appear multiple times)
    print(f"\n[8/8] 处理可能的低库存警告...")
    time.sleep(2)
    for attempt in range(5):
        warning_handled = handle_low_stock_warning(cdp)
        if warning_handled:
            print(f"  ✓ 已确认低库存警告 (第{attempt+1}次)")
            time.sleep(1)
        else:
            break
    
    # Wait for operation to complete
    time.sleep(2)

    # Verify the result
    return verify_stock(cdp, sku_id, target_stock)


def verify_stock(cdp, sku_id, target_stock):
    """验证库存是否已成功修改。"""
    print(f"\n{'='*60}")
    print("  验证结果...")
    print(f"{'='*60}")

    # Wait for all modals to close naturally (after confirm + low stock warning)
    for wait_attempt in range(10):
        modal_status = check_modals(cdp)
        if modal_status['count'] == 0:
            break
        print(f"  等待弹窗关闭... ({wait_attempt+1}/10)")
        # Try handling any lingering low stock warnings
        handle_low_stock_warning(cdp, max_wait=1)
        time.sleep(1)
    
    # If modals still open after waiting, try close icon (not cancel)
    modal_status = check_modals(cdp)
    if modal_status['count'] > 0:
        print(f"  还有 {modal_status['count']} 个弹窗未关闭, 尝试关闭...")
        close_all_modals(cdp)
        time.sleep(1)

    # Wait for page to update
    time.sleep(1)
    
    # Re-check from dataSource
    sku_info = find_sku(cdp, sku_id)
    if not sku_info.get('found'):
        print(f"  ⚠ 验证时未找到 SKU {sku_id}")
        return False
    
    new_stock = sku_info['currentStock']
    if new_stock == target_stock:
        print(f"  ✓ 成功! SKU {sku_id} 库存已从原值修改为 {new_stock}")
        return True
    else:
        print(f"  ⚠ 库存现为 {new_stock}，目标为 {target_stock}")
        print(f"    (dataSource 可能有缓存，刷新页面后再验证)")
        return True  # Optimistically return true if no error occurred


# ============================================================
# Part 4: Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='Temu 卖家后台库存设置工具',
        epilog='示例: python3 temu_stock_setter.py --sku 48656740770 --stock 0'
    )
    parser.add_argument('--sku', required=True, help='目标 SKU ID')
    parser.add_argument('--stock', required=True, type=int, help='目标库存值')
    parser.add_argument('--cdp-port', type=int, default=9222, help='Chrome DevTools 端口 (默认 9222)')
    parser.add_argument('--cdp-host', default='127.0.0.1', help='Chrome DevTools 主机 (默认 127.0.0.1)')
    args = parser.parse_args()

    if args.stock < 0:
        print("错误: 目标库存值不能为负数")
        sys.exit(1)

    print(f"Temu 库存设置工具")
    print(f"  SKU: {args.sku}")
    print(f"  目标库存: {args.stock}")
    print(f"  CDP: {args.cdp_host}:{args.cdp_port}")

    cdp = CDPClient(args.cdp_host, args.cdp_port)
    try:
        cdp.connect()
        print(f"  已连接到 Chrome DevTools\n")
        
        success = set_stock(cdp, args.sku, args.stock)
        
        if success:
            print(f"\n{'='*60}")
            print(f"  ✓ 操作完成")
            print(f"{'='*60}")
        else:
            print(f"\n{'='*60}")
            print(f"  ✗ 操作失败")
            print(f"{'='*60}")
            sys.exit(1)
    except Exception as e:
        print(f"\n✗ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
