/*
 * タイムライン（F-17）: 「避難を始めるまで｜歩く｜上がる｜余裕時間（または不足）」を横の帯で表示し、
 * 津波到達時間の位置に縦の線を引く。不足する時間は線の右にはみ出して、赤い斜線で示す。
 * 色だけで伝えないため、区間ごとに模様を変え、下に文字の一覧（区間の名前と分）を付ける。
 * 分の値は ScenarioResult（コードが算出）のものだけを使う。ここでは長さを描くだけで、計算はしない。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const { h, svg, isNum, ceilMin, floorMin, verdictView } = EC.u;
  const L = EC.L;

  const FILL = {
    leave: 'url(#ec-pat-leave)',
    walk: 'url(#ec-pat-walk)',
    climb: 'url(#ec-pat-climb)',
    help: 'url(#ec-pat-help)',
    supporter_travel: 'url(#ec-pat-travel)',
  };

  // 横の幅は 280 とする。スマートフォンでは 1 倍以上に拡大されるので、文字の大きさ 24 が画面で 24px 以上になる
  const W = 280;
  const PAD = 6;
  const BAR_Y = 44;
  const BAR_H = 44;

  function segLabel(seg) {
    const label = seg.label || L.BREAKDOWN_LABELS[seg.key] || seg.key;
    // 凡例が長くならないよう、移動の「直線距離 × 1.3 の概算」は、下の注に回す
    return label.replace(/直線距離\s*×\s*1\.3\s*の概算、?/, '').replace('、）', '）');
  }

  // 判定の印（丸に記号）。画面の判定の札と同じ形・同じ色。色は CSS の変数と同じ値（README の色の一覧）
  const DOT = { ok: ['#1b7d46', '#ffffff'], tight: ['#f4b400', '#2e2100'], short: ['#c62a1f', '#ffffff'], unknown: ['#5d667b', '#ffffff'] };
  const GLYPH = {
    ok: 'M-5.5 0.5L-1.8 4.2L5.8 -4',
    short: 'M-4.6 -4.6L4.6 4.6M4.6 -4.6L-4.6 4.6',
    tight: 'M0 -6.8V1.2',
    unknown: 'M-3.6 -3.2A3.6 3.6 0 1 1 1.6 0.2C0.4 0.9 0 1.5 0 2.6',
  };

  function verdictDot(key, cx, cy) {
    const [fill, ink] = DOT[key] || DOT.unknown;
    const g = svg('g', { transform: `translate(${cx.toFixed(1)} ${cy})`, 'aria-hidden': 'true' });
    g.appendChild(svg('circle', { r: 13, fill }));
    g.appendChild(svg('path', { d: GLYPH[key] || GLYPH.unknown, fill: 'none', stroke: ink, 'stroke-width': 3, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    if (key === 'tight' || key === 'unknown') g.appendChild(svg('circle', { cy: 6.4, r: 1.8, fill: ink }));
    return g;
  }

  /** 文字の幅の見積もり（SVG の文字の位置を決めるだけに使う）。半角は 0.55 倍、全角は 1 倍。 */
  const textWidth = (str, size) => [...String(str)].reduce((sum, c) => sum + (c.charCodeAt(0) < 256 ? 0.55 : 1) * size, 0);

  /** 一覧の記号（小さな四角）。タイムラインと同じ模様を使う。 */
  function swatch(fill) {
    return svg('svg', { class: 'tl-swatch', width: 36, height: 28, viewBox: '0 0 36 28', 'aria-hidden': 'true', focusable: 'false' },
      svg('rect', { x: 1.5, y: 1.5, width: 33, height: 25, rx: 4, fill, stroke: '#0d1b36', 'stroke-width': 2 }));
  }

  /**
   * result: ScenarioResult
   * opts: { scale: 軸の最大の分（2 つを並べて比べるときに同じ値を渡す）, title: 見出し }
   */
  function render(result, opts) {
    const o = Object.assign({ scale: null, title: null, footnote: true }, opts || {});
    const segs = ((result && result.breakdown) || []).filter((s) => isNum(s.minutes) && s.minutes > 0);
    const avail = result && isNum(result.avail_min) ? result.avail_min : null;
    const drawn = segs.reduce((sum, s) => sum + s.minutes, 0); // 描画の長さにだけ使う
    const need = result && isNum(result.need_min) ? result.need_min : drawn;
    const view = verdictView(result);

    const maxMin = (o.scale || Math.max(drawn, need, avail || 0)) * 1.04 || 1;
    const k = (W - PAD * 2) / maxMin;
    const xOf = (m) => PAD + m * k;

    const g = svg('svg', {
      class: 'tl-svg', viewBox: `0 0 ${W} 150`, role: 'img', focusable: 'false',
      'aria-label': `避難所要時間 ${ceilMin(need) ?? '不明'}分。津波到達時間 ${avail === null ? '不明' : floorMin(avail) + '分'}。${view.text}`,
    });

    // 区間の帯。すべて実際の長さで描く（津波の線をまたいでも隠さない）。数字のラベルは、あとで最前面に描く
    const labels = [];
    let acc = 0;
    segs.forEach((seg) => {
      const x = xOf(acc);
      const w = seg.minutes * k;
      g.appendChild(svg('rect', { x: x.toFixed(1), y: BAR_Y, width: w.toFixed(1), height: BAR_H, fill: FILL[seg.key] || FILL.walk, stroke: '#0d1b36', 'stroke-width': 2 }));
      labels.push({ x, w, text: `${Math.round(seg.minutes)}` });
      acc += seg.minutes;
    });

    // 余裕（緑・黄）か不足（赤い枠と、下の赤い斜線の帯）
    if (avail !== null) {
      const xEnd = xOf(drawn);
      const xLine = xOf(avail);
      let lineBottom = BAR_Y + BAR_H + 10;
      if (drawn > avail) {
        // 津波が来たあとの区間: 赤い太い枠で囲み、下に赤い斜線の帯を付ける（区間の模様は隠さない）
        g.appendChild(svg('rect', { x: xLine.toFixed(1), y: BAR_Y - 3, width: (xEnd - xLine).toFixed(1), height: BAR_H + 6, fill: 'none', stroke: '#a31d14', 'stroke-width': 4 }));
        g.appendChild(svg('rect', { x: xLine.toFixed(1), y: BAR_Y + BAR_H + 9, width: (xEnd - xLine).toFixed(1), height: 14, fill: 'url(#ec-pat-short)', stroke: '#a31d14', 'stroke-width': 2 }));
        lineBottom = BAR_Y + BAR_H + 26;
      } else {
        const gap = xLine - xEnd;
        if (gap > 0.5) {
          g.appendChild(svg('rect', { x: xEnd.toFixed(1), y: BAR_Y, width: gap.toFixed(1), height: BAR_H, fill: view.key === 'tight' ? 'url(#ec-pat-tight)' : 'url(#ec-pat-margin)', stroke: view.key === 'tight' ? '#8a6400' : '#1b7d46', 'stroke-width': 3 }));
        }
      }

      // 津波到達時間の縦の線と名前
      const labelX = Math.min(Math.max(xLine, PAD + 76), W - PAD - 76);
      g.appendChild(svg('line', { x1: xLine.toFixed(1), x2: xLine.toFixed(1), y1: 34, y2: lineBottom, stroke: '#0d1b36', 'stroke-width': 4 }));
      g.appendChild(svg('text', { class: 'tl-line-label', x: labelX.toFixed(1), y: 26, 'text-anchor': 'middle' }, `津波 ${floorMin(avail)}分`));

      // 余裕・不足の文字。前に、判定の札と同じ印（丸に記号）を付ける
      const noteText = view.detail || view.label;
      const noteX = view.key === 'short' ? W - PAD : Math.min(Math.max(xLine, 170), W - PAD);
      const note = svg('text', { class: `tl-note tl-note-${view.key}`, x: noteX.toFixed(1), y: 140, 'text-anchor': 'end' });
      note.textContent = noteText;
      g.appendChild(verdictDot(view.key, noteX - textWidth(noteText, 26) - 22, 131));
      g.appendChild(note);
    }

    // 区間の分の数字。模様の上でも読めるよう、白い札の上に出す。幅が足りないときは出さない
    labels.forEach((l) => {
      const pw = l.text.length * 15 + 14;
      if (l.w < pw + 2) return;
      const cx = l.x + l.w / 2;
      g.appendChild(svg('rect', { x: (cx - pw / 2).toFixed(1), y: BAR_Y + 7, width: pw.toFixed(1), height: 30, rx: 8, fill: '#ffffff', stroke: '#0d1b36', 'stroke-width': 1.5 }));
      g.appendChild(svg('text', { class: 'tl-num', x: cx.toFixed(1), y: BAR_Y + 30, 'text-anchor': 'middle' }, l.text));
    });

    // 文字の一覧
    const legend = h('ul', { class: 'tl-legend' });
    segs.forEach((seg) => {
      legend.appendChild(h('li', null,
        swatch(FILL[seg.key] || FILL.walk),
        h('span', { class: 'tl-name' }, segLabel(seg)),
        h('span', { class: 'tl-min' }, `${Math.round(seg.minutes)}分`)));
    });

    const sum = h('p', { class: 'tl-sum' },
      h('span', null, `避難所要時間 `),
      h('strong', null, need === null ? '不明' : `${ceilMin(need)}分`),
      h('span', null, `　津波到達時間 `),
      h('strong', null, avail === null ? '不明' : `${floorMin(avail)}分`));

    const notes = [];
    if (o.footnote) notes.push('時間は、すべて、揺れ始めてからの分数です。');
    if (o.footnote && segs.some((seg) => seg.key === 'climb')) notes.push('「上がる」は、避難先の階段や坂を上がる時間です。');
    if (o.footnote && segs.some((seg) => /直線距離\s*×\s*1\.3\s*の概算/.test(seg.label || ''))) notes.push('移動の距離は、直線距離 × 1.3 の概算です。');
    const note = notes.length ? h('p', { class: 'sub tl-foot' }, notes.join('')) : null;
    return h('div', { class: 'timeline' }, o.title ? h('p', { class: 'tl-title' }, o.title) : null, g, sum, legend, note);
  }

  EC.timeline = { render };
})();
