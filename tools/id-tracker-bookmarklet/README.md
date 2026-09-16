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
- Remembers everything you've collected as you click "Next" and move
  through pages, and removes duplicates automatically.
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
3. Click **🎯 Setup fields** once, on the first page only. For each field
   (ID, Brand, Vendor, Category):
   - Click **🎯 Pick \<Field\> value**, then click the matching value in the
     **first row** of the list on the page itself (e.g. click "45798" for
     ID, "Party Centre" for Brand, "PARTY CENTRE LLC" for Vendor,
     "Costumes" for Category). Press `Esc` if you click the wrong thing.
   - Vendor and Category are optional — if the list doesn't show them,
     leave those two unset; you'll be asked for them once per brand
     instead at the end (see step 8).
   - Click **👁 Preview parsed rows** to confirm it read the first few rows
     correctly, then **← Back**. (This setup is remembered — you only
     need to redo it if the tool's page layout changes.)
4. Click **➕ Collect this page**. It grabs the rows from the page.
5. Click the tool's own **Next** button to go to the next page.
6. Click the **ID Tracker** bookmark again (the panel re-opens, still
   showing your running total) and click **➕ Collect this page** again.
7. Repeat steps 5–6 for every page.
8. When you're done, click **📋 Finish & copy for Sheet**:
   - If Vendor/Category were picked in step 3, they're already filled in
     from the page — nothing more to do.
   - For any brand still missing a Vendor or Category (not picked, or
     blank on some rows), it asks you once (there's a **↓ fill vendor
     down** button if several new brands share the same vendor, and it
     auto-suggests the Category if that Vendor's already been used
     before).
   - It then copies a ready-to-paste block to your clipboard.
9. Go to the Google Sheet tracker, click the top-left cell of the ID
   column, and paste (`Ctrl+V` / `Cmd+V`). ID, Brand, Vendor and Category
   land in their columns automatically; fill in "No. of Child" as usual.

Use **🏷 Vendor/Category list** any time to review or correct what it has
remembered. **🧹 Clear collected IDs** starts a fresh batch (e.g. for your
next tracking run) without forgetting the Vendor/Category memory.

If your browser blocks the auto-copy (some browsers require a recent click
before allowing clipboard writes), the panel shows the same text in a box
you can select and copy manually — nothing is lost.

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
