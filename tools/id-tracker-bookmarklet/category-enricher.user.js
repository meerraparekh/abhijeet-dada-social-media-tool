// ==UserScript==
// @name         ID Tracker — Category Enricher
// @namespace    id-tracker-bookmarklet
// @version      1.0.0
// @description  Auto-visits each product collected by the ID Tracker bookmarklet, reads the real Category from its detail page, and chains to the next one automatically.
// @match        https://sellerportal.mumzworld.com/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==
/* eslint-disable no-var */
(function () {
  'use strict';

  // Same key the ID Tracker bookmarklet reads/writes — this script only
  // ever overwrites the "category" field on rows that already exist there.
  var ROWS_KEY = '__idTrackerRows_v2';
  var ENRICH_CFG_KEY = '__idTrackerEnrichConfig_v1';   // { categorySelector }
  var ENRICH_QUEUE_KEY = '__idTrackerEnrichQueue_v1';  // { currentId, pending: [ids], listUrl, done, total, skipped }
  var ENRICH_RESULT_KEY = '__idTrackerEnrichResult_v1'; // one-shot summary shown after the run finishes

  function loadJSON(key, fallback) {
    try {
      var raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) { return fallback; }
  }
  function saveJSON(key, val) { localStorage.setItem(key, JSON.stringify(val)); }
  function clearKey(key) { localStorage.removeItem(key); }

  function loadRows() { return loadJSON(ROWS_KEY, {}); }
  function saveRows(rows) { saveJSON(ROWS_KEY, rows); }
  function loadEnrichCfg() { return loadJSON(ENRICH_CFG_KEY, { categorySelector: '' }); }
  function saveEnrichCfg(cfg) { saveJSON(ENRICH_CFG_KEY, cfg); }
  function loadQueue() { return loadJSON(ENRICH_QUEUE_KEY, null); }
  function saveQueue(q) { saveJSON(ENRICH_QUEUE_KEY, q); }
  function clearQueue() { clearKey(ENRICH_QUEUE_KEY); }

  // ---------- element picking (same approach as the bookmarklet, simplified for a single element) ----------

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
        for (var i = classes.length; i >= 1; i--) out.push(tag + '.' + classes.slice(0, i).map(cssEscape).join('.'));
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
  function pickUniqueSelector(el) {
    var candidates = selectorCandidates(el);
    for (var i = 0; i < candidates.length; i++) {
      try {
        var matches = document.querySelectorAll(candidates[i]);
        if (matches.length === 1 && matches[0] === el) return candidates[i];
      } catch (e) { /* invalid selector, skip */ }
    }
    return nthChildPath(el);
  }

  // Form fields (especially disabled/read-only ones, like a locked
  // Category input) show their text via .value, not .textContent — an
  // <input> has no text node children at all.
  function textOf(el) {
    var tag = el.tagName ? el.tagName.toLowerCase() : '';
    if (tag === 'input' || tag === 'textarea') return (el.value || '').replace(/\s+/g, ' ').trim();
    if (tag === 'select') {
      var opt = el.options && el.options[el.selectedIndex];
      return ((opt ? opt.text : el.value) || '').replace(/\s+/g, ' ').trim();
    }
    return el.textContent.replace(/\s+/g, ' ').trim();
  }

  function sleep(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  // Detail pages can take a moment to render — poll for the calibrated
  // Category selector for up to `timeoutMs` before giving up on this product.
  function waitForCategoryText(selector, timeoutMs) {
    return new Promise(function (resolve) {
      var start = Date.now();
      (function poll() {
        var el = null;
        try { el = document.querySelector(selector); } catch (e) { /* ignore */ }
        var text = el ? textOf(el) : '';
        if (text) { resolve(text); return; }
        if (Date.now() - start > timeoutMs) { resolve(''); return; }
        setTimeout(poll, 300);
      })();
    });
  }

  // ---------- UI ----------

  var PANEL_ID = '__idTrackerEnrichPanel';
  var panel, body;
  var pickCleanup = null;

  function ensurePanel() {
    var existing = document.getElementById(PANEL_ID);
    if (existing) existing.remove();
    panel = document.createElement('div');
    panel.id = PANEL_ID;
    panel.style.cssText = 'position:fixed;bottom:16px;left:16px;width:320px;max-height:70vh;overflow:auto;' +
      'background:#1e1e2e;color:#eee;font:13px/1.4 -apple-system,Segoe UI,sans-serif;border-radius:10px;' +
      'box-shadow:0 8px 30px rgba(0,0,0,.4);z-index:2147483647;padding:12px;';
    var header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;';
    header.innerHTML = '<strong>Category Enricher</strong>';
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

  // Clicking and Enter-while-hovering both fail on a genuinely disabled
  // Category field in some browsers — so this always also shows a live
  // "Hovering: ..." readout plus a normal, always-clickable "Pick hovered
  // element" button in the panel (never part of the page, so never
  // disabled). That button is the guaranteed path.
  function startPicking(onPicked) {
    stopPicking();
    var prevOutline = null, prevEl = null;

    var liveBox = document.createElement('div');
    liveBox.style.cssText = 'margin-top:8px;padding:6px;border:1px dashed #555;border-radius:6px;font-size:11px;color:#aaa;';
    var hoverLabel = document.createElement('div');
    hoverLabel.textContent = 'Hovering: (move your mouse over the value)';
    liveBox.appendChild(hoverLabel);
    var confirmBtn = button('✅ Pick hovered element', function () {
      if (!prevEl) return;
      var el = prevEl;
      cleanup();
      onPicked(el);
    }, '#0ca678');
    confirmBtn.disabled = true;
    confirmBtn.style.opacity = '0.5';
    liveBox.appendChild(confirmBtn);
    body.appendChild(liveBox);

    function describe(el) {
      var tag = el.tagName ? el.tagName.toLowerCase() : '?';
      var text = (el.value || el.textContent || '').replace(/\s+/g, ' ').trim();
      return tag + (text ? ': "' + text.slice(0, 40) + (text.length > 40 ? '…' : '') + '"' : ' (no text)');
    }

    // Uses elementFromPoint (pure geometry) rather than trusting the
    // hovered element to dispatch its own mouseover — a disabled form
    // control's event behavior varies enough across browsers that relying
    // on it to fire anything at all isn't safe. This can't be suppressed
    // by any element's disabled state.
    function onMouseMove(e) {
      var el = document.elementFromPoint ? document.elementFromPoint(e.clientX, e.clientY) : e.target;
      if (!el || el === prevEl || panel.contains(el)) return;
      if (prevEl) prevEl.style.outline = prevOutline;
      prevEl = el;
      prevOutline = prevEl.style.outline;
      prevEl.style.outline = '2px solid #fab005';
      hoverLabel.textContent = 'Hovering: ' + describe(prevEl);
      confirmBtn.disabled = false;
      confirmBtn.style.opacity = '1';
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
      if (e.key === 'Escape') { cleanup(); renderLauncher(); return; }
      if (e.key === 'Enter' && prevEl) {
        e.preventDefault();
        var el = prevEl;
        cleanup();
        onPicked(el);
      }
    }
    function cleanup() {
      if (prevEl) prevEl.style.outline = prevOutline;
      document.removeEventListener('mousemove', onMouseMove, true);
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('keydown', onKeyDown, true);
      pickCleanup = null;
    }
    document.addEventListener('mousemove', onMouseMove, true);
    document.addEventListener('click', onClick, true);
    document.addEventListener('keydown', onKeyDown, true);
    pickCleanup = cleanup;
  }
  function stopPicking() { if (pickCleanup) pickCleanup(); }

  function rowsWithLinks() {
    var rows = loadRows();
    return Object.keys(rows).filter(function (id) { return rows[id].productLink; });
  }

  function renderLauncher() {
    stopPicking();
    body.innerHTML = '';
    var cfg = loadEnrichCfg();
    var eligible = rowsWithLinks();

    var linkInfo = document.createElement('div');
    linkInfo.style.marginBottom = '6px';
    linkInfo.textContent = eligible.length + ' product(s) have a link captured (pick "Product Link" in the ID Tracker bookmarklet\'s Setup if this is 0).';
    body.appendChild(linkInfo);

    var catStatus = document.createElement('div');
    catStatus.style.marginBottom = '6px';
    catStatus.textContent = 'Category field: ' + (cfg.categorySelector ? '✅ set' : '❌ not set — open one product\'s detail page first, then pick it here.');
    body.appendChild(catStatus);

    body.appendChild(button('🎯 Pick Category field', function () {
      body.innerHTML = '';
      var msg = document.createElement('div');
      msg.textContent = 'Now click the Category value on this page. If clicking it does nothing (common — it\'s usually greyed out/disabled once a product already exists), hover over it and use the "✅ Pick hovered element" button below instead. Esc to cancel.';
      body.appendChild(msg);
      startPicking(function (el) {
        cfg.categorySelector = pickUniqueSelector(el);
        saveEnrichCfg(cfg);
        renderLauncher();
      });
    }));
    if (cfg.categorySelector) {
      body.appendChild(button('Clear', function () {
        cfg.categorySelector = '';
        saveEnrichCfg(cfg);
        renderLauncher();
      }, '#495057'));
    }
    body.appendChild(document.createElement('br'));

    var canStart = cfg.categorySelector && eligible.length > 0;
    var startBtn = button('▶ Start enrichment (' + eligible.length + ')', function () {
      var queue = { currentId: eligible[0], pending: eligible.slice(1), listUrl: window.location.href, done: 0, total: eligible.length, skipped: 0 };
      saveQueue(queue);
      var rows = loadRows();
      navigateTo(rows[queue.currentId].productLink);
    }, '#0ca678');
    if (!canStart) { startBtn.disabled = true; startBtn.style.opacity = '0.5'; startBtn.style.cursor = 'not-allowed'; }
    body.appendChild(startBtn);

    var note = document.createElement('div');
    note.style.cssText = 'margin-top:8px;font-size:11px;color:#aaa;';
    note.textContent = 'Once started, this visits each product\'s page automatically (full page loads), reads Category, and chains to the next — no clicking needed. A brief pause with a Stop button appears between each one.';
    body.appendChild(note);
  }

  function renderProgress(msg, showStop) {
    body.innerHTML = '';
    var m = document.createElement('div');
    m.style.marginBottom = '8px';
    m.style.whiteSpace = 'pre-line';
    m.textContent = msg;
    body.appendChild(m);
    if (showStop) {
      body.appendChild(button('⏹ Stop here', function () {
        var q = loadQueue();
        var listUrl = q && q.listUrl;
        var done = q ? q.done : 0, total = q ? q.total : 0, skipped = q ? q.skipped : 0;
        clearQueue();
        saveJSON(ENRICH_RESULT_KEY, { stopped: true, done: done, total: total, skipped: skipped });
        if (listUrl) navigateTo(listUrl); else renderLauncher();
      }, '#c92a2a'));
    }
  }

  function navigateTo(url) { window.location.href = url; }

  function showResultIfAny() {
    var result = loadJSON(ENRICH_RESULT_KEY, null);
    if (!result) return false;
    clearKey(ENRICH_RESULT_KEY);
    ensurePanel();
    var text = (result.stopped ? '⏹ Stopped. ' : '✅ Enrichment complete. ')
      + result.done + ' of ' + result.total + ' updated'
      + (result.skipped ? ', ' + result.skipped + ' skipped (Category not found in time)' : '') + '.';
    renderProgress(text, false);
    body.appendChild(document.createElement('br'));
    body.appendChild(button('← Back', renderLauncher, '#495057'));
    return true;
  }

  // ---------- main per-load flow ----------

  async function runQueueStep(queue) {
    ensurePanel();
    var cfg = loadEnrichCfg();
    var rows = loadRows();
    var id = queue.currentId;

    renderProgress('⏳ Reading Category for product #' + id + '… (' + (queue.done + 1) + '/' + queue.total + ')', false);

    var text = cfg.categorySelector ? await waitForCategoryText(cfg.categorySelector, 8000) : '';
    if (text && rows[id]) {
      rows[id].category = text;
      saveRows(rows);
    } else {
      queue.skipped = (queue.skipped || 0) + 1;
    }
    queue.done = (queue.done || 0) + 1;

    // Find the next pending row that still has a link (should always be
    // true — only eligible rows are queued — but skip defensively rather
    // than risk extracting from the wrong page if one goes missing).
    var pending = queue.pending;
    var next = null, nextUrl = null;
    while (pending.length) {
      var candidate = pending.shift();
      var url = rows[candidate] && rows[candidate].productLink;
      if (url) { next = candidate; nextUrl = url; break; }
      queue.skipped = (queue.skipped || 0) + 1;
      queue.done = queue.done + 1;
    }

    if (!next) {
      // Finished — go back to the list.
      saveJSON(ENRICH_RESULT_KEY, { stopped: false, done: queue.done, total: queue.total, skipped: queue.skipped || 0 });
      clearQueue();
      renderProgress((text ? '✅ Got "' + text + '". ' : '⚠ Category not found. ') + 'Done — returning to the list…', false);
      await sleep(900);
      navigateTo(queue.listUrl);
      return;
    }

    queue.currentId = next;
    queue.pending = pending;
    saveQueue(queue);

    // queue is already saved with the advanced state, so if Stop is clicked
    // during this pause, renderProgress's built-in handler picks it up correctly.
    renderProgress((text ? '✅ Got "' + text + '". ' : '⚠ Category not found. ') + 'Next product in 1.5s… (' + queue.done + '/' + queue.total + ')', true);
    await sleep(1500);
    if (loadQueue()) navigateTo(nextUrl); // still running (Stop wasn't clicked, which would have cleared it and navigated away already)
  }

  // True if the current page looks like the product the queue expects us
  // to be on. Guards against a stale/interrupted queue (e.g. the tab was
  // closed mid-run and later reopened on the list page instead) — without
  // this, we'd try to extract Category from the wrong page and silently
  // mark a real product as skipped.
  function onExpectedPage(expectedUrl) {
    if (!expectedUrl) return false;
    if (window.location.href === expectedUrl) return true;
    try { return new URL(window.location.href).pathname === new URL(expectedUrl).pathname; } catch (e) { return false; }
  }

  function renderInterrupted(queue, rows) {
    ensurePanel();
    body.innerHTML = '';
    var msg = document.createElement('div');
    msg.style.marginBottom = '8px';
    msg.textContent = 'An enrichment run looks like it was interrupted (expected to be on product #' + queue.currentId + '\'s page, ' + (queue.done || 0) + '/' + queue.total + ' done so far).';
    body.appendChild(msg);
    var expectedUrl = rows[queue.currentId] && rows[queue.currentId].productLink;
    body.appendChild(button('▶ Resume', function () {
      if (expectedUrl) navigateTo(expectedUrl);
    }, '#0ca678'));
    body.appendChild(button('Discard', function () {
      clearQueue();
      renderLauncher();
    }, '#c92a2a'));
  }

  function init() {
    var queue = loadQueue();
    if (queue && queue.currentId) {
      var rows = loadRows();
      var expectedUrl = rows[queue.currentId] && rows[queue.currentId].productLink;
      if (onExpectedPage(expectedUrl)) {
        runQueueStep(queue);
      } else {
        renderInterrupted(queue, rows);
      }
      return;
    }
    ensurePanel();
    if (showResultIfAny()) return;
    renderLauncher();
  }

  init();
})();
