/**
 * ID Tracker Collector — bookmarklet source.
 *
 * Run this on each page of the internal tool (10 IDs/page). You click once
 * on an example ID and Brand value on the page (any order); from those two
 * points it identifies the repeating "row" element (works for real <table>
 * markup as well as div/card-based grids) and records each field's
 * position *within* that row, rather than an independent selector per
 * field — this guarantees every field always lines up on the same row,
 * even when a field's own element has no distinguishing CSS class.
 * Vendor/Category/No. of SKUs/Next button are picked the same way once the
 * row is known. It accumulates rows across pages (deduped by ID, blank-
 * brand rows dropped), remembers Vendor/Category per brand as a fallback,
 * and produces a tab-separated block you paste directly into Google Sheets
 * (ID, Brand, Vendor, Category — "No of Child" stays manual as today).
 *
 * See build.js for how this turns into the javascript: bookmarklet URL,
 * and README.md for install/usage instructions.
 */
(function () {
  'use strict';

  // Bumped alongside the row-detection rewrite: earlier versions could
  // collect garbage rows (matched page chrome like nav/filter/action
  // labels instead of real product rows). Versioning the key means a
  // browser with old contaminated data starts clean instead of silently
  // merging garbage into a correct collection.
  var ROWS_KEY = '__idTrackerRows_v2';
  var MAP_KEY = '__idTrackerVendorMap_v1';
  var CFG_KEY = '__idTrackerConfig_v2';
  var PANEL_ID = '__idTrackerPanel';

  function loadJSON(key, fallback) {
    try {
      var raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) {
      return fallback;
    }
  }
  function saveJSON(key, val) {
    localStorage.setItem(key, JSON.stringify(val));
  }

  var state = {
    rows: loadJSON(ROWS_KEY, {}),        // { id: { id, brand, vendor, category } }
    vendorMap: loadJSON(MAP_KEY, {}),    // { brandLower: { brand, vendor, category } } — fallback per brand
    cfg: loadJSON(CFG_KEY, {
      rowSelector: '', rowSkip: 0,
      idPath: null, brandPath: null, vendorPath: null, categoryPath: null, skusPath: null,
      nextSelector: ''
    })
  };

  function persist() {
    saveJSON(ROWS_KEY, state.rows);
    saveJSON(MAP_KEY, state.vendorMap);
    saveJSON(CFG_KEY, state.cfg);
  }

  // ---------- field picking (works on tables, divs, cards — anything) ----------

  function cssEscape(s) {
    if (window.CSS && window.CSS.escape) return window.CSS.escape(s);
    return s.replace(/([^a-zA-Z0-9_-])/g, '\\$1');
  }

  function classTokens(el) {
    var c = el.className;
    if (!c || typeof c !== 'string') return [];
    return c.trim().split(/\s+/).filter(Boolean);
  }

  function selectorCandidates(el) {
    var out = [];
    var cur = el;
    var depth = 0;
    while (cur && cur.tagName && depth < 5) {
      var tag = cur.tagName.toLowerCase();
      var classes = classTokens(cur);
      if (classes.length) {
        for (var i = classes.length; i >= 1; i--) {
          out.push(tag + '.' + classes.slice(0, i).map(cssEscape).join('.'));
        }
      } else {
        out.push(tag);
      }
      cur = cur.parentElement;
      depth++;
    }
    return out;
  }

  function nthChildPath(el) {
    var parts = [];
    var cur = el;
    while (cur && cur.parentElement) {
      var idx = Array.prototype.indexOf.call(cur.parentElement.children, cur) + 1;
      parts.unshift(cur.tagName.toLowerCase() + ':nth-child(' + idx + ')');
      cur = cur.parentElement;
      if (parts.length > 8) break;
    }
    return parts.join(' > ');
  }

  // Upper bound on an acceptable match count — a genuine "same field on
  // every row" selector should match roughly a page's worth of rows, not
  // hundreds. Without this, a picked element with no distinguishing class
  // (falling back to something like a bare tag name) could get accepted
  // even though it matches unrelated elements sitewide.
  var MAX_REASONABLE_MATCHES = 200;

  function pickBestSelector(el) {
    var candidates = selectorCandidates(el);
    for (var i = 0; i < candidates.length; i++) {
      var sel = candidates[i];
      try {
        var matches = document.querySelectorAll(sel);
        if (matches.length >= 2 && matches.length <= MAX_REASONABLE_MATCHES && Array.prototype.indexOf.call(matches, el) !== -1) {
          return sel;
        }
      } catch (e) { /* invalid selector, skip */ }
    }
    return nthChildPath(el);
  }

  // For a single element (like a "Next" button) rather than a repeated row field.
  function pickUniqueSelector(el) {
    var candidates = selectorCandidates(el);
    for (var i = 0; i < candidates.length; i++) {
      var sel = candidates[i];
      try {
        var matches = document.querySelectorAll(sel);
        if (matches.length === 1 && matches[0] === el) return sel;
      } catch (e) { /* invalid selector, skip */ }
    }
    return nthChildPath(el);
  }

  // ---------- row detection + within-row relative paths ----------

  function sameTagAndClass(a, b) {
    if (!a || !b || a.tagName !== b.tagName) return false;
    return classTokens(a).join(' ') === classTokens(b).join(' ');
  }

  function closestCommonAncestor(elA, elB) {
    var ancestors = [];
    var cur = elA;
    while (cur) { ancestors.push(cur); cur = cur.parentElement; }
    cur = elB;
    while (cur) {
      if (ancestors.indexOf(cur) !== -1) return cur;
      cur = cur.parentElement;
    }
    return null;
  }

  // Given two elements from the SAME row (e.g. the row's ID and Brand
  // values), finds the repeating row container: walks up from their common
  // ancestor until it finds a level whose siblings share the same tag+class
  // (i.e. the same component repeated once per row).
  function establishRowRoot(elA, elB) {
    var ancestor = closestCommonAncestor(elA, elB);
    if (!ancestor) return null;
    var cur = ancestor;
    var depth = 0;
    while (cur && cur.parentElement && depth < 12) {
      var siblings = Array.prototype.filter.call(cur.parentElement.children, function (c) { return sameTagAndClass(c, cur); });
      if (siblings.length >= 2) return cur;
      cur = cur.parentElement;
      depth++;
    }
    return ancestor;
  }

  // Sequence of child indices from `root` down to `el` — reapplying this
  // sequence to any other row element finds the equivalent field there.
  function relativePath(root, el) {
    var path = [];
    var cur = el;
    while (cur && cur !== root) {
      var parent = cur.parentElement;
      if (!parent) return null;
      var idx = Array.prototype.indexOf.call(parent.children, cur);
      path.unshift(idx);
      cur = parent;
    }
    return cur === root ? path : null;
  }

  function applyPath(root, path) {
    var cur = root;
    for (var i = 0; i < path.length; i++) {
      if (!cur || !cur.children || !cur.children[path[i]]) return null;
      cur = cur.children[path[i]];
    }
    return cur;
  }

  function rawRows() {
    if (!state.cfg.rowSelector) return [];
    try { return Array.prototype.slice.call(document.querySelectorAll(state.cfg.rowSelector)); } catch (e) { return []; }
  }

  function currentRows() {
    var all = rawRows();
    var skip = state.cfg.rowSkip || 0;
    return skip > 0 ? all.slice(skip) : all;
  }

  function findContainingRow(el) {
    var rows = rawRows();
    for (var i = 0; i < rows.length; i++) {
      if (rows[i] === el || rows[i].contains(el)) return rows[i];
    }
    return null;
  }

  var pendingPick = null; // { field, el } — transient, holds one pick until a second establishes the row

  // Picking any field either (a) locates it within the already-known row
  // pattern, or (b) if no row is known yet, pairs it with a previously
  // stashed pick from a different field to establish the row for the first
  // time. Returns a short status message to show the user.
  function handleFieldPick(field, el) {
    var pathKey = field + 'Path';
    var rowEl = findContainingRow(el);
    if (rowEl) {
      var path = relativePath(rowEl, el);
      if (!path) return { ok: false, message: 'That element doesn\'t seem to be inside the current row pattern. Try a spot closer to the row\'s main content, or use "🔄 Reset row pattern" below if the page layout changed.' };
      state.cfg[pathKey] = path;
      pendingPick = null;
      persist();
      return { ok: true };
    }
    if (pendingPick && pendingPick.field !== field) {
      var newRowEl = establishRowRoot(pendingPick.el, el);
      if (!newRowEl) {
        pendingPick = null;
        return { ok: false, message: 'Couldn\'t work out the row pattern from those two picks. Try again with two values that are closer together in the same row.' };
      }
      var rowSel = pickBestSelector(newRowEl);
      var rowMatches = document.querySelectorAll(rowSel);
      var rowIdx = Array.prototype.indexOf.call(rowMatches, newRowEl);
      state.cfg.rowSelector = rowSel;
      state.cfg.rowSkip = rowIdx > 0 ? rowIdx : 0;
      state.cfg[pendingPick.field + 'Path'] = relativePath(newRowEl, pendingPick.el);
      state.cfg[pathKey] = relativePath(newRowEl, el);
      pendingPick = null;
      persist();
      return { ok: true };
    }
    pendingPick = { field: field, el: el };
    return { ok: 'pending', message: 'Got "' + FIELD_LABELS[field] + '". Now pick one more field (e.g. ' + (field === 'brand' ? 'ID' : 'Brand') + ') on the SAME row so the row pattern can be worked out.' };
  }

  function isDisabledEl(el) {
    if (!el) return true;
    if (el.disabled) return true;
    var aria = el.getAttribute && el.getAttribute('aria-disabled');
    if (aria === 'true') return true;
    if (el.className && typeof el.className === 'string' && /disabled/i.test(el.className)) return true;
    try {
      var style = window.getComputedStyle(el);
      if (style && (style.pointerEvents === 'none' || parseFloat(style.opacity) === 0)) return true;
    } catch (e) { /* ignore */ }
    return false;
  }

  function textOf(el) {
    return el.textContent.replace(/\s+/g, ' ').trim();
  }

  // ---------- data extraction ----------

  var FIELDS = ['id', 'brand', 'vendor', 'category', 'skus'];

  function selectorsConfigured() {
    return !!(state.cfg.rowSelector && state.cfg.idPath && state.cfg.brandPath);
  }

  function fieldElsViaRows(field) {
    var path = state.cfg[field + 'Path'];
    var rows = currentRows();
    if (!path) return rows.map(function () { return null; });
    return rows.map(function (rowEl) { return applyPath(rowEl, path); });
  }

  // "No. of SKUs" is usually shown as e.g. "Pending Variants (3)" — pull out just the count.
  function extractFieldValue(field, el) {
    if (!el) return '';
    var raw = textOf(el);
    if (field === 'skus') {
      var paren = raw.match(/\((\d+)\)/);
      if (paren) return paren[1];
      var digits = raw.match(/\d+/);
      return digits ? digits[0] : raw;
    }
    return raw;
  }

  function parseCurrentPage() {
    if (!selectorsConfigured()) return { rows: [], counts: {}, configured: false, totalRows: 0 };
    var elsByField = {};
    FIELDS.forEach(function (f) { elsByField[f] = fieldElsViaRows(f); });
    var totalRows = currentRows().length;
    var rows = [];
    for (var i = 0; i < totalRows; i++) {
      var row = {};
      FIELDS.forEach(function (f) {
        row[f] = extractFieldValue(f, elsByField[f][i]);
      });
      rows.push(row);
    }
    var counts = {};
    FIELDS.forEach(function (f) { counts[f] = elsByField[f].filter(Boolean).length; });
    return { rows: rows, counts: counts, configured: true, totalRows: totalRows };
  }

  function collectCurrentPage() {
    var parsed = parseCurrentPage();
    var added = 0, skippedBlank = 0;
    parsed.rows.forEach(function (r) {
      if (!r.id || !r.brand) { skippedBlank++; return; }
      if (!state.rows[r.id]) added++;
      state.rows[r.id] = { id: r.id, brand: r.brand, vendor: r.vendor, category: r.category, skus: r.skus };
      // Directly-scraped Vendor/Category are authoritative — keep the brand memory fresh from them.
      if (r.vendor || r.category) {
        state.vendorMap[brandKey(r.brand)] = { brand: r.brand, vendor: r.vendor, category: r.category };
      }
    });
    persist();
    return { added: added, skippedBlank: skippedBlank, counts: parsed.counts, configured: parsed.configured };
  }

  // ---------- auto-paginate (only works if "Next" advances without a full page reload) ----------

  var autoStopRequested = false;

  function pageSignature() {
    return parseCurrentPage().rows.map(function (r) { return r.id; }).join('|');
  }

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  // Mimics a real user click. Scrolls the element into view first (some UIs
  // ignore clicks on off-screen/virtualized elements), then uses the native
  // .click() — it already synthesizes the full pointer/mouse event sequence
  // per spec, so a component listening on mousedown/pointerdown still sees
  // it. Only falls back to manually dispatching a bare "click" event if
  // .click() isn't available at all. Never do both — that double-fires the
  // handler and can skip a page per click.
  function fireClick(el) {
    try { el.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) { /* ignore */ }
    if (typeof el.click === 'function') {
      try { el.click(); return; } catch (e) { /* fall through to manual dispatch */ }
    }
    try {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    } catch (e) { /* nothing more we can do */ }
  }

  function waitForPageChange(beforeSig, timeoutMs) {
    return new Promise(function (resolve) {
      var start = Date.now();
      (function poll() {
        if (autoStopRequested) { resolve(false); return; }
        var nowSig = pageSignature();
        if (nowSig && nowSig !== beforeSig) { resolve(true); return; }
        if (Date.now() - start > timeoutMs) { resolve(false); return; }
        setTimeout(poll, 250);
      })();
    });
  }

  // Runs until Next is missing/disabled, the page stops changing, or Stop is clicked.
  // onProgress(pageNum, totalCollected, totalSkippedBlank) is called after each page.
  async function autoCollectAllPages(onProgress) {
    autoStopRequested = false;
    var pageNum = 1;
    var totalSkipped = collectCurrentPage().skippedBlank;
    onProgress(pageNum, Object.keys(state.rows).length, totalSkipped);
    var maxPages = 500;
    while (!autoStopRequested && pageNum < maxPages) {
      var nextEl = document.querySelector(state.cfg.nextSelector);
      if (!nextEl || isDisabledEl(nextEl)) {
        return { stoppedReason: 'end', pages: pageNum, skipped: totalSkipped };
      }
      var beforeSig = pageSignature();
      fireClick(nextEl);
      var changed = await waitForPageChange(beforeSig, 10000);
      if (autoStopRequested) return { stoppedReason: 'user', pages: pageNum, skipped: totalSkipped };
      if (!changed) {
        // One retry — some UIs need a second nudge (e.g. a hover state before the first click "arms" the button).
        nextEl = document.querySelector(state.cfg.nextSelector);
        if (!nextEl || isDisabledEl(nextEl)) return { stoppedReason: 'end', pages: pageNum, skipped: totalSkipped };
        fireClick(nextEl);
        changed = await waitForPageChange(beforeSig, 10000);
        if (autoStopRequested) return { stoppedReason: 'user', pages: pageNum, skipped: totalSkipped };
        if (!changed) return { stoppedReason: 'no-change', pages: pageNum, skipped: totalSkipped };
      }
      pageNum++;
      totalSkipped += collectCurrentPage().skippedBlank;
      onProgress(pageNum, Object.keys(state.rows).length, totalSkipped);
      await sleep(250);
    }
    return { stoppedReason: autoStopRequested ? 'user' : 'max-pages', pages: pageNum, skipped: totalSkipped };
  }

  function brandKey(b) { return b.trim().toLowerCase(); }

  function effectiveVendorCategory(row) {
    var fallback = state.vendorMap[brandKey(row.brand)] || {};
    return {
      vendor: row.vendor || fallback.vendor || '',
      category: row.category || fallback.category || ''
    };
  }

  function missingBrands() {
    var seen = {};
    var out = [];
    Object.keys(state.rows).forEach(function (id) {
      var r = state.rows[id];
      var k = brandKey(r.brand);
      if (!seen[k]) {
        seen[k] = true;
        var eff = effectiveVendorCategory(r);
        if (!eff.vendor || !eff.category) out.push(r.brand);
      }
    });
    return out;
  }

  function buildTSV() {
    var includeSkus = !!state.cfg.skusPath;
    var lines = [];
    Object.keys(state.rows).forEach(function (id) {
      var r = state.rows[id];
      var eff = effectiveVendorCategory(r);
      var cols = [r.id, r.brand, eff.vendor, eff.category];
      if (includeSkus) cols.push(r.skus || '');
      lines.push(cols.join('\t'));
    });
    return lines.join('\n');
  }

  function copyText(text, onDone) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { onDone(true); }, function () { onDone(false, text); });
    } else {
      onDone(false, text);
    }
  }

  // ---------- UI ----------

  var panel, body;
  var pickCleanup = null;

  function ensurePanel() {
    var existing = document.getElementById(PANEL_ID);
    if (existing) existing.remove();
    panel = document.createElement('div');
    panel.id = PANEL_ID;
    panel.style.cssText = 'position:fixed;top:16px;right:16px;width:340px;max-height:85vh;overflow:auto;' +
      'background:#1e1e2e;color:#eee;font:13px/1.4 -apple-system,Segoe UI,sans-serif;border-radius:10px;' +
      'box-shadow:0 8px 30px rgba(0,0,0,.4);z-index:2147483647;padding:12px;';
    var header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;';
    header.innerHTML = '<strong>ID Tracker</strong>';
    var closeBtn = button('✕', function () { stopPicking(); panel.remove(); }, 'transparent');
    closeBtn.style.padding = '2px 8px';
    header.appendChild(closeBtn);
    panel.appendChild(header);
    body = document.createElement('div');
    panel.appendChild(body);
    document.body.appendChild(panel);
  }

  function button(label, onClick, bg) {
    var b = document.createElement('button');
    b.textContent = label;
    b.style.cssText = 'margin:3px 4px 3px 0;padding:6px 10px;border:none;border-radius:6px;cursor:pointer;' +
      'background:' + (bg || '#4c6ef5') + ';color:#fff;font-size:12px;';
    b.onclick = onClick;
    return b;
  }

  function textInput(value, placeholder) {
    var i = document.createElement('input');
    i.type = 'text';
    i.value = value || '';
    i.placeholder = placeholder || '';
    i.style.cssText = 'width:100%;margin:2px 0;padding:4px;border-radius:4px;border:1px solid #444;box-sizing:border-box;';
    return i;
  }

  function numberInput(value, onChange, width) {
    var i = document.createElement('input');
    i.type = 'number';
    i.min = '0';
    i.value = value;
    i.style.cssText = 'width:' + (width || '50px') + ';margin:0 4px;padding:3px;border-radius:4px;border:1px solid #444;';
    i.oninput = function () { onChange(parseInt(i.value, 10) || 0); };
    return i;
  }

  function renderMain() {
    stopPicking();
    body.innerHTML = '';
    var count = Object.keys(state.rows).length;
    var summary = document.createElement('div');
    summary.style.marginBottom = '4px';
    summary.innerHTML = 'Collected so far: <strong>' + count + '</strong> unique ID(s)';
    body.appendChild(summary);

    var configured = selectorsConfigured();
    var status = document.createElement('div');
    status.style.cssText = 'font-size:11px;color:#aaa;margin-bottom:8px;';
    status.textContent = configured ? 'ID & Brand fields are set up on this page layout.' : '⚠ Set up ID & Brand fields first (below).';
    body.appendChild(status);

    body.appendChild(button('➕ Collect this page', function () {
      var r = collectCurrentPage();
      renderMain();
      if (!r.configured) {
        toast('Set up ID & Brand fields first.');
      } else if (r.counts.id !== r.counts.brand) {
        toast(r.added + ' added, ' + r.skippedBlank + ' skipped — ⚠ found ' + r.counts.id + ' ID(s) but ' + r.counts.brand + ' Brand(s), check Setup.');
      } else {
        toast(r.added + ' new row(s) added' + (r.skippedBlank ? ', ' + r.skippedBlank + ' skipped (blank)' : '') + '.');
      }
    }));

    body.appendChild(button('🎯 Setup fields', renderSetup, '#495057'));
    body.appendChild(document.createElement('br'));

    if (configured && state.cfg.nextSelector) {
      body.appendChild(button('▶ Auto-collect all pages', renderAutoRun, '#0ca678'));
      body.appendChild(document.createElement('br'));
    }

    body.appendChild(button('📋 Finish & copy for Sheet', renderFinish, '#2f9e44'));
    body.appendChild(button('🏷 Vendor/Category list', renderVendorList, '#495057'));
    body.appendChild(document.createElement('br'));

    body.appendChild(button('🧹 Clear collected IDs', function () {
      if (confirm('Clear all collected rows for this batch? (Vendor/Category memory is kept.)')) {
        state.rows = {};
        persist();
        renderMain();
      }
    }, '#c92a2a'));

    var hint = document.createElement('div');
    hint.style.cssText = 'margin-top:10px;font-size:11px;color:#aaa;';
    hint.textContent = state.cfg.nextSelector
      ? 'Or go page-by-page yourself: click Next in the tool, click this bookmarklet again, and hit "Collect this page".'
      : 'Go to the next page in the tool, then click this bookmarklet again and hit "Collect this page". (Pick a "Next" button in Setup to enable one-click Auto-collect instead.)';
    body.appendChild(hint);
  }

  function startPicking(target, onPicked) {
    stopPicking();
    var prevOutline = null, prevEl = null;

    function onMouseOver(e) {
      if (panel.contains(e.target)) return;
      if (prevEl) prevEl.style.outline = prevOutline;
      prevEl = e.target;
      prevOutline = prevEl.style.outline;
      prevEl.style.outline = '2px solid #fab005';
    }
    function onClick(e) {
      if (panel.contains(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
      var el = e.target;
      cleanup();
      onPicked(el);
    }
    function onKeyDown(e) {
      if (e.key === 'Escape') { cleanup(); renderSetup(); }
    }
    function cleanup() {
      if (prevEl) prevEl.style.outline = prevOutline;
      document.removeEventListener('mouseover', onMouseOver, true);
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('keydown', onKeyDown, true);
      pickCleanup = null;
    }

    document.addEventListener('mouseover', onMouseOver, true);
    document.addEventListener('click', onClick, true);
    document.addEventListener('keydown', onKeyDown, true);
    pickCleanup = cleanup;
  }

  function stopPicking() {
    if (pickCleanup) pickCleanup();
  }

  function renderAutoRun() {
    stopPicking();
    autoStopRequested = false;
    body.innerHTML = '';
    var msg = document.createElement('div');
    msg.style.cssText = 'margin-bottom:8px;white-space:pre-line;';
    msg.textContent = '⏳ Collecting page 1… (' + Object.keys(state.rows).length + ' unique ID(s) so far)';
    body.appendChild(msg);
    var stopBtn = button('⏹ Stop', function () { autoStopRequested = true; }, '#c92a2a');
    body.appendChild(stopBtn);
    var note = document.createElement('div');
    note.style.cssText = 'margin-top:8px;font-size:11px;color:#aaa;';
    note.textContent = 'If the tool reloads the whole page to go to the next set, this will only get page 1 — use "Collect this page" manually instead in that case.';
    body.appendChild(note);

    autoCollectAllPages(function (pageNum, total, skipped) {
      msg.textContent = '⏳ Collecting page ' + pageNum + '… (' + total + ' unique ID(s) so far'
        + (skipped ? ', ' + skipped + ' skipped for blank Brand' : '') + ')';
    }).then(function (result) {
      body.innerHTML = '';
      var done = document.createElement('div');
      done.style.marginBottom = '6px';
      var total = Object.keys(state.rows).length;
      var reasonText = {
        'end': '✅ Done — reached the last page.',
        'user': '⏹ Stopped.',
        'no-change': '⚠ Stopped — the page didn\'t change after clicking Next twice (may be the last page, the click isn\'t reaching the real button, or it needs more time — try "🧪 Test Next click" in Setup).',
        'max-pages': '⚠ Stopped — hit the safety limit of 500 pages.'
      }[result.stoppedReason] || 'Done.';
      done.textContent = reasonText + ' Collected ' + total + ' unique ID(s) across ' + result.pages + ' page(s)'
        + (result.skipped ? ' (' + result.skipped + ' row(s) skipped for blank Brand).' : '.');
      body.appendChild(done);
      body.appendChild(button('← Back', renderMain, '#495057'));
    }).catch(function (e) {
      body.innerHTML = '';
      var err = document.createElement('div');
      err.textContent = '⚠ Auto-collect stopped due to an error: ' + (e && e.message ? e.message : e);
      body.appendChild(err);
      body.appendChild(button('← Back', renderMain, '#495057'));
    });
  }

  var FIELD_LABELS = { id: 'ID', brand: 'Brand', vendor: 'Vendor', category: 'Category', skus: 'No. of SKUs' };
  var OPTIONAL_FIELDS = { vendor: true, category: true, skus: true };

  function renderSetup() {
    stopPicking();
    body.innerHTML = '';

    var info = document.createElement('div');
    info.style.marginBottom = '8px';
    info.innerHTML = 'Click a button below, then click the matching value <u>on the page</u> (in the first row). Press Esc to cancel. ID and Brand are required — the first time you pick one of them, you\'ll be asked to pick the other too so the row pattern can be worked out; after that every field just needs one click. Vendor and Category are optional — leave unset if this page doesn\'t show them, and you\'ll be asked for them once per brand instead. No. of SKUs is optional too — leave it unset to keep filling that in by hand, or pick it (e.g. the number in "Pending Variants (3)") to add it as a 5th column automatically.';
    body.appendChild(info);

    var rowRawCount = rawRows().length;
    var rowCount = currentRows().length;
    var rowStatus = document.createElement('div');
    rowStatus.style.cssText = 'margin-bottom:4px;';
    rowStatus.textContent = 'Row pattern: ' + (state.cfg.rowSelector
      ? ('✅ found ' + rowCount + ' row(s)' + (state.cfg.rowSkip ? ' (after skipping ' + state.cfg.rowSkip + ' of ' + rowRawCount + ' total matches)' : ''))
      : '❌ not established yet — pick ID and Brand below to begin.');
    body.appendChild(rowStatus);

    if (state.cfg.rowSelector && rowCount === 0) {
      var rowSkipRow = document.createElement('div');
      rowSkipRow.style.cssText = 'font-size:11px;color:#aaa;margin-bottom:4px;';
      rowSkipRow.appendChild(document.createTextNode('Row skip count: '));
      rowSkipRow.appendChild(numberInput(state.cfg.rowSkip || 0, function (v) {
        state.cfg.rowSkip = Math.max(0, v);
        persist();
        renderSetup();
      }, '45px'));
      rowSkipRow.appendChild(document.createTextNode(rowRawCount === 0
        ? ' — 0 total matches even before skipping: the row selector isn\'t matching anything right now.'
        : ' — try lowering this (currently skipping all ' + rowRawCount + ' match(es) found).'));
      body.appendChild(rowSkipRow);
    }

    if (state.cfg.rowSelector) {
      body.appendChild(button('🔄 Reset row pattern', function () {
        if (!confirm('This clears the row pattern and every picked field (ID, Brand, Vendor, Category, No. of SKUs). Continue?')) return;
        state.cfg.rowSelector = '';
        state.cfg.rowSkip = 0;
        FIELDS.forEach(function (f) { state.cfg[f + 'Path'] = null; });
        pendingPick = null;
        persist();
        renderSetup();
      }, '#c92a2a'));
    }

    FIELDS.forEach(function (field) {
      var pathKey = field + 'Path';
      var path = state.cfg[pathKey];
      var status = document.createElement('div');
      status.style.cssText = 'margin-top:10px;margin-bottom:4px;';
      var resolvedCount = path ? fieldElsViaRows(field).filter(Boolean).length : 0;
      var label = FIELD_LABELS[field] + (OPTIONAL_FIELDS[field] ? ' (optional)' : '');
      status.textContent = label + ': ' + (path
        ? ('✅ set (' + resolvedCount + ' of ' + rowCount + ' row(s) have a value)')
        : '❌ not set');
      body.appendChild(status);

      var row = document.createElement('div');
      row.appendChild(button('🎯 Pick ' + FIELD_LABELS[field] + ' value', function () {
        body.innerHTML = '';
        var msg = document.createElement('div');
        msg.textContent = 'Now click the ' + FIELD_LABELS[field] + ' value in the FIRST row of the list (Esc to cancel)...';
        body.appendChild(msg);
        startPicking(field, function (el) {
          var result = handleFieldPick(field, el);
          if (result.message) alert(result.message);
          renderSetup();
        });
      }));
      if (path) {
        row.appendChild(button('Clear', function () {
          state.cfg[pathKey] = null;
          persist();
          renderSetup();
        }, '#495057'));
      }
      body.appendChild(row);
    });

    var nextStatus = document.createElement('div');
    nextStatus.style.cssText = 'margin-top:14px;margin-bottom:4px;border-top:1px solid #333;padding-top:10px;';
    var nextOk = state.cfg.nextSelector ? document.querySelector(state.cfg.nextSelector) : null;
    var nextInfo = state.cfg.nextSelector
      ? (nextOk ? ('✅ set (' + (isDisabledEl(nextOk) ? 'currently disabled — normal on the last page' : 'found, enabled') + ')') : '⚠ set, but not found on this page')
      : '❌ not set';
    nextStatus.textContent = 'Next button (optional, enables Auto-collect): ' + nextInfo;
    body.appendChild(nextStatus);
    var nextRow = document.createElement('div');
    nextRow.appendChild(button('🎯 Pick Next button', function () {
      body.innerHTML = '';
      var msg = document.createElement('div');
      msg.textContent = 'Now click the "Next" button/link that moves to the next set of rows (Esc to cancel)...';
      body.appendChild(msg);
      startPicking('next', function (el) {
        state.cfg.nextSelector = pickUniqueSelector(el);
        persist();
        renderSetup();
      });
    }));
    if (state.cfg.nextSelector) {
      nextRow.appendChild(button('Clear', function () {
        state.cfg.nextSelector = '';
        persist();
        renderSetup();
      }, '#495057'));
      nextRow.appendChild(button('🧪 Test Next click (waits up to 10s)', function () {
        var el = document.querySelector(state.cfg.nextSelector);
        if (!el) { alert('Next button not found on this page.'); return; }
        if (isDisabledEl(el)) { alert('Next button looks disabled right now — nothing to test (maybe this is the last page).'); return; }
        var beforeIds = parseCurrentPage().rows.map(function (r) { return r.id; });
        var before = beforeIds.join('|');
        fireClick(el);
        waitForPageChange(before, 10000).then(function (changed) {
          var afterIds = parseCurrentPage().rows.map(function (r) { return r.id; });
          if (changed) {
            alert('✅ It worked — rows changed after clicking Next. Auto-collect should work now.\n\nBefore: ' + beforeIds.slice(0, 3).join(', ') + '…\nAfter: ' + afterIds.slice(0, 3).join(', ') + '…');
          } else {
            alert('⚠ Rows did NOT change within 10s of clicking Next (this is the same wait Auto-collect uses).\n\n'
              + 'Before: ' + (beforeIds.slice(0, 3).join(', ') || '(none read)') + '…\n'
              + 'After:  ' + (afterIds.slice(0, 3).join(', ') || '(none read)') + '…\n\n'
              + (afterIds.length === 0
                ? 'The ID/Brand selectors found nothing at all just now — they may only match while this exact page is showing. Try re-picking ID and Brand on whichever page is showing now.'
                : (before === afterIds.join('|')
                  ? 'Same IDs read before and after — if the page visibly changed, the picked ID field might be matching something that stays constant (e.g. a header/label) instead of the row values. Try re-picking ID.'
                  : 'Rows were read both times but look unchanged — if you saw the page visibly change, this may just need more time; if it looked the same, this Next click may be re-showing the same page.')));
          }
        });
      }, '#495057'));
    }
    body.appendChild(nextRow);

    body.appendChild(document.createElement('br'));
    body.appendChild(button('👁 Preview parsed rows', function () {
      var parsed = parseCurrentPage();
      if (!parsed.configured) { alert('Set up ID and Brand fields first.'); return; }
      var lines = parsed.rows.slice(0, 6).map(function (r) {
        return 'ID=' + r.id + ' | Brand=' + r.brand + ' | Vendor=' + (r.vendor || '(none)') + ' | Category=' + (r.category || '(none)') + ' | SKUs=' + (r.skus || '(none)');
      });
      var mismatch = FIELDS.filter(function (f) { return state.cfg[f + 'Path'] && parsed.counts[f] < parsed.totalRows; });
      var extra = mismatch.length
        ? '\n\n⚠ ' + mismatch.map(function (f) { return FIELD_LABELS[f] + ' has a value on ' + parsed.counts[f] + ' of ' + parsed.totalRows + ' row(s)'; }).join('; ') + ' — some rows may be missing that field, or it needs re-picking.'
        : '';
      alert(lines.length ? ('First rows parsed as:\n\n' + lines.join('\n') + extra) : 'No rows parsed — try picking again.');
    }, '#495057'));
    body.appendChild(document.createElement('br'));

    body.appendChild(button('← Back', function () { stopPicking(); renderMain(); }, '#495057'));
  }

  function renderFinish() {
    var missing = missingBrands();
    if (missing.length === 0) {
      finalizeCopy();
      return;
    }
    body.innerHTML = '';
    var info = document.createElement('div');
    info.style.marginBottom = '6px';
    info.innerHTML = missing.length + ' new brand(s) need Vendor/Category (remembered for next time):';
    body.appendChild(info);

    var inputs = {};
    missing.forEach(function (brand) {
      var wrap = document.createElement('div');
      wrap.style.cssText = 'border-top:1px solid #333;padding:6px 0;';
      var label = document.createElement('div');
      label.innerHTML = '<strong>' + escapeHtml(brand) + '</strong>';
      wrap.appendChild(label);

      var vIn = textInput('', 'Vendor');
      var cIn = textInput('', 'Category');
      wrap.appendChild(vIn);
      wrap.appendChild(cIn);

      // suggest category from vendor map if this vendor was already used elsewhere
      vIn.oninput = function () {
        if (!cIn.value) {
          var match = Object.keys(state.vendorMap).map(function (k) { return state.vendorMap[k]; })
            .filter(function (v) { return v.vendor && v.vendor.trim().toLowerCase() === vIn.value.trim().toLowerCase(); })
            .pop();
          if (match) cIn.value = match.category || '';
        }
      };

      var fillDown = button('↓ fill vendor down', function () {
        Object.keys(inputs).forEach(function (b) {
          if (!inputs[b].vendor.value) inputs[b].vendor.value = vIn.value;
        });
      }, '#495057');
      fillDown.style.fontSize = '11px';
      wrap.appendChild(fillDown);

      inputs[brand] = { vendor: vIn, category: cIn };
      body.appendChild(wrap);
    });

    body.appendChild(button('Save & copy for Sheet', function () {
      missing.forEach(function (brand) {
        var v = inputs[brand].vendor.value.trim();
        var c = inputs[brand].category.value.trim();
        state.vendorMap[brandKey(brand)] = { brand: brand, vendor: v, category: c };
      });
      persist();
      finalizeCopy();
    }, '#2f9e44'));
    body.appendChild(button('← Back', renderMain, '#495057'));
  }

  function finalizeCopy() {
    var tsv = buildTSV();
    copyText(tsv, function (ok, text) {
      body.innerHTML = '';
      var msg = document.createElement('div');
      msg.style.marginBottom = '6px';
      var cols = 'ID, Brand, Vendor, Category' + (state.cfg.skusPath ? ', No. of SKUs' : '');
      msg.textContent = ok
        ? '✅ Copied to clipboard — paste into Google Sheets (columns: ' + cols + ').'
        : '⚠️ Auto-copy blocked by the browser. Select all text below and copy manually:';
      body.appendChild(msg);
      if (!ok) {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'width:100%;height:140px;';
        ta.onclick = function () { ta.select(); };
        body.appendChild(ta);
      }
      body.appendChild(button('← Back', renderMain, '#495057'));
    });
  }

  function renderVendorList() {
    body.innerHTML = '';
    var keys = Object.keys(state.vendorMap);
    var info = document.createElement('div');
    info.textContent = keys.length + ' brand(s) remembered:';
    info.style.marginBottom = '6px';
    body.appendChild(info);

    keys.forEach(function (k) {
      var v = state.vendorMap[k];
      var row = document.createElement('div');
      row.style.cssText = 'border-top:1px solid #333;padding:4px 0;display:flex;justify-content:space-between;align-items:center;';
      var txt = document.createElement('span');
      txt.textContent = v.brand + ' → ' + (v.vendor || '(no vendor)') + ' / ' + (v.category || '(no category)');
      row.appendChild(txt);
      var del = button('✕', function () {
        delete state.vendorMap[k];
        persist();
        renderVendorList();
      }, '#c92a2a');
      del.style.padding = '2px 6px';
      row.appendChild(del);
      body.appendChild(row);
    });

    body.appendChild(button('← Back', renderMain, '#495057'));
  }

  function toast(text) {
    var t = document.createElement('div');
    t.textContent = text;
    t.style.cssText = 'position:fixed;bottom:16px;right:16px;background:#333;color:#fff;padding:8px 12px;' +
      'border-radius:6px;font:12px sans-serif;z-index:2147483647;max-width:320px;';
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2600);
  }

  function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  ensurePanel();
  renderMain();
})();
