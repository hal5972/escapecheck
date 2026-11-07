/*
 * 共通の関数: DOM の作成（textContent だけを使う。innerHTML は使わない）、分の表示、判定の表示、名前の置き換え。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const L = EC.L;
  const SVG_NS = 'http://www.w3.org/2000/svg';

  // ---------------------------------------------------------------------------
  // DOM の作成
  // ---------------------------------------------------------------------------

  // 設定させない属性。innerHTML 系は使わない。style 属性は CSP で止まるので、CSS のクラスと CSSOM を使う
  const BLOCKED = new Set(['innerhtml', 'outerhtml', 'srcdoc', 'style']);

  function safeHref(value) {
    try {
      const url = new URL(String(value), window.location.href);
      return url.protocol === 'http:' || url.protocol === 'https:' ? String(value) : null;
    } catch (e) {
      return null;
    }
  }

  function applyProps(el, props) {
    if (!props) return;
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      const lower = key.toLowerCase();
      if (BLOCKED.has(lower)) continue;
      if (key === 'class') {
        el.setAttribute('class', String(value));
      } else if (key === 'text') {
        el.textContent = String(value);
      } else if (key === 'dataset') {
        for (const [dk, dv] of Object.entries(value)) el.dataset[dk] = String(dv);
      } else if (lower.startsWith('on') && typeof value === 'function') {
        el.addEventListener(lower.slice(2), value);
      } else if (lower === 'href') {
        const ok = safeHref(value);
        if (ok) el.setAttribute('href', ok);
      } else {
        el.setAttribute(key, value === true ? '' : String(value));
      }
    }
  }

  function appendChildren(el, children) {
    for (const child of children) {
      if (child === undefined || child === null || child === false) continue;
      if (Array.isArray(child)) appendChildren(el, child);
      else if (child instanceof Node) el.appendChild(child);
      else el.appendChild(document.createTextNode(String(child)));
    }
  }

  function h(tag, props, ...children) {
    const el = document.createElement(tag);
    applyProps(el, props);
    appendChildren(el, children);
    return el;
  }

  function svg(tag, props, ...children) {
    const el = document.createElementNS(SVG_NS, tag);
    applyProps(el, props);
    appendChildren(el, children);
    return el;
  }

  function clear(el) {
    el.replaceChildren();
    return el;
  }

  /** 子を追加する。null・undefined・false は無視する（DOM の append に null を渡すと、文字の「null」が入るため）。 */
  function add(el, ...children) {
    appendChildren(el, children);
    return el;
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function uid(prefix) {
    const rnd = Math.random().toString(16).slice(2, 10);
    return `${prefix}_${rnd}`;
  }

  // ---------------------------------------------------------------------------
  // 分の表示。安全側に丸める（不足は切り上げ、余裕は切り捨て）。表示の丸めだけで、計算はしない
  // ---------------------------------------------------------------------------

  const EPS = 1e-9;
  const isNum = (x) => typeof x === 'number' && Number.isFinite(x);
  const ceilMin = (x) => (isNum(x) ? Math.ceil(x - EPS) : null);
  const floorMin = (x) => (isNum(x) ? Math.floor(x + EPS) : null);
  const roundMin = (x) => (isNum(x) ? Math.round(x) : null);

  const REASONS = {
    NO_ARRIVAL_TIME: '津波到達時間が分かりません',
    NO_SHELTER: '避難先が決まっていません',
    NO_HOME: '自宅の場所が決まっていません',
    NO_WALK_SPEED: '歩行速度が分かりません',
    NO_SUPPORTER: '避難を支援する人がいないと、避難できません',
  };

  /** 判定の表示用の値。色だけで伝えないため、印（○△×？）と文字を必ず付ける。 */
  function verdictView(result) {
    const v = result ? result.verdict : 'cannot_compute';
    const margin = result ? result.margin_min : null;
    if (v === 'ok') {
      const m = floorMin(margin);
      return {
        key: 'ok', mark: '○', label: L.VERDICT_LABELS.ok,
        detail: m === null ? '' : `${m}分の余裕`,
        text: m === null ? '間に合う' : `間に合う（${m}分の余裕）`,
      };
    }
    if (v === 'tight') {
      const m = floorMin(margin);
      return {
        key: 'tight', mark: '△', label: L.VERDICT_LABELS.tight,
        detail: m === null ? '' : `${m}分の余裕`,
        text: m === null ? '余裕が少ない' : `余裕が少ない（${m}分の余裕）`,
      };
    }
    if (v === 'short') {
      const m = isNum(margin) ? ceilMin(-margin) : null;
      return {
        key: 'short', mark: '×', label: L.VERDICT_LABELS.short,
        detail: m === null ? '' : `${m}分不足`,
        text: m === null ? '間に合わない' : `間に合わない（${m}分不足）`,
      };
    }
    const reason = (result && REASONS[result.reason_code]) || '必要な値が足りません';
    return { key: 'unknown', mark: '？', label: L.VERDICT_LABELS.cannot_compute, detail: reason, text: `計算できない（${reason}）` };
  }

  const SEVERITY = { short: 0, unknown: 1, tight: 2, ok: 3 };
  const severityOf = (result) => SEVERITY[verdictView(result).key];

  /** 避難支援者の判定があるときは、悪いほうを全体の判定にする（S5 など）。 */
  function worstResult(result) {
    if (!result) return result;
    if (result.supporter_verdict && SEVERITY[verdictView({ verdict: result.supporter_verdict }).key] < severityOf(result)) {
      return { ...result, verdict: result.supporter_verdict, margin_min: result.supporter_margin_min };
    }
    return result;
  }

  // ---------------------------------------------------------------------------
  // 文字列
  // ---------------------------------------------------------------------------

  /** "避難先（指定緊急避難場所）" → { title: "避難先", hint: "指定緊急避難場所" } */
  function splitLabel(label) {
    const m = /^(.*?)（(.*)）$/.exec(label || '');
    return m ? { title: m[1], hint: m[2] } : { title: label || '', hint: '' };
  }

  /** 説明文のプレースホルダー（{need_min} など）を、結果の値で置き換える。
   *  通常はサーバーが置き換え済みの explanation を返す。これは explanation が無いときの予備。 */
  function fillTemplate(template, result, extra) {
    if (!template) return '';
    const r = result || {};
    // 区間（避難を始めるまで・歩く・上がる・声をかける）の分。所要時間なので、切り上げる
    const part = (key) => {
      const segs = (r.breakdown || []).filter((s) => s.key === key && isNum(s.minutes));
      return segs.length ? String(ceilMin(segs.reduce((sum, s) => sum + s.minutes, 0))) : null;
    };
    const margin = isNum(r.margin_min) ? r.margin_min : null;
    const sMargin = isNum(r.supporter_margin_min) ? r.supporter_margin_min : null;
    const values = {
      need_min: isNum(r.need_min) ? String(ceilMin(r.need_min)) : null,
      avail_min: isNum(r.avail_min) ? String(floorMin(r.avail_min)) : null,
      // {margin_min} は、間に合うときの余裕。足りないときの古い書き方でも、不足の分を出す
      margin_min: margin === null ? null : String(margin < 0 ? ceilMin(-margin) : floorMin(margin)),
      shortage_min: margin !== null && margin < 0 ? String(ceilMin(-margin)) : null,
      supporter_margin_min: sMargin !== null && sMargin >= 0 ? String(floorMin(sMargin)) : null,
      supporter_shortage_min: sMargin !== null && sMargin < 0 ? String(ceilMin(-sMargin)) : null,
      leave_min: part('leave'),
      walk_min: part('walk'),
      climb_min: part('climb'),
      help_min: part('help'),
      ...(extra || {}),
    };
    return String(template).replace(/\{(\w+)\}/g, (all, key) => (values[key] != null ? values[key] : '？'));
  }

  function formatDate(iso) {
    const d = iso ? new Date(iso) : new Date();
    if (Number.isNaN(d.getTime())) return '';
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
  }

  function formatClock(iso) {
    const d = iso ? new Date(iso) : new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  // ---------------------------------------------------------------------------
  // 名前の置き換え（R-02）。本名は端末の中だけ。サーバーと Claude には仮名（Aさん）だけを送る
  // ---------------------------------------------------------------------------

  /** names: [{ alias: "Aさん", real: "山田", call: "山田さん" }] */
  function pseudonymize(text, names) {
    let out = String(text || '');
    const list = (names || [])
      .filter((n) => n && n.real && n.alias)
      .sort((a, b) => b.real.length - a.real.length);
    for (const n of list) {
      const real = n.real.replace(/\s+/g, '');
      for (const suffix of ['さん', '様', 'さま', '']) {
        out = out.split(real + suffix).join(n.alias);
      }
    }
    return out;
  }

  /** 画面に出すときだけ、仮名を本名（呼び名）に戻す。 */
  function reveal(text, names) {
    let out = String(text == null ? '' : text);
    for (const n of names || []) {
      if (n && n.alias && n.call) out = out.split(n.alias).join(n.call);
    }
    return out;
  }

  EC.u = {
    h, svg, clear, add, sleep, uid, safeHref,
    isNum, ceilMin, floorMin, roundMin,
    verdictView, severityOf, worstResult,
    splitLabel, fillTemplate, formatDate, formatClock,
    pseudonymize, reveal,
  };
})();
