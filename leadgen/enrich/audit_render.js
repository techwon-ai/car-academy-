// Rendered mobile audit (real Chromium, iPhone-sized viewport). Used for every site the quick HTML
// audit flagged, because JS-built sites (Wix, React) look empty to a plain fetch and would otherwise be
// misreported as parked or as having no call button.
// Usage: node audit_render.js urls.txt out.jsonl     (resumable)
const { chromium, devices } = require('playwright'); const fs = require('fs');
const [inp, out] = process.argv.slice(2);
const done = new Set(fs.existsSync(out) ? fs.readFileSync(out, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l).url) : []);
const urls = fs.readFileSync(inp, 'utf8').split('\n').map(s => s.trim()).filter(u => u && !done.has(u));
const PARKED = /domain (is|may be) for sale|buy this domain|this domain (name )?(is|has been) (registered|parked)|parked (free|domain)|sedoparking|hugedomains|afternic|is under construction|coming soon|launching soon|future home of|site not found|account (has been )?suspended|default web site page|welcome to nginx|index of \/|this site can.t be reached|website expired|renew (your|this) (domain|site)/i;

async function auditOne(ctx, url) {
  const p = await ctx.newPage(); const res = { url, rendered: true };
  try {
    const t0 = Date.now();
    const resp = await p.goto(url.startsWith('http') ? url : 'http://' + url, { waitUntil: 'load', timeout: 30000 });
    res.load_s = +((Date.now() - t0) / 1000).toFixed(1);
    res.status = resp ? resp.status() : 0;
    await p.waitForTimeout(1500);
    Object.assign(res, await p.evaluate(() => {
      const vis = document.body ? document.body.innerText : '';
      const vh = window.innerHeight;
      const tels = [...document.querySelectorAll('a[href^="tel:"]')];
      const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
      const html = document.documentElement.outerHTML;
      const years = [...vis.matchAll(/(?:©|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})/gi)].map(m => +m[1]);
      const foot = vis.slice(-1500);
      const ag = foot.match(/(?:web\s*site|web design|designed|developed|built|created|marketing|seo|powered)\s*by\s*:?\s*([A-Z][\w&.' -]{2,40})/i);
      return {
        final_url: location.href, https: location.protocol === 'https:',
        viewport: !!document.querySelector('meta[name="viewport"]'),
        overflow_px: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
        tel_link: tels.some(visible), tel_above_fold: tels.some(t => visible(t) && t.getBoundingClientRect().top < vh),
        cta_above_fold: [...document.querySelectorAll('a,button')].some(e => visible(e) && e.getBoundingClientRect().top < vh && /call|quote|estimate|book|schedule|contact|get started/i.test(e.innerText || '')),
        form: [...document.querySelectorAll('form')].some(f => f.querySelectorAll('input:not([type=hidden]),textarea').length >= 2) || !!document.querySelector('iframe[src*="form"], iframe[src*="jotform"], iframe[src*="housecall"], iframe[src*="jobber"]'),
        booking: /housecallpro|getjobber|servicetitan|calendly|acuityscheduling|fieldedge|book online|book now|schedule (service|online)/i.test(html),
        words: vis.split(/\s+/).filter(Boolean).length, text_head: vis.slice(0, 300).replace(/\s+/g, ' '),
        copyright_year: years.length ? Math.max(...years) : null, agency_credit: ag ? ag[1].trim() : '',
        meta_pixel: typeof window.fbq === 'function', google_ads_tag: /AW-\d{6,}/.test(html),
        emails: [...new Set([...html.matchAll(/mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})/g)].map(m => m[1].toLowerCase()))].slice(0, 3),
        facebook: (html.match(/https?:\/\/(?:www\.|m\.)?facebook\.com\/(?!sharer|share|plugins|tr\b|dialog)[A-Za-z0-9_.\-]+/) || [''])[0],
        instagram: (html.match(/https?:\/\/(?:www\.)?instagram\.com\/(?!p\/|explore)[A-Za-z0-9_.]+/) || [''])[0],
        linkedin: (html.match(/https?:\/\/(?:www\.)?linkedin\.com\/(?:company|in)\/[A-Za-z0-9_\-]+/) || [''])[0],
        title: document.title.slice(0, 120),
      };
    }));
    res.bot_wall = res.status === 202 || /sgcaptcha|captcha|challenge/i.test(res.final_url) || /just a moment|verify you are human|attention required|access denied/i.test(res.title + ' ' + res.text_head);
    res.parked = !res.bot_wall && (PARKED.test(res.text_head) || res.words < 25);
    res.loads = res.status < 400;
  } catch (e) { res.loads = false; res.error = String(e).split('\n')[0].slice(0, 140); }
  await p.close();
  return res;
}

(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ ...devices['iPhone 13'] });
  const queue = [...urls]; let n = 0;
  async function worker() {
    while (queue.length) {
      const u = queue.shift();
      // hard cap per site: some pages never settle and would stall the worker
      const r = await Promise.race([auditOne(ctx, u), new Promise(ok => setTimeout(() => ok({ url: u, rendered: true, loads: false, error: 'hard timeout 75s' }), 75000))]);
      fs.appendFileSync(out, JSON.stringify(r) + '\n');
      if (++n % 50 === 0) console.log(new Date().toISOString(), n, '/', urls.length);
    }
  }
  await Promise.all(Array.from({ length: +(process.env.RENDER_WORKERS || 4) }, worker));
  await b.close();
})();
