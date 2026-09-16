#!/usr/bin/env node
/**
 * Builds bookmarklet.txt (a javascript: URL) from collector.js.
 * Run: node build.js
 */
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, 'collector.js'), 'utf8');
const uri = 'javascript:' + encodeURIComponent(src);

fs.writeFileSync(path.join(__dirname, 'bookmarklet.txt'), uri);
console.log('Wrote bookmarklet.txt (' + uri.length + ' chars).');
