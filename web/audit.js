/*
 * 試験用: 文字とボタンの大きさと、文字の色のコントラストを確認する（U-01）。
 * 使い方: ?audit=1 を付けて開き、ブラウザのコンソールで
 *   await ECAudit.runAll()   主な画面を順に開いて確認する（サンプルの山田さん。サーバーが無くても動く）
 *   ECAudit.run()            いま開いている画面だけを確認する
 * 結果は、問題のある箇所の一覧（空なら問題なし）。window.__auditReport にも入る。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const MIN_FONT = 24;
  const MIN_BUTTON = 64;
  const MIN_CONTRAST = 7;
  const MAX_MAIN_BUTTONS = 2;

  const visible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    if (el.closest('[hidden], .defs, .visually-hidden')) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const cs = window.getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity) > 0;
  };

  const parse = (value) => {
    const m = /rgba?\(([^)]+)\)/.exec(value || '');
    if (!m) return null;
    const p = m[1].split(',').map((x) => parseFloat(x));
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const lum = (c) => {
    const f = (v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => { const la = lum(a); const lb = lum(b); return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05); };

  function backgroundOf(el) {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const bg = parse(window.getComputedStyle(e).backgroundColor);
      if (bg && bg.a > 0.95) return bg;
    }
    return { r: 246, g: 244, b: 238, a: 1 };
  }

  /** 画面に出ている文字の、実際の大きさ（SVG の拡大・縮小を含む）。 */
  function effectiveFont(el) {
    const size = parseFloat(window.getComputedStyle(el).fontSize);
    if (el instanceof SVGElement && el.getScreenCTM) {
      const m = el.getScreenCTM();
      if (m) return size * Math.hypot(m.a, m.b);
    }
    return size;
  }

  function run() {
    if (EC.map && EC.map.resizeAll) EC.map.resizeAll();
    const report = { screen: window.location.hash, smallText: [], smallButtons: [], mainButtons: [], lowContrast: [], overflow: [] };
    const seen = new Set();

    // 文字の大きさと色
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent.trim();
      const el = node.parentElement;
      if (!text || !el || seen.has(el) || !visible(el)) continue;
      if (el.closest('script, style, noscript')) continue;
      seen.add(el);
      const size = effectiveFont(el);
      if (size < MIN_FONT - 0.6) report.smallText.push({ text: text.slice(0, 24), px: Math.round(size * 10) / 10, tag: el.tagName.toLowerCase(), cls: el.getAttribute('class') || '' });
      if (!(el instanceof SVGElement)) {
        const fg = parse(window.getComputedStyle(el).color);
        const bg = backgroundOf(el);
        if (fg) {
          const r = ratio(fg, bg);
          if (r < MIN_CONTRAST) report.lowContrast.push({ text: text.slice(0, 24), ratio: Math.round(r * 10) / 10, cls: el.getAttribute('class') || el.tagName.toLowerCase() });
        }
      }
    }

    // ボタンの高さ（SVG の中の地図の部品は除く）
    document.querySelectorAll('button, a.btn-link, input, textarea, [role="switch"], [role="button"]').forEach((el) => {
      if (!visible(el)) return;
      const h = el.getBoundingClientRect().height;
      if (h < MIN_BUTTON - 0.6) report.smallButtons.push({ text: (el.textContent || el.getAttribute('aria-label') || el.tagName).trim().slice(0, 24), px: Math.round(h) });
    });

    // 横にはみ出す部品が無いか（画面の幅 360px でも、横にスクロールさせない）
    const limit = document.documentElement.clientWidth + 1;
    document.querySelectorAll('#main *, #actions *, #topbar *').forEach((el) => {
      if (!visible(el) || el.closest('svg') || el.closest('.print-map')) return;
      const r = el.getBoundingClientRect();
      if (r.right > limit && report.overflow.length < 5) report.overflow.push({ tag: el.tagName.toLowerCase(), cls: el.getAttribute('class') || '', right: Math.round(r.right) });
    });

    // 下の大きなボタンは 2 つまで
    const mains = [...document.querySelectorAll('#actions .btn')].filter(visible);
    report.mainButtons = mains.map((b) => b.textContent.trim());
    report.mainButtonsOk = mains.length <= MAX_MAIN_BUTTONS;
    report.ok = !report.smallText.length && !report.smallButtons.length && !report.lowContrast.length && !report.overflow.length && report.mainButtonsOk;
    return report;
  }

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  async function open(hash, ms) {
    // 同じ画面を開き直すときは、hashchange が起きないので、画面を描き直す
    if (window.location.hash === hash) EC.app.render();
    else window.location.hash = hash;
    await wait(ms || 1500);
  }

  /** 主な画面を順に開いて確認する。サンプルの山田さんを使う。 */
  async function runAll() {
    const A = EC.app;
    const results = [];
    const record = async (name, hash, ms) => {
      await open(hash, ms);
      const r = run();
      r.name = name;
      results.push(r);
    };
    const load = async (stage) => { A.openRecord(await EC.mock.samples.build('yamada', stage)); };

    await record('ホーム', '#/home');
    await record('サンプル一覧', '#/samples');
    await record('サンプル（どこから）', '#/sample?id=yamada');
    await record('新しい人（地図）', '#/new', 2500);
    await load('start');
    await record('話す（初め）', '#/talk', 1200);
    await load('talked');
    await record('話す（聞き取りのあと）', '#/talk', 1200);
    await record('点検（まだ）', '#/check', 1200);
    await load('checked');
    await record('点検（結果）', '#/check', 3500);
    await record('改善案', '#/fix?finding=fnd_s1', 3500);
    await record('確認', '#/verify', 2500);
    await load('improved');
    await record('印刷', '#/print', 3500);

    const bad = results.filter((r) => !r.ok);
    window.__auditReport = results;
    return { checked: results.length, ng: bad.length, problems: bad.map((r) => ({ name: r.name, smallText: r.smallText, smallButtons: r.smallButtons, lowContrast: r.lowContrast, overflow: r.overflow, mainButtons: r.mainButtonsOk ? undefined : r.mainButtons })) };
  }

  EC.audit = { run, runAll };
  window.ECAudit = EC.audit;
})();
