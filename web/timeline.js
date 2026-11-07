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
    return seg.label || L.BREAKDOWN_LABELS[seg.key] || seg.key;
  }

  /** 一覧の記号（小さな四角）。タイムラインと同じ模様を使う。 */
  function swatch(fill) {
    return svg('svg', { class: 'tl-swatch', width: 36, height: 28, viewBox: '0 0 36 28', 'aria-hidden': 'true', focusable: 'false' },
      svg('rect', { x: 1.5, y: 1.5, width: 33, height: 25, rx: 4, fill, stroke: '#12161c', 'stroke-width': 2 }));
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
      g.appendChild(svg('rect', { x: x.toFixed(1), y: BAR_Y, width: w.toFixed(1), height: BAR_H, fill: FILL[seg.key] || FILL.walk, stroke: '#12161c', 'stroke-width': 2 }));
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
        g.appendChild(svg('rect', { x: xLine.toFixed(1), y: BAR_Y - 3, width: (xEnd - xLine).toFixed(1), height: BAR_H + 6, fill: 'none', stroke: '#9b1111', 'stroke-width': 4 }));
        g.appendChild(svg('rect', { x: xLine.toFixed(1), y: BAR_Y + BAR_H + 9, width: (xEnd - xLine).toFixed(1), height: 14, fill: 'url(#ec-pat-short)', stroke: '#9b1111', 'stroke-width': 2 }));
        lineBottom = BAR_Y + BAR_H + 26;
      } else {
        const gap = xLine - xEnd;
        if (gap > 0.5) {
          g.appendChild(svg('rect', { x: xEnd.toFixed(1), y: BAR_Y, width: gap.toFixed(1), height: BAR_H, fill: view.key === 'tight' ? 'url(#ec-pat-tight)' : 'url(#ec-pat-margin)', stroke: view.key === 'tight' ? '#5e3c00' : '#0e5a2a', 'stroke-width': 3 }));
        }
      }

      // 津波到達時間の縦の線と名前
      const labelX = Math.min(Math.max(xLine, PAD + 76), W - PAD - 76);
      g.appendChild(svg('line', { x1: xLine.toFixed(1), x2: xLine.toFixed(1), y1: 34, y2: lineBottom, stroke: '#12161c', 'stroke-width': 4 }));
      g.appendChild(svg('text', { class: 'tl-line-label', x: labelX.toFixed(1), y: 26, 'text-anchor': 'middle' }, `津波 ${floorMin(avail)}分`));

      // 余裕・不足の文字
      const note = svg('text', { class: `tl-note tl-note-${view.key}`, y: 140 });
      note.textContent = `${view.mark} ${view.detail || view.label}`;
      note.setAttribute('x', (view.key === 'short' ? W - PAD : Math.min(Math.max(xLine, 170), W - PAD)).toFixed(1));
      note.setAttribute('text-anchor', 'end');
      g.appendChild(note);
    }

    // 区間の分の数字。幅が足りないときは出さない
    labels.forEach((l) => {
      if (l.w >= 34) g.appendChild(svg('text', { class: 'tl-num', x: (l.x + l.w / 2).toFixed(1), y: BAR_Y + 30, 'text-anchor': 'middle' }, l.text));
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

    const note = o.footnote && segs.some((seg) => seg.key === 'climb')
      ? h('p', { class: 'sub tl-foot' }, '「上がる」は、避難先の階段や坂を上がる時間です。') : null;
    return h('div', { class: 'timeline' }, o.title ? h('p', { class: 'tl-title' }, o.title) : null, g, sum, legend, note);
  }

  EC.timeline = { render };
})();
