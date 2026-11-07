/*
 * 画面の部品: 出典のラベルと内容、項目の行、判定の表示、避難経路の地図、確認の表示。
 * 画面に出す文字は、すべて textContent（h() の text・子要素）で入れる。innerHTML は使わない。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const { h, svg, clear, isNum, verdictView, splitLabel } = EC.u;
  const L = EC.L;

  // ---------------------------------------------------------------------------
  // 出典（S-02）。欄ごとに「本人の話」「民生委員の話」「国・県のデータ」「仮定」を表示し、押すと内容を表示する
  // ---------------------------------------------------------------------------

  function sourceInfo(plan, sourceId) {
    const s = plan && plan.sources ? plan.sources[sourceId] : null;
    if (!sourceId || !s) return { key: 'unknown', label: L.SOURCE_LABELS.unknown, source: null };
    let key = 'unknown';
    if (s.kind === 'utterance') key = s.speaker === 'person' ? 'person' : s.speaker === 'family' ? 'family' : 'minsei';
    else if (s.kind === 'dataset') key = 'data';
    else if (s.kind === 'tool') key = 'tool';
    else if (s.kind === 'assumption') key = 'assumption';
    else if (s.kind === 'map_tap') key = 'map_tap';
    return { key, label: L.SOURCE_LABELS[key] || L.SOURCE_LABELS.unknown, source: s };
  }

  function chip(info) {
    return h('span', { class: `chip chip-${info.key === 'map_tap' ? 'map' : info.key}` }, info.label);
  }

  function sourceDetail(plan, sourceId, quote, T) {
    const s = plan && plan.sources ? plan.sources[sourceId] : null;
    const box = h('div', { class: 'source-detail' });
    if (!s) {
      box.append(h('p', null, '出典がありません。'));
      return box;
    }
    box.append(h('p', { class: 'strong' }, `出典: ${T(s.label)}`));
    if (s.kind === 'utterance') {
      const q = quote || s.text;
      if (q) box.append(h('p', null, '話した言葉: ', h('span', { class: 'quote' }, `「${T(q)}」`)));
    } else if (s.kind === 'map_tap') {
      box.append(h('p', null, '地図をタップして、自宅の場所を決めました。位置は、この端末の中に保存します。'));
    } else {
      if (s.name) box.append(h('p', null, `データ: ${s.name}`));
      if (s.version) box.append(h('p', null, `版: ${s.version}`));
      if (s.value !== undefined && s.value !== null && s.value !== '') box.append(h('p', null, `値: ${s.value}${s.unit || ''}`));
      if (s.basis) box.append(h('p', null, `${s.kind === 'assumption' ? '仮定の理由' : '補足'}: ${s.basis}`));
      if (s.url) box.append(h('p', null, `URL: ${s.url}`));
    }
    if (s.kind === 'assumption') box.append(h('p', null, 'これは仮定です。本人から聞いた値があれば、置き換えます。'));
    return box;
  }

  // ---------------------------------------------------------------------------
  // 項目の行
  // ---------------------------------------------------------------------------

  const distanceText = (m) => `約${Math.round(m / 10) * 10}m`;

  function supporterText(plan, T) {
    return (plan.supporters || [])
      .map((s) => {
        const detail = [s.relation, s.age_band].filter(Boolean).join('・');
        return `${T(s.alias)}${detail ? `（${detail}）` : ''}`;
      })
      .join('、');
  }

  /** 項目の値を、画面に出す文字にする。値の形（文字・数・オブジェクト）が変わっても壊れないようにする。 */
  function valueText(value, T) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'string' || typeof value === 'number') return T(String(value));
    if (Array.isArray(value)) return value.map((v) => valueText(v, T)).filter(Boolean).join('、');
    if (typeof value === 'object') {
      const name = value.name || value.label || value.text;
      if (name) return T(String(name));
      return Object.values(value).filter((v) => typeof v === 'string').map((v) => T(v)).join(' ');
    }
    return '';
  }

  function isFilled(field) {
    return Boolean(field) && field.status === 'filled' && field.value !== null && field.value !== undefined && field.value !== '';
  }

  /** 項目 1 つ。行全体が 64px 以上のボタンで、押すと出典の内容を開く。 */
  function fieldRow(plan, key, T, opts) {
    const o = opts || {};
    const field = (plan.fields && plan.fields[key]) || { value: null, status: 'unconfirmed' };
    const filled = isFilled(field);
    const parts = splitLabel(L.FIELDS[key] || key);
    const help = L.FIELD_HELP[key];
    const hint = parts.hint && help ? `${parts.hint}（${help}）` : parts.hint || (help ? `（${help}）` : '');

    let text = '';
    let sub = '';
    if (filled) {
      text = key === 'supporters' && (plan.supporters || []).length ? supporterText(plan, T) : valueText(field.value, T);
      if (key === 'shelter' && field.value && typeof field.value === 'object' && isNum(field.value.distance_m)) {
        sub = `距離の概算 ${distanceText(field.value.distance_m)}（直線距離×1.3）`;
      }
    }

    const detail = h('div', { class: 'source-detail-wrap', hidden: true });
    const button = h('button', {
      type: 'button',
      class: `field-row${filled ? '' : ' unconfirmed'}${o.changed ? ' changed' : ''}`,
      'aria-expanded': 'false',
    },
    h('span', { class: 'field-title' }, parts.title),
    hint ? h('span', { class: 'field-hint' }, hint) : null,
    h('span', { class: 'field-value' }, filled ? text : '？ 未確認'),
    sub ? h('span', { class: 'field-hint' }, sub) : null,
    filled ? chip(sourceInfo(plan, field.source_id)) : null);

    button.addEventListener('click', () => {
      const open = button.getAttribute('aria-expanded') === 'true';
      button.setAttribute('aria-expanded', open ? 'false' : 'true');
      if (!open && !detail.firstChild) {
        detail.append(filled ? sourceDetail(plan, field.source_id, field.quote, T) : h('div', { class: 'source-detail' }, h('p', null, 'まだ聞いていません。話していない欄は、未確認のままにします。値は補いません。')));
      }
      detail.hidden = open;
    });

    return h('div', { class: 'field-item', dataset: { field: key } }, button, detail);
  }

  // ---------------------------------------------------------------------------
  // 判定（色だけで伝えない: 印と文字を付ける）
  // ---------------------------------------------------------------------------

  function verdictBadge(result) {
    const v = verdictView(result);
    return h('div', { class: `verdict verdict-${v.key}` },
      h('span', { class: 'mark', 'aria-hidden': 'true' }, v.mark),
      h('span', null, h('span', { class: 'vtext' }, v.label), v.detail ? h('span', { class: 'vdetail' }, v.detail) : null));
  }

  // ---------------------------------------------------------------------------
  // 避難経路の地図（F-16）
  // ---------------------------------------------------------------------------

  function findShelter(plan, facts, shelterId) {
    const list = (facts && facts.shelters) || [];
    const field = plan.fields && plan.fields.shelter && plan.fields.shelter.value;
    const id = shelterId || (field && typeof field === 'object' ? field.shelter_id : null);
    let found = id ? list.find((s) => s.shelter_id === id) : null;
    if (!found && field && typeof field === 'object' && field.point) found = { ...field };
    if (!found && field && typeof field === 'object' && field.name) found = list.find((s) => s.name === field.name) || null;
    return found || null;
  }

  /** 避難の途中で津波が来る位置の比率（0〜1）。描画だけに使う。表示する数値は、コードが出した値だけ。 */
  function stallRatio(result) {
    if (!result || !isNum(result.avail_min)) return null;
    let start = 0;
    let walk = 0;
    for (const seg of result.breakdown || []) {
      if (seg.key === 'walk') walk += seg.minutes;
      else if (seg.key !== 'climb') start += seg.minutes;
    }
    if (!walk) return null;
    return Math.max(0, Math.min(1, (result.avail_min - start) / walk));
  }

  const lerp = (a, b, t) => ({ lat: a.lat + (b.lat - a.lat) * t, lon: a.lon + (b.lon - a.lon) * t });

  /**
   * シナリオの結果を、地図に描く。
   * ctx: { plan, facts, T, shelterId（比べるときの別の避難先）, oldShelterId（いまの避難先を点線で出す） }
   */
  function scenarioMap(result, ctx) {
    const plan = ctx.plan;
    const T = ctx.T;
    const home = plan.home;
    const shelter = findShelter(plan, ctx.facts, ctx.shelterId);
    const map = ctx.fixed
      ? EC.map.create({ fixed: true, width: ctx.fixed.width, height: ctx.fixed.height, label: '避難経路の地図' })
      : EC.map.create({ height: ctx.height || 300, label: '避難経路の地図' });
    const wrap = h('div', { class: 'mapbox' }, map.el);
    if (!home || !shelter || !shelter.point) {
      wrap.append(h('p', { class: 'sub' }, '地図に経路を描くための位置が、まだありません。'));
      wrap.append(map.credit);
      if (home) {
        map.setShapes([{ t: 'marker', kind: 'home', at: home, label: '自宅', pos: 'r' }]);
        map.setView(home, 16);
      }
      return { el: wrap, map };
    }

    const shapes = [];
    const points = [home, shelter.point];
    // plain: 避難カードの地図。経路だけを出し、間に合わない区間の印は付けない
    const verdict = ctx.plain ? 'plain' : verdictView(result).key;
    const p = verdict === 'short' ? stallRatio(result) : null;
    let stalled = false;

    if (ctx.oldShelterId) {
      const old = findShelter(plan, ctx.facts, ctx.oldShelterId);
      if (old && old.point) {
        shapes.push({ t: 'line', kind: 'old', from: home, to: old.point });
        shapes.push({ t: 'marker', kind: 'shelter', at: old.point, label: 'いまの避難先', pos: 't' });
        points.push(old.point);
      }
    }

    if (verdict === 'short') {
      if (p === null || p <= 0) {
        shapes.push({ t: 'line', kind: 'stall', from: home, to: shelter.point });
        stalled = true;
      } else if (p >= 1) {
        shapes.push({ t: 'line', kind: 'plain', from: home, to: shelter.point });
      } else {
        const stop = lerp(home, shelter.point, p);
        shapes.push({ t: 'line', kind: 'plain', from: home, to: stop });
        shapes.push({ t: 'line', kind: 'stall', from: stop, to: shelter.point });
        stalled = true;
        shapes.push({ t: 'marker', kind: 'stop', at: stop, label: 'ここで津波', pos: 'b' });
      }
    } else {
      shapes.push({ t: 'line', kind: verdict === 'ok' ? 'good' : 'plain', from: home, to: shelter.point });
    }

    // 避難支援者が駆けつける区間（S5・S8）
    const usesSupporter = (result.breakdown || []).some((seg) => seg.key === 'supporter_travel');
    const travel = (result.breakdown || []).find((seg) => seg.key === 'supporter_travel');
    const supporter = usesSupporter
      ? ((plan.supporters || []).find((s) => s.location && travel && travel.label && travel.label.includes(s.alias))
        || (plan.supporters || []).find((s) => s.location))
      : null;
    if (supporter) {
      shapes.push({ t: 'line', kind: 'plain', from: supporter.location, to: home });
      shapes.push({ t: 'marker', kind: 'supporter', at: supporter.location, label: `${T(supporter.alias).replace(/さん$/, '')}さん宅`, pos: 'r' });
      points.push(supporter.location);
    }

    shapes.push({ t: 'marker', kind: 'shelter', at: shelter.point, label: ctx.shelterId ? '新しい避難先' : '避難先', pos: 't' });
    shapes.push({ t: 'marker', kind: 'home', at: home, label: '自宅', pos: 'b' });

    map.setShapes(shapes);
    map.fit(points, { x: 44, y: 64 });

    const item = (mark, text) => h('span', { class: 'key-item' }, h('span', { class: 'key-mark', 'aria-hidden': 'true' }, mark), text);
    const key = h('p', { class: 'map-key' },
      item('●', '自宅'), item('▲', '避難先'),
      supporter ? item('◆', '避難支援者の家') : null,
      stalled ? item('×', '津波に追いつかれる所') : null);
    wrap.append(key);
    if (stalled) wrap.append(h('p', { class: 'sub' }, '赤い点線は、間に合わない区間です。'));
    wrap.append(map.credit);
    return { el: wrap, map };
  }

  /** シナリオの表示名。S8（聞き取りの中だけにある前提条件）は、その前提条件の名前で出す。 */
  function scenarioTitle(plan, finding) {
    if (finding.scenario_id === 'S8') {
      const premise = (plan.premises || []).find((p) => p.code === finding.hole_type) || (plan.premises || [])[0];
      if (premise && L.PREMISE_CODES[premise.code]) return L.PREMISE_CODES[premise.code];
    }
    return L.SCENARIOS[finding.scenario_id] || finding.scenario_id;
  }

  /** 「◯◯は、間に合わない」のように、文の主語に使うシナリオの呼び方。S8 は、前提条件の名前に「場合」を付ける。 */
  function scenarioTopic(plan, finding) {
    const title = scenarioTitle(plan, finding);
    return finding.scenario_id === 'S8' ? `${title}場合` : title;
  }

  // ---------------------------------------------------------------------------
  // 場所の事実（浸水深・津波到達時間・標高）。取得できない値は「取得できませんでした」と出す（E4）
  // ---------------------------------------------------------------------------

  function factsPanel(facts) {
    const na = '取得できませんでした';
    const box = h('div', { class: 'card facts-card' }, h('p', { class: 'strong' }, 'この場所について'));
    if (!facts) {
      box.append(h('p', null, `${na}。地図は、点と線だけを表示します。`));
      return box;
    }
    box.append(
      h('p', null, `浸水深（津波で水が来たときの深さ）: ${facts.inundation_rank || na}`),
      h('p', null, `津波到達時間（浸水が30cmになるまで）: ${isNum(facts.arrival_min) ? `${EC.u.floorMin(facts.arrival_min)}分` : na}`));
    if (facts.arrival_version) box.append(h('p', { class: 'sub' }, `予測の版: ${facts.arrival_version}`));
    // 標高が取得できないときは、理由の文（notes）に標高のことが書かれていれば、同じ内容を 2 回出さない
    const notes = facts.notes || [];
    if (isNum(facts.elevation_m) || !notes.some((n) => /標高/.test(n))) {
      box.append(h('p', null, `標高: ${isNum(facts.elevation_m) ? `${facts.elevation_m}m` : na}`));
    }
    for (const note of notes) box.append(h('p', { class: 'sub' }, note));
    return box;
  }

  // ---------------------------------------------------------------------------
  // 確認の表示（大きなボタン 2 つ）
  // ---------------------------------------------------------------------------

  function confirmDialog(opts) {
    return new Promise((resolve) => {
      const overlay = document.getElementById('overlay');
      const previous = document.activeElement;
      const close = (value) => {
        overlay.hidden = true;
        clear(overlay);
        if (previous && previous.focus) previous.focus();
        resolve(value);
      };
      const cancel = h('button', { type: 'button', class: 'btn', onclick: () => close(false) }, opts.cancelLabel || 'やめる');
      const ok = h('button', { type: 'button', class: 'btn btn-primary', onclick: () => close(true) }, opts.okLabel || 'はい');
      clear(overlay);
      overlay.append(h('div', { class: 'dialog', role: 'alertdialog', 'aria-modal': 'true', 'aria-label': opts.message },
        h('p', null, opts.message), cancel, ok));
      overlay.hidden = false;
      cancel.focus();
    });
  }

  EC.ui = {
    sourceInfo, chip, sourceDetail, valueText, isFilled, fieldRow, supporterText,
    verdictBadge, findShelter, stallRatio, scenarioMap, confirmDialog, distanceText, factsPanel, scenarioTitle, scenarioTopic,
  };
})();
