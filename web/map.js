/*
 * 地図: 地理院タイル（淡色地図）を自前で並べ、津波浸水想定のタイルを半透明で重ね、SVG で自宅・避難先・経路を描く。
 * 地図のライブラリは使わない。タイルは都度読み込む（リポジトリに複製しない）。
 * 出典の表示（地理院タイル、津波浸水想定: 高知県（ハザードマップポータルサイト））を、地図と一緒に返す。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const { h, svg, clear } = EC.u;

  const TILE = 256;
  const MIN_Z = 12;
  const MAX_Z = 17; // 津波浸水想定のタイルは 17 まで
  const BASE_URL = (z, x, y) => `https://cyberjapandata.gsi.go.jp/xyz/pale/${z}/${x}/${y}.png`;
  const HAZARD_URL = (z, x, y) => `https://disaportaldata.gsi.go.jp/raster/04_tsunami_newlegend_pref_data/39/${z}/${x}/${y}.png`;
  const CREDIT_URL = 'https://maps.gsi.go.jp/development/ichiran.html';

  // 緯度経度 ↔ ズームごとの地図全体のピクセル（Web メルカトル）
  const worldX = (lon, z) => ((lon + 180) / 360) * TILE * 2 ** z;
  const worldY = (lat, z) => {
    const s = Math.sin((lat * Math.PI) / 180);
    return (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * TILE * 2 ** z;
  };
  const lonOf = (x, z) => (x / (TILE * 2 ** z)) * 360 - 180;
  const latOf = (y, z) => {
    const n = Math.PI - (2 * Math.PI * y) / (TILE * 2 ** z);
    return (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
  };

  /** 地図の出典の表示。画面でも印刷でも、地図の下に付ける。 */
  function creditNode(withLegend) {
    return h('div', { class: 'map-credit' },
      withLegend ? h('p', { class: 'map-legend' }, '色がついた範囲は、津波で浸水すると想定されている所です。') : null,
      h('p', null,
        '出典: ',
        h('a', { href: CREDIT_URL, target: '_blank', rel: 'noopener noreferrer' }, '地理院タイル'),
        '、津波浸水想定: 高知県（ハザードマップポータルサイト）'));
  }

  /**
   * 地図を作る。
   * opts: { width, height, fixed, interactive, hazard, label, onTap, center, zoom, legend }
   *   fixed: true なら width・height を固定する（印刷用）。false なら、置かれた場所の幅に合わせる
   */
  function create(opts) {
    const o = Object.assign({
      width: 358, height: 320, fixed: false, interactive: false, hazard: true,
      label: '地図', onTap: null, center: { lat: 33.4508, lon: 133.4354 }, zoom: 16, legend: true,
    }, opts || {});

    let W = o.width;
    let H = o.height;
    let center = { ...o.center };
    let zoom = o.zoom;
    let shapes = [];
    let destroyed = false;

    const gTiles = svg('g', { class: 'm-tiles' });
    const gHazard = svg('g', { class: 'm-hazard', opacity: '0.55' });
    const gLines = svg('g', { class: 'm-lines' });
    const gMarks = svg('g', { class: 'm-marks' });
    const view = svg('svg', {
      class: 'gsimap-svg', role: 'img', 'aria-label': o.label,
      width: W, height: H, viewBox: `0 0 ${W} ${H}`,
    }, svg('rect', { width: '100%', height: '100%', fill: '#eef0f2' }), gTiles, gHazard, gLines, gMarks);

    const root = h('div', { class: o.fixed ? 'gsimap gsimap-fixed' : 'gsimap' }, view);
    const credit = creditNode(o.legend && o.hazard);

    // 画面の左上の地図全体のピクセル（整数に丸めて、タイルのすき間を無くす）
    let originX = 0;
    let originY = 0;
    function updateOrigin() {
      originX = Math.round(worldX(center.lon, zoom) - W / 2);
      originY = Math.round(worldY(center.lat, zoom) - H / 2);
    }

    function project(p) {
      return { x: worldX(p.lon, zoom) - originX, y: worldY(p.lat, zoom) - originY };
    }

    function unproject(x, y) {
      return { lat: latOf(y + originY, zoom), lon: lonOf(x + originX, zoom) };
    }

    function renderTiles() {
      clear(gTiles);
      clear(gHazard);
      // 画面の外にも 1 枚ぶん読み込み、ドラッグで動かしたときに空白が出にくいようにする
      const x0 = Math.floor(originX / TILE) - 1;
      const x1 = Math.floor((originX + W) / TILE) + 1;
      const y0 = Math.floor(originY / TILE) - 1;
      const y1 = Math.floor((originY + H) / TILE) + 1;
      const max = 2 ** zoom;
      for (let ty = y0; ty <= y1; ty++) {
        if (ty < 0 || ty >= max) continue;
        for (let tx = x0; tx <= x1; tx++) {
          const wx = ((tx % max) + max) % max;
          const x = tx * TILE - originX;
          const y = ty * TILE - originY;
          gTiles.appendChild(svg('image', { href: BASE_URL(zoom, wx, ty), x, y, width: TILE, height: TILE }));
          if (o.hazard) {
            const img = svg('image', { href: HAZARD_URL(zoom, wx, ty), x, y, width: TILE, height: TILE });
            img.addEventListener('error', () => img.setAttribute('visibility', 'hidden'));
            gHazard.appendChild(img);
          }
        }
      }
    }

    // --- 経路と印 ------------------------------------------------------------

    const LINE_STYLE = {
      good: { color: '#0e5a2a', width: 8, dash: null },
      stall: { color: '#b3261e', width: 8, dash: '16 9' },
      plain: { color: '#0a4a98', width: 8, dash: null },
      old: { color: '#4b5563', width: 6, dash: '3 10' },
    };

    function drawLine(s) {
      const a = project(s.from);
      const b = project(s.to);
      const st = LINE_STYLE[s.kind] || LINE_STYLE.plain;
      const d = `M${a.x.toFixed(1)} ${a.y.toFixed(1)} L${b.x.toFixed(1)} ${b.y.toFixed(1)}`;
      gLines.appendChild(svg('path', { d, fill: 'none', stroke: '#ffffff', 'stroke-width': st.width + 7, 'stroke-linecap': 'round' }));
      gLines.appendChild(svg('path', {
        d, fill: 'none', stroke: st.color, 'stroke-width': st.width, 'stroke-linecap': 'round',
        'stroke-dasharray': st.dash,
      }));
    }

    // 文字の幅の見積もり（全角は 24、半角は 13）
    const textWidth = (text) => [...String(text)].reduce((w, c) => w + (c.charCodeAt(0) > 255 ? 24 : 13), 0);

    /** ラベルを置く位置の候補 where（r・l・t・b）から、文字の位置と、占める四角を求める。 */
    function labelBox(text, p, where) {
      const width = textWidth(text);
      let lx = p.x + 26;
      let ly = p.y + 8;
      let anchor = 'start';
      if (where === 'l') { lx = p.x - 26; anchor = 'end'; }
      if (where === 't') { lx = p.x; ly = p.y - 28; anchor = 'middle'; }
      if (where === 'b') { lx = p.x; ly = p.y + 46; anchor = 'middle'; }
      const x0 = anchor === 'start' ? lx : anchor === 'end' ? lx - width : lx - width / 2;
      return { text, lx, ly, anchor, x0, x1: x0 + width, y0: ly - 22, y1: ly + 6 };
    }

    const overlaps = (a, b) => a.x0 < b.x1 + 4 && a.x1 > b.x0 - 4 && a.y0 < b.y1 + 2 && a.y1 > b.y0 - 2;
    const inside = (b) => b.x0 >= 4 && b.x1 <= W - 4 && b.y0 >= 2 && b.y1 <= H - 2;

    function drawSymbol(s, p) {
      const g = svg('g', { transform: `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})` });
      if (s.kind === 'shelter') {
        g.appendChild(svg('polygon', { points: '0,-21 20,15 -20,15', fill: '#0e5a2a', stroke: '#ffffff', 'stroke-width': 4, 'stroke-linejoin': 'round' }));
      } else if (s.kind === 'supporter') {
        g.appendChild(svg('polygon', { points: '0,-19 17,0 0,19 -17,0', fill: '#6b2c91', stroke: '#ffffff', 'stroke-width': 4, 'stroke-linejoin': 'round' }));
      } else if (s.kind === 'stop') {
        g.appendChild(svg('circle', { r: 18, fill: '#b3261e', stroke: '#ffffff', 'stroke-width': 4 }));
        g.appendChild(svg('path', { d: 'M-8 -8 L8 8 M8 -8 L-8 8', stroke: '#ffffff', 'stroke-width': 5, 'stroke-linecap': 'round' }));
      } else {
        g.appendChild(svg('circle', { r: 16, fill: '#0e2b52', stroke: '#ffffff', 'stroke-width': 4 }));
      }
      gMarks.appendChild(g);
    }

    /** ほかのラベルや印と重ならず、地図の中に収まる位置を、候補の順に探す。無ければ、最初の候補にする。 */
    function drawLabel(s, p, taken) {
      const order = [s.pos || 'r', 't', 'b', 'r', 'l'].filter((w, i, a) => a.indexOf(w) === i);
      let chosen = null;
      for (const where of order) {
        const box = labelBox(s.label, p, where);
        if (inside(box) && !taken.some((t) => overlaps(box, t))) { chosen = box; break; }
      }
      if (!chosen) {
        chosen = labelBox(s.label, p, order[0]);
        // 収まらないときは、地図の中に押し込む
        const shift = Math.max(0, 4 - chosen.x0) - Math.max(0, chosen.x1 - (W - 4));
        chosen.lx += shift; chosen.x0 += shift; chosen.x1 += shift;
      }
      taken.push(chosen);
      gMarks.appendChild(svg('text', { class: 'maplabel', x: chosen.lx.toFixed(1), y: chosen.ly.toFixed(1), 'text-anchor': chosen.anchor }, chosen.text));
    }

    function renderShapes() {
      clear(gLines);
      clear(gMarks);
      for (const s of shapes) if (s.t === 'line') drawLine(s);
      const marks = shapes.filter((s) => s.t === 'marker').map((s) => ({ s, p: project(s.at) }));
      const taken = [];
      for (const { s, p } of marks) {
        drawSymbol(s, p);
        taken.push({ x0: p.x - 24, x1: p.x + 24, y0: p.y - 26, y1: p.y + 22 });
      }
      for (const { s, p } of marks) if (s.label) drawLabel(s, p, taken);
    }

    function render() {
      if (destroyed) return;
      view.setAttribute('width', W);
      view.setAttribute('height', H);
      view.setAttribute('viewBox', `0 0 ${W} ${H}`);
      gTiles.removeAttribute('transform');
      gHazard.removeAttribute('transform');
      gLines.removeAttribute('transform');
      gMarks.removeAttribute('transform');
      updateOrigin();
      renderTiles();
      renderShapes();
    }

    // --- 表示範囲 ------------------------------------------------------------

    /** 点がすべて入るように、ズームと中心を決める。幅が変わったときは、もう一度合わせる。 */
    let lastFit = null;
    function fit(points, pad) {
      // pad: 余白のピクセル。数なら上下左右、{x, y} なら左右と上下で分ける（ラベルは左右より上下に置くことが多い）
      const margin = pad == null ? { x: 44, y: 64 } : typeof pad === 'number' ? { x: pad, y: pad } : pad;
      const pts = (points || []).filter((p) => p && EC.u.isNum(p.lat) && EC.u.isNum(p.lon));
      if (!pts.length) return;
      lastFit = { points: pts, pad };
      for (let z = MAX_Z; z >= MIN_Z; z--) {
        const xs = pts.map((p) => worldX(p.lon, z));
        const ys = pts.map((p) => worldY(p.lat, z));
        const w = Math.max(...xs) - Math.min(...xs);
        const hh = Math.max(...ys) - Math.min(...ys);
        if (w <= W - margin.x * 2 && hh <= H - margin.y * 2) {
          zoom = z;
          center = { lat: latOf((Math.max(...ys) + Math.min(...ys)) / 2, z), lon: lonOf((Math.max(...xs) + Math.min(...xs)) / 2, z) };
          render();
          return;
        }
      }
      zoom = MIN_Z;
      center = { lat: pts[0].lat, lon: pts[0].lon };
      render();
    }

    function setView(c, z) {
      lastFit = null;
      if (c) center = { ...c };
      if (z) zoom = Math.max(MIN_Z, Math.min(MAX_Z, z));
      render();
    }

    function setShapes(list) {
      shapes = list || [];
      renderShapes();
    }

    function resize() {
      if (o.fixed || destroyed) return;
      const w = Math.round(root.clientWidth);
      if (w > 0 && w !== W) {
        W = w;
        if (lastFit) fit(lastFit.points, lastFit.pad);
        else render();
      }
    }

    // --- 操作（ドラッグで動かす、タップで場所を決める、拡大・縮小） ----------------

    if (o.interactive) {
      root.classList.add('gsimap-interactive');
      let drag = null;
      view.addEventListener('pointerdown', (e) => {
        drag = { x: e.clientX, y: e.clientY, moved: false, dx: 0, dy: 0 };
        try { view.setPointerCapture(e.pointerId); } catch (err) { /* 古いブラウザでは無視 */ }
      });
      view.addEventListener('pointermove', (e) => {
        if (!drag) return;
        const rect = view.getBoundingClientRect();
        const k = rect.width ? W / rect.width : 1;
        const dx = (e.clientX - drag.x) * k;
        const dy = (e.clientY - drag.y) * k;
        if (!drag.moved && Math.hypot(dx, dy) > 8) drag.moved = true;
        if (drag.moved) {
          drag.dx = dx;
          drag.dy = dy;
          const t = `translate(${dx.toFixed(1)} ${dy.toFixed(1)})`;
          gTiles.setAttribute('transform', t);
          gHazard.setAttribute('transform', t);
          gLines.setAttribute('transform', t);
          gMarks.setAttribute('transform', t);
        }
      });
      const finish = (e) => {
        if (!drag) return;
        const d = drag;
        drag = null;
        const rect = view.getBoundingClientRect();
        const k = rect.width ? W / rect.width : 1;
        if (d.moved) {
          center = { lat: latOf(worldY(center.lat, zoom) - d.dy, zoom), lon: lonOf(worldX(center.lon, zoom) - d.dx, zoom) };
          render();
        } else if (e.type === 'pointerup' && o.onTap) {
          o.onTap(unproject((e.clientX - rect.left) * k, (e.clientY - rect.top) * k));
        }
      };
      view.addEventListener('pointerup', finish);
      view.addEventListener('pointercancel', finish);

      const zoomBox = h('div', { class: 'map-zoom' },
        h('button', { type: 'button', class: 'map-zoom-btn', 'aria-label': '地図を拡大する', onclick: () => setView(null, zoom + 1) }, '＋'),
        h('button', { type: 'button', class: 'map-zoom-btn', 'aria-label': '地図を縮小する', onclick: () => setView(null, zoom - 1) }, '－'));
      root.appendChild(zoomBox);
    }

    let observer = null;
    if (!o.fixed && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => resize());
      observer.observe(root);
    }
    render();

    const instance = {
      el: root,
      credit,
      fit,
      setView,
      setShapes,
      resize,
      project,
      unproject,
      getZoom: () => zoom,
      getCenter: () => ({ ...center }),
      destroy() {
        destroyed = true;
        registry.delete(instance);
        if (observer) observer.disconnect();
      },
    };
    registry.add(instance);
    return instance;
  }

  // 作った地図の一覧。画面が隠れているときは ResizeObserver が遅れるので、必要なときに一斉に大きさを合わせる
  const registry = new Set();
  function resizeAll() {
    registry.forEach((m) => m.resize());
  }

  EC.map = { create, creditNode, resizeAll };
})();
