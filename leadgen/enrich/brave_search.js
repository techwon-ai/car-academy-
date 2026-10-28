// Brave Search through a real headless browser (Google shows a CAPTCHA page, Bing serves junk results,
// and DDG returns 418 from this environment). Human pace, resumable JSONL.
// Usage: node brave_search.js queries.json out.jsonl     queries.json = [{key, q}]
const { chromium } = require('playwright'); const fs = require('fs');
const [inp, out] = process.argv.slice(2);
const done = new Set(fs.existsSync(out) ? fs.readFileSync(out, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l).key) : []);
const qs = JSON.parse(fs.readFileSync(inp, 'utf8')).filter(x => !done.has(x.key));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const GAP = +(process.env.BRAVE_GAP_MS || 6000); // base delay between queries
(async () => {
  const b = await chromium.launch();
  const c = await b.newContext({ locale: 'en-US', userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36' });
  const p = await c.newPage(); let fails = 0;
  for (const x of qs) {
    let rec = { key: x.key, q: x.q, results: [] };
    try {
      await p.goto('https://search.brave.com/search?q=' + encodeURIComponent(x.q) + '&source=web', { waitUntil: 'domcontentloaded', timeout: 40000 });
      await sleep(3500);
      rec.results = await p.$$eval('.snippet[data-type="web"]', els => els.map(e => {
        const a = [...e.querySelectorAll('a[href^="http"]')].find(x => !/brave\.com/.test(x.href));
        return { url: a ? a.href : '', text: e.innerText.replace(/\s+/g, ' ').slice(0, 400) };
      }).filter(r => r.url));
      const body = await p.innerText('body');
      if (/captcha|verify you are human|rate limit/i.test(body) && !rec.results.length) { rec.blocked = true; }
    } catch (e) { rec.error = String(e).slice(0, 120); }
    if (!rec.blocked) fs.appendFileSync(out, JSON.stringify(rec) + '\n'); // blocked rows are retried next run
    if (rec.blocked) { if (++fails >= 3) { console.log('BLOCKED - stopping'); break; } await sleep(15 * 60000); } else fails = 0;
    await sleep(GAP + Math.random() * GAP);
  }
  await b.close();
})();
