/**
 * ID Tracker Collector — bookmarklet source.
 *
 * Run this on each page of the internal tool (10 IDs/page). It scans the
 * page's tables, lets you pick which columns are ID / Brand, accumulates
 * rows across pages (deduped by ID, blank-brand rows dropped), remembers
 * Vendor/Category per brand so repeat brands auto-fill, and produces a
 * tab-separated block you paste directly into Google Sheets (ID, Brand,
 * Vendor, Category — "No of Child" stays manual as today).
 *
 * See build.js for how this turns into the javascript: bookmarklet URL,
 * and README.md for install/usage instructions.
 */
(function () {
  'use strict';

  var ROWS_KEY = '__idTrackerRows_v1';
  var MAP_KEY = '__idTrackerVendorMap_v1';
  var CFG_KEY = '__idTrackerConfig_v1';
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
    rows: loadJSON(ROWS_KEY, {}),        // { id: { id, brand } }
    vendorMap: loadJSON(MAP_KEY, {}),    // { brandLower: { brand, vendor, category } }
    cfg: loadJSON(CFG_KEY, { tableIndex: 0, idCol: 1, brandCol: 2, headerRow: true })
  };

  function persist() {
    saveJSON(ROWS_KEY, state.rows);
    saveJSON(MAP_KEY, state.vendorMap);
    saveJSON(CFG_KEY, state.cfg);
  }

  function candidateTables() {
    return Array.prototype.filter.call(document.querySelectorAll('table'), function (t) {
      return t.querySelectorAll('tr').length > 1;
    });
  }

  function clearHighlights() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-id-tracker-badge]'), function (b) {
      b.remove();
    });
    Array.prototype.forEach.call(document.querySelectorAll('table'), function (t) {
      t.style.outline = '';
    });
  }

  function highlightTables(tables) {
    clearHighlights();
    tables.forEach(function (t, i) {
      t.style.outline = (i === state.cfg.tableIndex) ? '3px solid #e64980' : '2px dashed #4c6ef5';
      var badge = document.createElement('div');
      badge.setAttribute('data-id-tracker-badge', '1');
      badge.textContent = 'Table #' + i;
      badge.style.cssText = 'position:absolute;background:#e64980;color:#fff;font:11px sans-serif;' +
        'padding:2px 6px;border-radius:4px;z-index:2147483647;pointer-events:none;';
      var r = t.getBoundingClientRect();
      badge.style.left = (window.scrollX + r.left) + 'px';
      badge.style.top = (window.scrollY + r.top - 18) + 'px';
      document.body.appendChild(badge);
    });
  }

  function getRows(table, headerRow) {
    var trs = Array.prototype.slice.call(table.querySelectorAll('tr'));
    if (headerRow) trs = trs.slice(1);
    return trs.map(function (tr) {
      return Array.prototype.map.call(tr.querySelectorAll('td,th'), function (c) {
        return c.textContent.replace(/\s+/g, ' ').trim();
      });
    }).filter(function (cells) { return cells.length > 0; });
  }

  function parseCurrentTable() {
    var tables = candidateTables();
    var table = tables[state.cfg.tableIndex];
    if (!table) return { rows: [], count: 0 };
    var rows = getRows(table, state.cfg.headerRow);
    var idIdx = state.cfg.idCol - 1;
    var brandIdx = state.cfg.brandCol - 1;
    var parsed = rows.map(function (cells) {
      return { id: (cells[idIdx] || '').trim(), brand: (cells[brandIdx] || '').trim() };
    });
    return { rows: parsed, count: parsed.length };
  }

  function collectCurrentPage() {
    var parsed = parseCurrentTable().rows;
    var added = 0;
    parsed.forEach(function (r) {
      if (!r.id || !r.brand) return; // filter: drop blank brand (and blank id)
      if (!state.rows[r.id]) added++;
      state.rows[r.id] = { id: r.id, brand: r.brand };
    });
    persist();
    return added;
  }

  function brandKey(b) { return b.trim().toLowerCase(); }

  function missingBrands() {
    var seen = {};
    var out = [];
    Object.keys(state.rows).forEach(function (id) {
      var b = state.rows[id].brand;
      var k = brandKey(b);
      if (!seen[k]) {
        seen[k] = true;
        if (!state.vendorMap[k]) out.push(b);
      }
    });
    return out;
  }

  function buildTSV() {
    var lines = [];
    Object.keys(state.rows).forEach(function (id) {
      var r = state.rows[id];
      var v = state.vendorMap[brandKey(r.brand)] || { vendor: '', category: '' };
      lines.push([r.id, r.brand, v.vendor || '', v.category || ''].join('\t'));
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
    var closeBtn = button('✕', function () { panel.remove(); clearHighlights(); }, 'transparent');
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

  function numberInput(value, onChange, width) {
    var i = document.createElement('input');
    i.type = 'number';
    i.value = value;
    i.style.cssText = 'width:' + (width || '50px') + ';margin:0 6px;padding:3px;border-radius:4px;border:1px solid #444;';
    i.oninput = function () { onChange(parseInt(i.value, 10) || 1); };
    return i;
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
    body.innerHTML = '';
    var count = Object.keys(state.rows).length;
    var summary = document.createElement('div');
    summary.style.marginBottom = '8px';
    summary.innerHTML = 'Collected so far: <strong>' + count + '</strong> unique ID(s)';
    body.appendChild(summary);

    body.appendChild(button('➕ Collect this page', function () {
      var added = collectCurrentPage();
      renderMain();
      toast(added + ' new row(s) added (blank-brand rows skipped).');
    }));

    body.appendChild(button('⚙ Setup columns', renderSetup, '#495057'));
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

  function renderSetup() {
    body.innerHTML = '';
    var tables = candidateTables();
    highlightTables(tables);

    var info = document.createElement('div');
    info.textContent = tables.length ? ('Found ' + tables.length + ' table(s). Pink outline = selected.') : 'No tables with rows found on this page.';
    info.style.marginBottom = '6px';
    body.appendChild(info);

    var row1 = document.createElement('div');
    row1.textContent = 'Table #:';
    var tIdx = numberInput(state.cfg.tableIndex, function (v) {
      state.cfg.tableIndex = Math.max(0, v);
      highlightTables(candidateTables());
      persist();
    });
    row1.appendChild(tIdx);
    body.appendChild(row1);

    var row2 = document.createElement('div');
    row2.textContent = 'ID column #:';
    var idc = numberInput(state.cfg.idCol, function (v) { state.cfg.idCol = Math.max(1, v); persist(); });
    row2.appendChild(idc);
    body.appendChild(row2);

    var row3 = document.createElement('div');
    row3.textContent = 'Brand column #:';
    var bc = numberInput(state.cfg.brandCol, function (v) { state.cfg.brandCol = Math.max(1, v); persist(); });
    row3.appendChild(bc);
    body.appendChild(row3);

    var row4 = document.createElement('label');
    row4.style.display = 'block';
    row4.style.marginTop = '4px';
    var hcb = document.createElement('input');
    hcb.type = 'checkbox';
    hcb.checked = state.cfg.headerRow;
    hcb.onchange = function () { state.cfg.headerRow = hcb.checked; persist(); };
    row4.appendChild(hcb);
    row4.appendChild(document.createTextNode(' First row is a header (skip it)'));
    body.appendChild(row4);

    body.appendChild(button('👁 Preview parsed rows', function () {
      var parsed = parseCurrentTable().rows.slice(0, 5);
      alert(parsed.length
        ? 'First rows parsed as:\n\n' + parsed.map(function (r) { return 'ID=' + r.id + '  |  Brand=' + r.brand; }).join('\n')
        : 'No rows parsed — check table #/column #.');
    }, '#495057'));
    body.appendChild(document.createElement('br'));

    body.appendChild(button('← Back', function () { clearHighlights(); renderMain(); }, '#495057'));
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
    var lastVendor = '';
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
        lastVendor = vIn.value;
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
      'border-radius:6px;font:12px sans-serif;z-index:2147483647;';
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2200);
  }

  function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  ensurePanel();
  renderMain();
})();
