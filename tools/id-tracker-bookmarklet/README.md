# ID Tracker Bookmarklet

Automates the manual "copy 10 IDs per page → paste in Excel → filter blank
brands → paste into the tracker → look up Vendor/Category by hand" workflow.

It's a **bookmarklet**: a bit of JavaScript that runs inside your browser,
on the internal tool's page, using your existing logged-in session. Nothing
is sent anywhere else — everything is stored locally in your browser.

What it does:
- Reads ID, Brand, Vendor and Category straight off the page (no more
  selecting and copying by hand) — works whether the list is a real HTML
  table or a div/card-based grid.
- Can optionally also read "No. of SKUs" (e.g. the `3` in "Pending
  Variants (3)") if you choose to pick that field too — otherwise that
  column stays manual, same as today.
- Remembers everything you've collected as you move through pages, and
  removes duplicates automatically.
- Can optionally click through every page for you (see **Auto-collect**
  below) if you also pick the "Next" button once — no manual clicking
  through pages required.
- Drops rows with a blank Brand (same as your current Excel filter step).
- If a page doesn't show Vendor/Category (or you skip setting those up),
  it falls back to a remembered Vendor + Category per brand: the first
  time it sees a brand with no Vendor/Category it asks you once, and
  every time after that (even days later) it fills them in automatically.
- Gives you one block of text — ID, Brand, Vendor, Category — ready to
  paste straight into Google Sheets. "No. of Child" stays manual, same as
  today.

## 1. Install the bookmarklet (one-time setup)

1. Open [`bookmarklet.txt`](./bookmarklet.txt) in this folder and copy its
   entire contents (it's one long line starting with `javascript:`).
2. In your browser, show the bookmarks bar if it's hidden:
   - Chrome/Edge: `Ctrl+Shift+B` (Windows) or `Cmd+Shift+B` (Mac)
3. Right-click the bookmarks bar → **Add page** (Chrome) / **Add bookmark**
   (Edge/Firefox).
4. For the **name**, type: `ID Tracker`
5. For the **URL/location**, paste the text you copied in step 1.
6. Save.

You now have an "ID Tracker" button on your bookmarks bar. You only need to
do this once — it keeps working after this.

## 2. Using it on the internal tool

1. Log in and open page 1 of the tool's list.
2. Click the **ID Tracker** bookmark. A small panel appears in the top
   right of the page.
3. Click **🎯 Setup fields** once, on the first page only:
   - Click **🎯 Pick ID value**, then click the ID number in the **first
     row** of the list (e.g. "45798"). Since this is the very first field
     you've picked, it'll ask you to pick one more — click **🎯 Pick Brand
     value** and click "Party Centre" on that **same row**. From those two
     clicks it works out the repeating "row" pattern on the page, so every
     field after this only needs a single click each.
   - Pick Vendor ("PARTY CENTRE LLC"), Category ("Costumes"), and No. of
     SKUs (the "Pending Variants (3)" badge — it automatically pulls out
     just the `3`) the same way, one click each, still on that first row.
     These three are optional — if the list doesn't show a field, or
     you'd rather keep filling it in by hand, leave it unset. Vendor/
     Category then fall back to asking you once per brand at the end (see
     step 5); No. of SKUs simply stays out of the pasted block if left
     unset. Press `Esc` any time to cancel a pick.
   - There's also a **🎯 Pick Next button** option at the bottom (optional)
     — see **Auto-collect all pages** below for what it unlocks.
   - Click **👁 Preview parsed rows** to confirm it read the first few rows
     correctly, then **← Back**. (This setup is remembered — you only
     need to redo it if the tool's page layout changes.)
4. Either:
   - **Manually, page by page:** click **➕ Collect this page**, then click
     the tool's own **Next** button, then click the **ID Tracker** bookmark
     again (the panel re-opens, still showing your running total) and
     click **➕ Collect this page** again. Repeat for every page.
   - **Or automatically, if you picked a Next button in step 3:** click
     **▶ Auto-collect all pages** once. It collects the current page,
     clicks Next, waits for the new rows to load, collects again, and
     keeps going until it reaches the last page (or you click **⏹ Stop**).
     See **Auto-collect all pages** below for when this works.
5. When you're done, click **📋 Finish & copy for Sheet**:
   - If Vendor/Category were picked in step 3, they're already filled in
     from the page — nothing more to do.
   - For any brand still missing a Vendor or Category (not picked, or
     blank on some rows), it asks you once (there's a **↓ fill vendor
     down** button if several new brands share the same vendor, and it
     auto-suggests the Category if that Vendor's already been used
     before).
   - It then copies a ready-to-paste block to your clipboard, with a
     header row (ID, Brand, Vendor, Category…) on top by default —
     uncheck **"Include a header row"** on the main panel first if you're
     pasting below existing rows already in the tracker rather than
     starting a fresh sheet.
6. Go to the Google Sheet tracker, click the top-left cell of the ID
   column (or the row where you want the new data to start), and paste
   (`Ctrl+V` / `Cmd+V`). ID, Brand, Vendor and Category land in their
   columns automatically (plus No. of SKUs, if you picked that field);
   fill in "No. of Child" by hand if you didn't.

Use **🏷 Vendor/Category list** any time to review or correct what it has
remembered. **🧹 Clear collected IDs** starts a fresh batch (e.g. for your
next tracking run) without forgetting the Vendor/Category memory.

If your browser blocks the auto-copy (some browsers require a recent click
before allowing clipboard writes), the panel shows the same text in a box
you can select and copy manually — nothing is lost.

## Auto-collect all pages

If you pick a "Next" button in **🎯 Setup fields**, a green
**▶ Auto-collect all pages** button appears on the main panel. Click it
once and it will, on its own: collect the current page, click Next, wait
for the new rows to appear, collect again, and repeat until either the
Next button becomes disabled/missing (normal end-of-list signal) or the
page stops changing after clicking Next twice in a row (it waits up to 10
seconds each time, and retries once before giving up). Click **⏹ Stop**
any time to break out early — nothing collected so far is lost.

The running total and any "skipped for blank Brand" count are shown live
as it works, and again in the final summary — so if you end up with fewer
rows than expected, check that number first: it may be correctly filtering
blank-brand rows rather than missing real ones.

This only works if clicking Next loads the next set of rows **without a
full page reload** (i.e. the page updates itself, like the QC countdown
timers on this tool do) — that's the case for most modern web apps. If the
tool instead does a full page reload/navigation when you click Next, the
script itself gets wiped out mid-navigation (a browser thing, unrelated to
this tool), Auto-collect will only grab the first page, and you'll see it
stop early. In that case just use the manual page-by-page flow in step 4 —
whatever it already collected is saved and nothing is lost either way.

**If it stops with "the page didn't change after clicking Next":** go back
to **🎯 Setup fields** and click **🧪 Test Next click** next to the Next
button controls. It fires one click, waits up to 10 seconds, and shows the
actual ID values it read before/after so you can see exactly what
happened. If the values look identical both times, re-check the "Row
pattern" status in Setup — see below. If that all looks right, the click
may not be reaching the tool's real "Next" handler — try **🎯 Pick Next
button** again on a slightly different spot (e.g. the arrow icon itself
vs. its outer button/link wrapper).

### How row detection works (and how to fix it if it's wrong)

Rather than finding "the ID field" and "the Brand field" independently
(which breaks if a field has no distinguishing style of its own, or if two
different fields happen to share the same style), this bookmarklet first
figures out the repeating **row** — the element that repeats once per
product — from your first two picks (ID + Brand), then remembers each
field's *position within that row*. This is why the first field you pick
always asks for a second one: it needs two points on the same row to work
out the pattern. Every field after that, including on a later visit,
resolves in one click because the row pattern is already known.

The Setup screen shows a **"Row pattern"** status at the top: "✅ found N
row(s)" once established. If it ever shows **0 rows** even though a
pattern is set, a **Row skip count** box appears with the raw total (e.g.
"skipping 5 of 2 total matches") — this happens if the row selector
matched something transient at pick time; try lowering it to 0. If the raw
total is also 0, the row selector itself isn't matching anything right now
— use **🔄 Reset row pattern** (appears once a pattern exists) and pick ID
and Brand again.

Each field's own status line shows "N of M row(s) have a value" — if a
field is consistently missing on some rows (not just occasionally, e.g.
for out-of-stock items), it likely needs re-picking.

## Notes / limitations

- This only works on the internal tool's own pages (it's a script that
  runs on that page), and only while you're logged in in that browser tab.
- Everything is stored in that browser's local storage for that site. If
  you switch browsers/computers, or clear site data, you'll need to
  re-teach it the Vendor/Category list (or someone can export/import
  `localStorage` values if that becomes worth automating later).
- If the tool changes its page layout, redo the **🎯 Setup fields** step.

## Files

- `collector.js` — the readable source code.
- `build.js` — regenerates `bookmarklet.txt` from `collector.js`
  (`node build.js`). Only needed if you edit `collector.js`.
- `bookmarklet.txt` — the generated `javascript:` URL to paste as your
  bookmark's URL.
