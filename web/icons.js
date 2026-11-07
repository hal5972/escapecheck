/*
 * 画面で使う印（アイコン）と、ロゴ。すべて自作の SVG で、外部のライブラリや画像のファイルは使わない。
 * 線の印は currentColor で描くので、色は、置いた部品の文字の色に従う。意味は、必ず文字でも伝える（印は補助）。
 * 使い方: EC.icon('mic', { size: 28 })  →  <svg> の要素（aria-hidden）
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const NS = 'http://www.w3.org/2000/svg';

  const el = (tag, attrs) => {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
    return node;
  };

  // 24 × 24 の格子。['p', 'パス'] は線、['dot', x, y] は小さな丸、['r', x, y, 幅, 高さ, 角] は角丸の四角、['c', x, y, 半径] は円の線
  const SHAPES = {
    mic: [['r', 9, 2.8, 6, 11.4, 3], ['p', 'M5.4 11.2a6.6 6.6 0 0 0 13.2 0'], ['p', 'M12 17.8v3.4M8.4 21.2h7.2']],
    check: [['p', 'M4.8 12.6l4.6 4.6L19.2 7.2']],
    cross: [['p', 'M6 6l12 12M18 6L6 18']],
    exclam: [['p', 'M12 5.6v8'], ['dot', 12, 18.2]],
    help: [['p', 'M8.8 9.4a3.3 3.3 0 1 1 5 2.8c-1.1.7-1.8 1.4-1.8 2.8'], ['dot', 12, 18.6]],
    info: [['dot', 12, 7], ['p', 'M12 11v6.6']],
    'chevron-left': [['p', 'M14.5 5l-7 7 7 7']],
    'chevron-right': [['p', 'M9.5 5l7 7-7 7']],
    'chevron-down': [['p', 'M5 9.5l7 7 7-7']],
    'chevron-up': [['p', 'M5 14.5l7-7 7 7']],
    'arrow-down': [['p', 'M12 4.5v15M6 13.5l6 6 6-6']],
    'arrow-right': [['p', 'M4.5 12h15M13.5 6l6 6-6 6']],
    speaker: [['p', 'M4 9.6v4.8h3.6L12.6 19V5L7.6 9.6H4z'], ['p', 'M16.2 9.2a4 4 0 0 1 0 5.6M18.8 6.6a7.6 7.6 0 0 1 0 10.8']],
    stop: [['r', 6, 6, 12, 12, 2.4]],
    clipboard: [['r', 5, 4.5, 14, 16.5, 2.4], ['p', 'M9 4.5v-.9h6v.9'], ['p', 'M8.8 13.4l2.4 2.4 4.2-4.6']],
    trend: [['p', 'M3.5 17l6-6 4 4 7-7.5'], ['p', 'M15.5 7.5h5v5']],
    shield: [['p', 'M12 3l7.5 3v5.6c0 4.4-3.1 7.6-7.5 9.4-4.4-1.8-7.5-5-7.5-9.4V6L12 3z'], ['p', 'M8.8 12.2l2.4 2.4 4.2-4.6']],
    flag: [['p', 'M6 21V4'], ['p', 'M6 5h11.5l-2.4 3.7 2.4 3.8H6']],
    print: [['p', 'M7 9V3.5h10V9'], ['r', 4, 9, 16, 8, 2], ['p', 'M7 14.5h10V21H7z']],
    home: [['p', 'M3.8 11.4L12 4l8.2 7.4'], ['p', 'M6.2 9.8V20h11.6V9.8'], ['p', 'M10 20v-5.4h4V20']],
    plus: [['p', 'M12 5v14M5 12h14']],
    person: [['c', 12, 8, 3.6], ['p', 'M5 20.2c.9-3.6 3.6-5.4 7-5.4s6.1 1.8 7 5.4']],
    list: [['p', 'M9 6.5h11M9 12h11M9 17.5h11'], ['dot', 4.6, 6.5], ['dot', 4.6, 12], ['dot', 4.6, 17.5]],
    lock: [['r', 5, 10.5, 14, 10, 2.4], ['p', 'M8.2 10.5V8a3.8 3.8 0 0 1 7.6 0v2.5']],
    wave: [['p', 'M2.5 10c2.3-2.8 4.7-2.8 7 0s4.7 2.8 7 0 3.5-2 5-.5'], ['p', 'M2.5 16.5c2.3-2.8 4.7-2.8 7 0s4.7 2.8 7 0 3.5-2 5-.5']],
    pin: [['p', 'M12 21s6.5-5.7 6.5-11A6.5 6.5 0 0 0 5.5 10c0 5.3 6.5 11 6.5 11z'], ['c', 12, 10, 2.4]],
    swap: [['p', 'M4.5 12a7.5 7.5 0 0 1 12.9-5.2L20 9.5'], ['p', 'M20 4.5v5h-5'], ['p', 'M19.5 12a7.5 7.5 0 0 1-12.9 5.2L4 14.5'], ['p', 'M4 19.5v-5h5']],
    trash: [['p', 'M5 7h14M10 7V4.5h4V7M7 7l.8 12.5h8.4L17 7']],
    play: [['p', 'M8 5.5v13l10.5-6.5L8 5.5z']],
    clock: [['c', 12, 12, 9.3], ['p', 'M12 7v5.2l3.4 2']],
    keyboard: [['r', 2.5, 6, 19, 12, 2.5], ['p', 'M6.6 10h.01M10.2 10h.01M13.8 10h.01M17.4 10h.01M7.5 14h9']],
    send: [['p', 'M4 12L20 4.5 15.5 20l-4-6.5L4 12z'], ['p', 'M11.5 13.5L20 4.5']],
    sheet: [['p', 'M6.5 3.5h8l4 4v13h-12z'], ['p', 'M14 3.5v4.5h4.5'], ['p', 'M9.5 12.5h5.5M9.5 16h5.5']],
    triangle: [['p', 'M12 3.8L3 19.4h18L12 3.8z'], ['p', 'M12 10.4v4'], ['dot', 12, 17.2]],
    bars: [['p', 'M5 10.5v3'], ['p', 'M8.5 7.5v9'], ['p', 'M12 4.5v15'], ['p', 'M15.5 7.5v9'], ['p', 'M19 10.5v3']],
  };

  /** 印を作る。size は px。filled の印（dot）は、currentColor で塗る。 */
  function icon(name, opts) {
    const o = opts || {};
    const size = o.size || 28;
    const svg = el('svg', {
      class: `ic ic-${name}${o.class ? ` ${o.class}` : ''}`,
      width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
      'stroke-width': o.stroke || 2.3, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
      'aria-hidden': 'true', focusable: 'false',
    });
    for (const s of SHAPES[name] || []) {
      if (s[0] === 'p') svg.appendChild(el('path', { d: s[1] }));
      else if (s[0] === 'dot') svg.appendChild(el('circle', { cx: s[1], cy: s[2], r: 1.25, fill: 'currentColor', stroke: 'none' }));
      else if (s[0] === 'r') svg.appendChild(el('rect', { x: s[1], y: s[2], width: s[3], height: s[4], rx: s[5] }));
      else if (s[0] === 'c') svg.appendChild(el('circle', { cx: s[1], cy: s[2], r: s[3] }));
    }
    return svg;
  }

  /** 判定の印（丸の中に、記号）。丸の色は CSS（.mark）が、判定の種類ごとに決める。 */
  const VERDICT_GLYPH = { ok: 'check', tight: 'exclam', short: 'cross', unknown: 'help' };
  function verdictIcon(key, size) {
    return icon(VERDICT_GLYPH[key] || 'help', { size: size || 24, stroke: 3 });
  }

  /**
   * ロゴ（自作）。チェックの印と、津波を表す波。実在の機関のロゴには似せない。
   * onDark: 暗い背景の上に置くとき（白い角丸の台に、藍の印）
   */
  function brandMark(opts) {
    const o = opts || {};
    const size = o.size || 40;
    const tile = o.onDark ? '#ffffff' : '#21478a';
    const check = o.onDark ? '#21478a' : '#ffffff';
    const wave = o.onDark ? '#a9c0e6' : '#a9c0e6';
    const svg = el('svg', {
      class: `brand-mark${o.class ? ` ${o.class}` : ''}`, width: size, height: size, viewBox: '0 0 64 64', 'aria-hidden': 'true', focusable: 'false',
    });
    svg.appendChild(el('rect', { width: 64, height: 64, rx: 15, fill: tile }));
    svg.appendChild(el('path', { d: 'M8 49c4.5-5.5 9-5.5 13.5 0s9 5.5 13.5 0 9-5.5 13.5 0 6.5 4 7.5 3', fill: 'none', stroke: wave, 'stroke-width': 4.2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    svg.appendChild(el('path', { d: 'M15.5 29.5l11 11 22-24', fill: 'none', stroke: check, 'stroke-width': 7.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    return svg;
  }

  EC.icon = icon;
  EC.verdictIcon = verdictIcon;
  EC.brandMark = brandMark;
})();
