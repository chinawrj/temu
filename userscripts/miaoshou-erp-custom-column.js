// ==UserScript==
// @name         Miaoshou ERP - Custom Column (clickable toast final)
// @namespace    https://erp.91miaoshou.com/
// @version      15.0
// @description  Add custom column next to "申报价格"; click to fetch USD price from local server and show toast
// @match        https://erp.91miaoshou.com/pddkj_choice/item/item*
// @run-at       document-idle
// @grant        unsafeWindow
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==

(function () {
  'use strict';

  const DEBUG = false;
  const LOG_PREFIX = '[TM-VT-CLICK]';

  const ANCHOR_TEXT = '申报价格';
  const COL_TITLE = '周董AI助手';

  const PRICE_SERVER_URL = 'http://127.0.0.1:18234/price';

  // Cache fetched prices: sku_id → '$xx.xx'
  const priceCache = new Map();

  const HDR_MARK = 'data-tm-vt-custom-hdr';
  const CELL_MARK = 'data-tm-vt-custom-cell';

  const colIndexMap = {
    skc: -1,
    sku: -1,
    platform: -1,
    price: -1,
  };

  const log = (...args) => DEBUG && console.log(LOG_PREFIX, ...args);

  function insertAfter(target, node) {
    const p = target.parentNode;
    if (!p) return;
    if (target.nextSibling) p.insertBefore(node, target.nextSibling);
    else p.appendChild(node);
  }

  function badgeSet(text) {
    let b = document.getElementById('tm-ext-running-badge');
    if (!b) {
      b = document.createElement('div');
      b.id = 'tm-ext-running-badge';
      b.style.cssText =
        'position:fixed;right:12px;bottom:12px;z-index:999999;' +
        'padding:6px 10px;border-radius:10px;background:#000;color:#fff;' +
        'font-size:12px;opacity:.85;user-select:none';
      document.body.appendChild(b);
    }
    b.textContent = text;
  }

  function toastSet(text) {
    let t = document.getElementById('tm-vt-toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'tm-vt-toast';
      t.style.cssText =
        'position:fixed;right:12px;bottom:42px;z-index:999999;' +
        'padding:8px 10px;border-radius:10px;background:#000;color:#fff;' +
        'font-size:12px;opacity:.9;user-select:none;max-width:360px;line-height:1.4;';
      document.body.appendChild(t);
    }
    t.textContent = text;

    clearTimeout(t._timer);
    t._timer = setTimeout(() => {
      if (t && t.parentNode) t.parentNode.removeChild(t);
    }, 3000);
  }

  function stableRandomFromString(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return Math.round((((h >>> 0) % 10000) / 10000) * 100);
  }

  function getAllRoots() {
    const roots = [document];
    for (const el of document.querySelectorAll('*')) {
      if (el.shadowRoot) roots.push(el.shadowRoot);
    }
    return roots;
  }

  function findHeaderAnchors() {
    const candidates = [];

    candidates.push(
      ...Array.from(document.querySelectorAll('.jx-pro-virtual-table__header-cell')).filter(
        el => (el.innerText || '').trim() === ANCHOR_TEXT,
      ),
    );

    candidates.push(
      ...Array.from(document.querySelectorAll('div')).filter(
        el => (el.innerText || '').trim() === ANCHOR_TEXT,
      ),
    );

    const roots = new Set();
    for (const el of document.querySelectorAll('*')) {
      if (el.shadowRoot) roots.add(el.shadowRoot);
    }
    for (const root of roots) {
      candidates.push(
        ...Array.from(root.querySelectorAll('.jx-pro-virtual-table__header-cell')).filter(
          el => (el.textContent || '').trim() === ANCHOR_TEXT,
        ),
      );
      candidates.push(
        ...Array.from(root.querySelectorAll('div')).filter(
          el => (el.textContent || '').trim() === ANCHOR_TEXT,
        ),
      );
    }

    const bestByRoot = new Map();
    for (const anchor of candidates) {
      if (!anchor) continue;
      const root = anchor.getRootNode && anchor.getRootNode();
      if (!root) continue;

      const key = root;
      const hasHeaderClass =
        anchor.classList && anchor.classList.contains('jx-pro-virtual-table__header-cell');

      if (!bestByRoot.has(key)) {
        bestByRoot.set(key, { root, anchor, score: hasHeaderClass ? 2 : 1 });
        continue;
      }
      const existing = bestByRoot.get(key);
      const existingHeaderClass =
        existing.anchor.classList &&
        existing.anchor.classList.contains('jx-pro-virtual-table__header-cell');
      if (!existingHeaderClass && hasHeaderClass) {
        bestByRoot.set(key, { root, anchor, score: 2 });
      }
    }
    return Array.from(bestByRoot.values());
  }

  function computeColIndexMap(anchor) {
    const row = anchor.parentElement;
    if (!row || !row.children) return;

    const cells = Array.from(row.children).map(c => (c.innerText || '').trim());

    colIndexMap.price = cells.findIndex(t => t === '申报价格' || t === ANCHOR_TEXT);
    colIndexMap.skc = cells.findIndex(t => t === 'SKC ID');
    colIndexMap.sku = cells.findIndex(t => t === 'SKU ID');
    colIndexMap.platform = cells.findIndex(t => t === '平台SKU');

    log('colIndexMap', colIndexMap, 'header cells=', cells);
  }

  function ensureHeaderInserted(anchorPairs) {
    let inserted = 0;
    for (const { anchor } of anchorPairs) {
      const row = anchor.parentElement;
      if (!row) continue;

      computeColIndexMap(anchor);
      if (row.querySelector(`[${HDR_MARK}]`)) continue;

      const newHeader = anchor.cloneNode(true);
      newHeader.innerText = COL_TITLE;
      newHeader.style.color = '#e53935';
      newHeader.style.fontWeight = 'bold';
      newHeader.setAttribute(HDR_MARK, '1');
      insertAfter(anchor, newHeader);
      inserted++;
    }
    return inserted;
  }

  function nthValueFromRowCell(rowCell, n) {
    if (!rowCell) return '';
    const inners = rowCell.querySelectorAll('.sku-list__item-inner');
    if (inners && inners.length) {
      const el = inners[n];
      if (el) return (el.innerText || '').trim();
      const fallback = inners[Math.min(n, inners.length - 1)];
      if (fallback) return (fallback.innerText || '').trim();
    }
    return (rowCell.innerText || '').trim();
  }

  function ensureBodyInserted() {
    let replacedOrInserted = 0;
    const roots = getAllRoots();

    for (const root of roots) {
      const lists = Array.from(root.querySelectorAll('.sku-list')).filter(
        list => list.textContent && /CNY\s*\d/.test(list.textContent),
      );

      for (const list of lists) {
        const priceRowCell =
          list.closest('.jx-pro-virtual-table__row-cell') || list.parentElement;
        if (!priceRowCell) continue;

        const row = priceRowCell.closest('.jx-pro-virtual-table__row') || priceRowCell.parentElement;
        if (!row) continue;

        const customCell = row.querySelector(`[${CELL_MARK}]`);

        const newCell = priceRowCell.cloneNode(true);
        newCell.setAttribute(CELL_MARK, '1');

        const inners = Array.from(newCell.querySelectorAll('.sku-list__item-inner'));
        // Read SKU IDs from the row to restore cached prices
        const skuCell = row.children[colIndexMap.sku];
        const skuInners = skuCell ? Array.from(skuCell.querySelectorAll('.sku-list__item-inner')) : [];
        const needsFetch = [];
        inners.forEach((inner, i) => {
          const skuId = skuInners[i] ? (skuInners[i].innerText || '').trim() : '';
          if (skuId && priceCache.has(skuId)) {
            inner.innerText = priceCache.get(skuId);
            inner.style.color = '#1565c0';
            inner.style.fontWeight = 'bold';
          } else {
            inner.innerText = '—';
            inner.style.color = '';
            inner.style.fontWeight = '';
            if (skuId) needsFetch.push(i);
          }
          inner.style.cursor = 'pointer';
        });

        if (customCell) {
          row.replaceChild(newCell, customCell);
        } else {
          insertAfter(priceRowCell, newCell);
        }
        replacedOrInserted++;

        // Auto-fetch prices for inners without cached values
        if (needsFetch.length > 0) {
          const rowCells = row.children || [];
          const skcCell = rowCells[colIndexMap.skc];
          const platformCell = rowCells[colIndexMap.platform];
          const skcInners = skcCell ? Array.from(skcCell.querySelectorAll('.sku-list__item-inner')) : [];
          const platformInners = platformCell ? Array.from(platformCell.querySelectorAll('.sku-list__item-inner')) : [];

          let productId = '';
          const infoCell = rowCells[1];
          if (infoCell) {
            const spans = infoCell.querySelectorAll('.product-goodInfo-spacing');
            for (const sp of spans) {
              const txt = (sp.textContent || '').trim();
              if (txt.startsWith('产品ID')) {
                productId = txt.replace(/^产品ID[：:]\s*/, '');
                break;
              }
            }
          }

          needsFetch.forEach(function(idx) {
            const skuId = skuInners[idx] ? (skuInners[idx].innerText || '').trim() : '';
            const skcId = skcInners[idx] ? (skcInners[idx].innerText || '').trim() : '';
            const platformSku = platformInners[idx] ? (platformInners[idx].innerText || '').trim() : '';
            if (!skuId) return;
            // Avoid duplicate in-flight requests
            if (priceCache.has('_pending_' + skuId)) return;
            priceCache.set('_pending_' + skuId, true);

            fetchPrice(productId, skcId, skuId, platformSku, function(result) {
              priceCache.delete('_pending_' + skuId);
              if (result && !result.error) {
                var priceText = '$' + result.usd_price;
                priceCache.set(skuId, priceText);
              }
            });
          });
        }
      }
    }
    return replacedOrInserted;
  }

  // ── Fetch USD price from local server ──
  function fetchPrice(productId, skc, sku, platformSku, callback) {
    const payload = JSON.stringify({
      product_id: productId,
      skc_id: skc,
      sku_id: sku,
      platform_sku: platformSku,
    });
    try {
      GM_xmlhttpRequest({
        method: 'POST',
        url: PRICE_SERVER_URL,
        headers: { 'Content-Type': 'application/json' },
        data: payload,
        timeout: 5000,
        onload: function(resp) {
          try {
            callback(JSON.parse(resp.responseText));
          } catch(e) {
            callback({ error: 'JSON parse error' });
          }
        },
        onerror: function() { callback({ error: '服务器连接失败' }); },
        ontimeout: function() { callback({ error: '请求超时' }); },
      });
    } catch(e) {
      // Fallback: plain fetch (may be blocked by CORS in some contexts)
      log('GM_xmlhttpRequest unavailable, using fetch', e);
      fetch(PRICE_SERVER_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
      })
        .then(r => r.json())
        .then(data => callback(data))
        .catch(() => callback({ error: '服务器连接失败' }));
    }
  }

  // ── Event delegation via window (survives DOM recycling & micro-frontend isolation) ──
  function handleCustomCellClick(e) {
    const target = e.target;
    if (!target || !target.closest) return;

    const customCell = target.closest('[' + CELL_MARK + ']');
    if (!customCell) return;

    // Proxy expand/collapse button clicks to the original price cell
    const expandBtn = target.closest('.sku-list__multiple, [class*="sku-list__expand"], [class*="sku-list__collapse"]');
    if (expandBtn && customCell.contains(expandBtn)) {
      const row = customCell.closest('.jx-pro-virtual-table__row');
      if (row) {
        const priceCell = row.children[colIndexMap.price];
        const origBtn = priceCell && priceCell.querySelector('.sku-list__multiple, [class*="sku-list__expand"], [class*="sku-list__collapse"]');
        if (origBtn) {
          origBtn.click();
          return;
        }
      }
    }

    // Resolve the inner element: target might be .sku-list__item-inner or its parent .sku-list__item
    let inner = target.closest('.sku-list__item-inner');
    if (!inner) {
      // Click landed on .sku-list__item or .sku-list — find the child inner
      inner = target.querySelector && target.querySelector('.sku-list__item-inner');
    }
    if (!inner) return;

    e.stopPropagation();
    e.preventDefault();

    const row = customCell.closest('.jx-pro-virtual-table__row') || customCell.parentElement;
    if (!row) return;

    // Find which SKU index this inner corresponds to
    const allInners = Array.from(customCell.querySelectorAll('.sku-list__item-inner'));
    const idx = Math.max(0, allInners.indexOf(inner));

    const rowCells = row.children || [];
    const skc = nthValueFromRowCell(rowCells[colIndexMap.skc], idx);
    const sku = nthValueFromRowCell(rowCells[colIndexMap.sku], idx);
    const platform = nthValueFromRowCell(rowCells[colIndexMap.platform], idx);

    // Extract product ID from column 1 (产品信息)
    let productId = '';
    const infoCell = rowCells[1];
    if (infoCell) {
      const spans = infoCell.querySelectorAll('.product-goodInfo-spacing');
      for (const sp of spans) {
        const txt = (sp.textContent || '').trim();
        if (txt.startsWith('产品ID')) {
          productId = txt.replace(/^产品ID[：:]\s*/, '');
          break;
        }
      }
    }

    log('delegation click', {idx, skc, sku, platform, productId});
    toastSet('查询价格中… 产品ID: ' + productId);

    // Fetch price from local server
    const clickedInner = inner;
    fetchPrice(productId, skc, sku, platform, function(result) {
      if (result.error) {
        toastSet('产品ID: ' + productId + ' / SKC: ' + skc + ' / SKU: ' + sku + ' / 平台SKU: ' + platform + ' / ⚠️ ' + result.error);
      } else {
        toastSet('产品ID: ' + productId + ' / SKC: ' + skc + ' / SKU: ' + sku + ' / 平台SKU: ' + platform + ' / 💰 USD ' + result.usd_price);
        var priceText = '$' + result.usd_price;
        priceCache.set(sku, priceText);
        clickedInner.textContent = priceText;
        clickedInner.style.color = '#1565c0';
        clickedInner.style.fontWeight = 'bold';
      }
    });
  }

  // Attach on window (capture phase) — bypasses any document proxy from micro-frontends
  window.addEventListener('click', handleCustomCellClick, true);
  window.addEventListener('mousedown', handleCustomCellClick, true);

  // Also try unsafeWindow in case Tampermonkey sandbox wraps window
  try {
    if (unsafeWindow && unsafeWindow !== window) {
      unsafeWindow.addEventListener('click', handleCustomCellClick, true);
      unsafeWindow.addEventListener('mousedown', handleCustomCellClick, true);
    }
  } catch(e) { log('unsafeWindow delegation error', e); }

  function __tmColCheck() {
    return {
      url: location.href,
      colIndexMap: { ...colIndexMap },
      headerInserted: document.querySelectorAll(`[${HDR_MARK}]`).length,
      bodyInserted: document.querySelectorAll(`[${CELL_MARK}]`).length,
    };
  }

  try { unsafeWindow.__tmColCheck = __tmColCheck; } catch (e) {}
  try { unsafeWindow.__tmColPing = () => LOG_PREFIX + ' pong v9'; } catch (e) {}

  let scheduled = false;
  function scheduleRun(reason) {
    if (scheduled) return;
    scheduled = true;

    setTimeout(() => {
      scheduled = false;
      try {
        const anchors = findHeaderAnchors();
        if (anchors.length) ensureHeaderInserted(anchors);

        const b = ensureBodyInserted();
        const c = __tmColCheck();

        badgeSet(`TM已运行 H:${c.headerInserted} B:${c.bodyInserted}`);
        log('run', reason, 'body replaced/inserted', b, c);
      } catch (e) {
        console.error(LOG_PREFIX, e);
        badgeSet('TM脚本已运行（error）');
      }
    }, 80);
  }

  scheduleRun('init');

  const mo = new MutationObserver(() => scheduleRun('mutation'));
  mo.observe(document.body, { childList: true, subtree: true });

  document.addEventListener('click', () => scheduleRun('click'), true);
})();
