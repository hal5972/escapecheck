/*
 * 印刷用の 2 枚（F-13）: 1 枚目は市に出す様式（A4）、2 枚目は本人用の避難カード（A4、大きな文字）。
 * 画面の「印刷」の画面にも、同じ内容を表示する。印刷はブラウザの印刷機能と print.css で行う。
 * 本名は端末の中だけにあり、画面と印刷のときに、仮名（Aさん）から戻して出す（R-02）。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const { h, verdictView, isNum, ceilMin, floorMin, formatDate, worstResult, severityOf } = EC.u;
  const L = EC.L;

  const SAFETY = 'この計画は話し合いのたたき台です。揺れたら、すぐ高い所へ。市の情報に従ってください。';

  function personName(rec) {
    const p = (rec.names || []).find((n) => n.role === 'person') || (rec.names || [])[0];
    if (!p) return rec.plan.alias;
    return p.full || p.call || p.real || rec.plan.alias;
  }

  function callName(rec) {
    const p = (rec.names || []).find((n) => n.role === 'person') || (rec.names || [])[0];
    return p && (p.call || (p.real ? `${p.real}さん` : null)) || rec.plan.alias;
  }

  /** 点検の結果のうち、いちばん厳しいもの（余裕が最も小さい）。 */
  function worstFinding(plan) {
    const all = (plan.findings || []).filter((f) => f.result);
    // 計画の避難先に上がれない問題点の時間は、別の避難先の分。避難支援者が来る場合（S5 など）の時間は、支援者が動く時間を含む。
    // 本人用のカードには、本人だけの動きの時間を出す（ほかにあれば）
    const own = all.filter((f) => !EC.u.isBlockedShelter(plan, f) && !(f.result.breakdown || []).some((s) => s.key === 'supporter_travel'));
    const list = own.length ? own : all;
    if (!list.length) return null;
    return list.slice().sort((a, b) => {
      const ra = worstResult(a.result);
      const rb = worstResult(b.result);
      const d = severityOf(ra) - severityOf(rb);
      if (d !== 0) return d;
      const ma = isNum(ra.margin_min) ? ra.margin_min : 0;
      const mb = isNum(rb.margin_min) ? rb.margin_min : 0;
      return ma - mb;
    })[0];
  }

  /** 印刷する項目の名前。避難開始時間は日中・夜間を区別する。そのほかは、括弧の中の例を省く。 */
  function printLabel(key) {
    const parts = EC.u.splitLabel(L.FIELDS[key] || key);
    const keep = key.startsWith('time_to_leave') || key === 'shelter';
    return `${parts.title}${keep && parts.hint ? `（${parts.hint}）` : ''}`;
  }

  function shelterName(plan, T) {
    const f = plan.fields && plan.fields.shelter;
    return EC.ui.isFilled(f) ? EC.ui.valueText(f.value, T) : '';
  }

  // ---------------------------------------------------------------------------
  // 1 枚目: 市に出す様式
  // ---------------------------------------------------------------------------

  function formSheet(rec, ctx) {
    const plan = rec.plan;
    const T = ctx.T;
    const rows = [];
    rows.push(['お名前', `${personName(rec)}${rec.sample ? '（デモ用の架空の人物）' : ''}`]);
    rows.push(['地区', plan.area_label || '']);
    rows.push(['作成日', formatDate(plan.updated || new Date().toISOString())]);

    const tbody = h('tbody');
    for (const [label, value] of rows) tbody.append(h('tr', null, h('th', null, label), h('td', { colspan: '2' }, value)));

    for (const key of Object.keys(L.FIELDS)) {
      const f = plan.fields && plan.fields[key];
      const filled = EC.ui.isFilled(f);
      let text = '未確認';
      if (filled) text = key === 'supporters' && (plan.supporters || []).length ? EC.ui.supporterText(plan, T) : EC.ui.valueText(f.value, T);
      tbody.append(h('tr', null,
        h('th', null, printLabel(key)),
        h('td', null, text),
        h('td', { class: 'src-cell' }, filled ? EC.ui.chip(EC.ui.sourceInfo(plan, f.source_id)) : null)));
    }

    // 画面では閉じておく（印刷では、print.css が開く）。押すと開く
    const body = h('div', { class: 'sheet-body', hidden: true }, h('table', { class: 'sheet-table' }, tbody));
    const toggle = h('button', { type: 'button', class: 'btn-row sheet-toggle', 'aria-expanded': 'false' }, '市に出す様式の中身を見る');
    toggle.addEventListener('click', () => {
      const open = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
      body.hidden = open;
    });
    const sheet = h('section', { class: 'sheet sheet-form' },
      h('h2', { class: 'sheet-title' }, '1枚目: 個別避難計画（市に出す様式の項目）'),
      h('p', { class: 'sub screen-only' }, 'A4・1枚。市の様式に合わせた項目です。'),
      toggle, body);

    // 点検の結果
    const findings = plan.findings || [];
    if (findings.length) {
      const resultRows = h('tbody');
      for (const f of findings) {
        const v = verdictView(EC.u.findingResult(plan, f));
        const blocked = EC.u.isBlockedShelter(plan, f);
        resultRows.append(h('tr', null,
          h('th', null, EC.ui.scenarioTitle(plan, f)),
          h('td', null, `${v.mark} ${v.text}`,
            isNum(f.result.need_min) && isNum(f.result.avail_min)
              ? h('div', null, `${blocked ? `上がれる避難先（${f.result.shelter_name || '別の避難先'}）にした場合: ` : ''}避難所要時間 ${ceilMin(f.result.need_min)}分／津波到達時間 ${floorMin(f.result.avail_min)}分`)
              : null)));
      }
      body.append(h('h3', null, '点検の結果（厳しい条件でのシミュレーション）'), h('table', { class: 'sheet-table' }, resultRows));
      const allShort = findings.every((f) => verdictView(EC.u.findingResult(plan, f)).key === 'short');
      if (allShort) body.append(h('p', { class: 'strong' }, 'この方は、現在の条件では間に合いません。市や家族と話し合うための資料です。'));
    }

    // 前提条件（聞き取りの中だけにある条件）
    if ((plan.premises || []).length) {
      const ul = h('ul', null);
      for (const p of plan.premises) {
        ul.append(h('li', null, `${L.PREMISE_CODES[p.code] || p.code}　「${T(p.quote)}」`));
      }
      body.append(h('h3', null, '聞き取りの中だけにあった前提条件'), ul);
    }

    // 改善の記録
    if ((plan.approvals || []).length) {
      const ul = h('ul', null);
      for (const a of plan.approvals) {
        const fix = (plan.fixes || []).find((x) => x.fix_id === a.fix_id);
        if (fix) ul.append(h('li', null, `${L.FIX_TYPES[fix.fix_type] || fix.fix_type}（${a.by === 'person' ? '本人' : '民生委員'}が承認）`));
      }
      body.append(h('h3', null, '改善の記録'), ul);
    }

    // 出典と注意
    const datasets = Object.values(plan.sources || {}).filter((s) => s.kind === 'dataset');
    const foot = h('div', { class: 'sheet-foot' },
      h('p', null, SAFETY),
      datasets.length ? h('p', null, `データの出典: ${datasets.map((d) => `${d.label}${d.version && !d.label.includes(d.version) ? `（${d.version}）` : ''}`).join('、')}`) : null,
      h('p', null, '地図: 地理院タイル、津波浸水想定: 高知県（ハザードマップポータルサイト）'),
      rec.sample ? h('p', null, 'この計画は、デモ用の架空の人物のサンプルです。実在の人物とは関係ありません。') : null);
    body.append(foot);
    return sheet;
  }

  // ---------------------------------------------------------------------------
  // 2 枚目: 本人用の避難カード
  // ---------------------------------------------------------------------------

  function cardSheet(rec, ctx) {
    const plan = rec.plan;
    const T = ctx.T;
    const sheet = h('section', { class: 'sheet sheet-card' },
      h('div', { class: 'card-banner' }, '揺れたら、すぐ避難'),
      h('h2', { class: 'sheet-title card-name' }, `${callName(rec)}の避難カード`),
      h('p', { class: 'sub screen-only' }, '2枚目: 本人用。A4・1枚。冷蔵庫などに貼ります。'));

    const sName = shelterName(plan, T);
    const shelterField = plan.fields && plan.fields.shelter && plan.fields.shelter.value;
    sheet.append(h('p', { class: 'big-line' }, `避難先: ${sName || '未確認'}`));
    // 計画の避難先に上がれないまま印刷するときは、カードに警告を出す
    if ((plan.findings || []).some((f) => EC.u.isBlockedShelter(plan, f))) {
      sheet.append(h('p', { class: 'strong' }, 'この避難先には、上がれません。避難先を相談してください。'));
    }
    if (shelterField && typeof shelterField === 'object' && isNum(shelterField.distance_m)) {
      sheet.append(h('p', { class: 'sub' }, `家から ${EC.ui.distanceText(shelterField.distance_m)}（直線距離×1.3の概算）`));
    }

    // 簡易地図（画面用の幅に合わせた地図と、印刷用の固定の大きさの地図）
    const worst = worstFinding(plan);
    const mapSlot = h('div', { class: 'card-map' });
    sheet.append(mapSlot);
    const result = worst ? worst.result : null;
    const screenMap = result ? EC.ui.scenarioMap(result, { plan, facts: ctx.facts, T, height: 260, plain: true }) : null;
    if (screenMap) mapSlot.append(h('div', { class: 'map-screen' }, screenMap.el));
    // 印刷用は、大きさを固定した別の地図にする（画面の幅の地図を引き伸ばすと、文字が小さくなるため）
    const printMap = result ? EC.ui.scenarioMap(result, { plan, facts: ctx.facts, T, fixed: { width: 700, height: 300 }, plain: true }) : null;
    if (printMap) mapSlot.append(h('div', { class: 'print-map' }, printMap.el));

    // 避難所要時間（いちばん厳しい場合）
    if (worst) {
      const r = worstResult(worst.result);
      const v = verdictView(r);
      sheet.append(
        h('p', { class: 'big-line' }, `避難所要時間: ${isNum(worst.result.need_min) ? `${ceilMin(worst.result.need_min)}分` : '不明'}`),
        h('p', { class: 'big-line' }, `津波到達時間: ${isNum(worst.result.avail_min) ? `${floorMin(worst.result.avail_min)}分` : '不明'}`),
        h('div', { class: `verdict verdict-${v.key}` },
          h('span', { class: 'mark', 'aria-hidden': 'true' }, v.mark),
          h('span', { class: 'vtext' }, v.text)),
        h('p', { class: 'sub' }, `いちばん厳しい場合の時間です。条件: ${EC.ui.scenarioTitle(plan, worst)}`));
      const open = (plan.findings || []).filter((f) => ['short', 'unknown'].includes(verdictView(EC.u.findingResult(plan, f)).key));
      if (open.length) {
        sheet.append(h('p', { class: 'strong' }, '間に合わない場合があります。家族や市と相談してください。'));
      }
      // 避難支援者の判定（result.supporter_verdict）は、本人の時間とは別に出す
      const supporterShort = (plan.findings || []).find((f) => f.result && f.result.supporter_verdict === 'short');
      if (supporterShort) {
        const alias = ((plan.supporters || [])[0] || {}).alias;
        sheet.append(h('p', { class: 'strong' }, `避難支援者${alias ? `（${T(alias)}）` : ''}自身の避難が、間に合わないことがあります。`));
      }
    } else {
      sheet.append(h('p', { class: 'big-line' }, 'まだ点検していません。'));
    }

    const callers = (plan.supporters || []).map((s) => T(s.alias)).join('、');
    sheet.append(h('p', { class: 'big-line' }, `声をかける人: ${callers || '未確認'}`));
    sheet.append(h('p', { class: 'notice notice-safety' }, SAFETY));
    if (rec.sample) sheet.append(h('p', { class: 'sub' }, 'デモ用の架空の人物のサンプルです。'));
    return sheet;
  }

  /** 印刷の画面の中身を作る。 */
  function buildSheets(rec, ctx) {
    return h('div', { class: 'sheets-preview', id: 'sheets-root' }, formSheet(rec, ctx), cardSheet(rec, ctx));
  }

  EC.print = { buildSheets, worstFinding, personName, callName, SAFETY };
})();
