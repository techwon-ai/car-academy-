// Craigslist Florida: gigs (computer, creative) for owners asking for web/marketing help, and
// jobs (marketing, web/design) for small trade businesses hiring marketers. Public pages, no login.
// Usage: node craigslist.js out.json
const { chromium } = require('playwright'); const fs = require('fs');
const SITES = ['tampa', 'orlando', 'jacksonville', 'miami', 'fortmyers', 'sarasota', 'lakeland', 'tallahassee', 'gainesville', 'daytona', 'spacecoast', 'treasure', 'ocala', 'pensacola', 'panamacity', 'staugustine', 'keys', 'lakecity', 'okaloosa'];
const CATS = { cpg: 'gigs/computer', crg: 'gigs/creative', mar: 'jobs/marketing', web: 'jobs/web-design' };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36' });
  const out = [];
  for (const s of SITES) for (const [cat, label] of Object.entries(CATS)) {
    try {
      await p.goto(`https://${s}.craigslist.org/search/${cat}#search=2~list~0`, { waitUntil: 'domcontentloaded', timeout: 40000 });
      await p.waitForSelector('.cl-search-result, li.cl-static-search-result', { timeout: 15000 }).catch(() => {});
      await sleep(2500);
      const rows = await p.$$eval('.cl-search-result, li.cl-static-search-result', els => els.map(e => {
        const a = e.querySelector('a[href*="craigslist.org"]') || e.querySelector('a');
        return { title: (e.getAttribute('title') || (e.querySelector('.label, .title, .posting-title') || {}).innerText || (a && a.innerText) || '').trim(), url: a ? a.href : '', meta: ((e.querySelector('.meta') || {}).innerText || '').trim() };
      }));
      for (const r of rows) out.push({ site: s, cat, label, ...r });
      console.log(s, cat, rows.length);
    } catch (e) { console.log('err', s, cat, String(e).slice(0, 80)); }
    await sleep(3000 + Math.random() * 3000);
  }
  fs.writeFileSync(process.argv[2], JSON.stringify(out, null, 1)); await b.close();
})();
