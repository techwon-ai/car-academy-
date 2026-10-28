// Download Sunbiz daily corporate (doc/cor, yyyymmddc.txt) and fictitious-name (doc/fic, yyyymmddf.txt) files.
// Credentials are the public ones printed on https://dos.fl.gov/sunbiz/other-services/data-downloads/.
// SFTP (port 22) is blocked in the cloud container and Cloudflare challenges curl, so this logs in to the
// HTTPS portal in a real browser and fetches files from inside the page.
// Usage: node sunbiz_download.js <out_dir> <first yyyymmdd>
const { chromium } = require('playwright'); const fs = require('fs');
const [OUT, FROM] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch(); const p = await (await b.newContext()).newPage();
  await p.goto('https://sftp.floridados.gov/', { waitUntil: 'networkidle' });
  await p.fill('input[name=username]', 'Public'); await p.fill('input[name=password]', 'PubAccess1845!');
  await p.keyboard.press('Enter'); await p.waitForTimeout(6000);
  const get = u => p.evaluate(async u => (await fetch(u, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })).text(), u);
  for (const [dir, suf] of [['cor', 'c'], ['fic', 'f']]) {
    let names = [];
    for (let i = 0; i < 4 && !names.length; i++) {  // the listing call is flaky right after login
      const x = await get(`/Public/doc/${dir}?request=data&_=${Date.now()}`);
      names = [...x.matchAll(/entry name="([^"]+)"/g)].map(m => m[1])
        .filter(n => /^\d{8}[cf]\.txt$/i.test(n) && n[8].toLowerCase() === suf && n >= FROM).sort();
      if (!names.length) await p.waitForTimeout(4000);
    }
    for (const n of names) {
      const f = `${OUT}/${n}`; if (fs.existsSync(f)) continue;
      // raw bytes: decoding as UTF-8 would shift the fixed-width columns on accented names
      const b64 = await p.evaluate(async u => {
        const buf = new Uint8Array(await (await fetch(u, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })).arrayBuffer());
        let s = ''; for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
        return btoa(s);
      }, `/Public/doc/${dir}/${n}`);
      const data = Buffer.from(b64, 'base64');
      if (data[0] === 0x3c) { console.log('bad response for', n); continue; }
      fs.writeFileSync(f, data); console.log('saved', n, data.length);
      await p.waitForTimeout(1500);
    }
  }
  await b.close();
})();
