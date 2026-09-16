/**
 * ID Tracker Collector — bookmarklet source.
 *
 * Run this on each page of the internal tool (10 IDs/page). You click once
 * on an example ID, Brand, Vendor and Category value on the page, and it
 * works out how to find every other row's values automatically (works for
 * real <table> markup as well as div/card-based grids). It accumulates
 * rows across pages (deduped by ID, blank-brand rows dropped), remembers
 * Vendor/Category per brand as a fallback for rows where those fields
 * weren't picked or came back blank, and produces a tab-separated block
 * you paste directly into Google Sheets (ID, Brand, Vendor, Category —
 * "No of Child" stays manual as today).
 *
 * See build.js for how this turns into the javascript: bookmarklet URL,
 * and README.md for install/usage instructions.
 */
(function () {
  'use strict';

  var ROWS_KEY = '__idTrackerRows_v1';
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
    cfg: loadJSON(CFG_KEY, { idSelector: '', brandSelector: '', vendorSelector: '', categorySelector: '' })
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

  function pickBestSelector(el) {
    var candidates = selectorCandidates(el);
    for (var i = 0; i < candidates.length; i++) {
      var sel = candidates[i];
      try {
        var matches = document.querySelectorAll(sel);
        if (matches.length >= 2 && Array.prototype.indexOf.call(matches, el) !== -1) {
          return sel;
        }
      } catch (e) { /* invalid selector, skip */ }
    }
    return nthChildPath(el);
  }

  function textOf(el) {
    return el.textContent.replace(/\s+/g, ' ').trim();
  }

  // ---------- data extraction ----------

  var FIELDS = ['id', 'brand', 'vendor', 'category'];

  function selectorsConfigured() {
    return !!(state.cfg.idSelector && state.cfg.brandSelector);
  }

  function fieldEls(field) {
    var sel = state.cfg[field + 'Selector'];
    if (!sel) return [];
    try { return Array.prototype.slice.call(document.querySelectorAll(sel)); } catch (e) { return []; }
  }

  function parseCurrentPage() {
    if (!selectorsConfigured()) return { rows: [], counts: {}, configured: false };
    var elsByField = {};
    FIELDS.forEach(function (f) { elsByField[f] = fieldEls(f); });
    // Row count is driven by ID/Brand (the required fields); Vendor/Category are optional extras.
    var n = Math.min(elsByField.id.length, elsByField.brand.length);
    var rows = [];
    for (var i = 0; i < n; i++) {
      var row = {};
      FIELDS.forEach(function (f) {
        row[f] = elsByField[f][i] ? textOf(elsByField[f][i]) : '';
      });
      rows.push(row);
    }
    var counts = {};
    FIELDS.forEach(function (f) { counts[f] = elsByField[f].length; });
    return { rows: rows, counts: counts, configured: true };
  }

  function collectCurrentPage() {
    var parsed = parseCurrentPage();
    var added = 0, skippedBlank = 0;
    parsed.rows.forEach(function (r) {
      if (!r.id || !r.brand) { skippedBlank++; return; }
      if (!state.rows[r.id]) added++;
      state.rows[r.id] = { id: r.id, brand: r.brand, vendor: r.vendor, category: r.category };
      // Directly-scraped Vendor/Category are authoritative — keep the brand memory fresh from them.
      if (r.vendor || r.category) {
        state.vendorMap[brandKey(r.brand)] = { brand: r.brand, vendor: r.vendor, category: r.category };
      }
    });
    persist();
    return { added: added, skippedBlank: skippedBlank, counts: parsed.counts, configured: parsed.configured };
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
    var lines = [];
    Object.keys(state.rows).forEach(function (id) {
      var r = state.rows[id];
      var eff = effectiveVendorCategory(r);
      lines.push([r.id, r.brand, eff.vendor, eff.category].join('\t'));
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
    hint.textContent = 'Go to the next page in the tool, then click this bookmarklet again and hit "Collect this page".';
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

  var FIELD_LABELS = { id: 'ID', brand: 'Brand', vendor: 'Vendor', category: 'Category' };
  var OPTIONAL_FIELDS = { vendor: true, category: true };

  function renderSetup() {
    stopPicking();
    body.innerHTML = '';

    var info = document.createElement('div');
    info.style.marginBottom = '8px';
    info.innerHTML = 'Click a button below, then click the matching value <u>on the page</u> (in the first row). Press Esc to cancel. ID and Brand are required; Vendor and Category are optional — leave unset if this page doesn\'t show them, and you\'ll be asked for them once per brand instead.';
    body.appendChild(info);

    FIELDS.forEach(function (field) {
      var selKey = field + 'Selector';
      var status = document.createElement('div');
      status.style.cssText = 'margin-top:10px;margin-bottom:4px;';
      var count = state.cfg[selKey] ? (function () { try { return document.querySelectorAll(state.cfg[selKey]).length; } catch (e) { return 0; } })() : 0;
      var label = FIELD_LABELS[field] + (OPTIONAL_FIELDS[field] ? ' (optional)' : '');
      status.textContent = label + ': ' + (state.cfg[selKey] ? ('✅ set (' + count + ' found on this page)') : '❌ not set');
      body.appendChild(status);

      var row = document.createElement('div');
      row.appendChild(button('🎯 Pick ' + FIELD_LABELS[field] + ' value', function () {
        body.innerHTML = '';
        var msg = document.createElement('div');
        msg.textContent = 'Now click the ' + FIELD_LABELS[field] + ' value in the FIRST row of the list (Esc to cancel)...';
        body.appendChild(msg);
        startPicking(field, function (el) {
          state.cfg[selKey] = pickBestSelector(el);
          persist();
          renderSetup();
        });
      }));
      if (state.cfg[selKey]) {
        row.appendChild(button('Clear', function () {
          state.cfg[selKey] = '';
          persist();
          renderSetup();
        }, '#495057'));
      }
      body.appendChild(row);
    });

    body.appendChild(document.createElement('br'));
    body.appendChild(button('👁 Preview parsed rows', function () {
      var parsed = parseCurrentPage();
      if (!parsed.configured) { alert('Set up ID and Brand fields first.'); return; }
      var lines = parsed.rows.slice(0, 6).map(function (r) {
        return 'ID=' + r.id + ' | Brand=' + r.brand + ' | Vendor=' + (r.vendor || '(none)') + ' | Category=' + (r.category || '(none)');
      });
      var mismatch = FIELDS.filter(function (f) { return state.cfg[f + 'Selector'] && parsed.counts[f] !== parsed.counts.id; });
      var extra = mismatch.length
        ? '\n\n⚠ ' + mismatch.map(function (f) { return FIELD_LABELS[f] + '=' + parsed.counts[f]; }).join(', ') + ' vs ID=' + parsed.counts.id + ' — some fields may not line up. Try re-picking.'
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
      msg.textContent = ok
        ? '✅ Copied to clipboard — paste into Google Sheets (columns: ID, Brand, Vendor, Category).'
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
