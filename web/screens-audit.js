/*
 * 画面: 記録（AC-02）。エージェントが計画に書き込もうとしたとき、サーバーのコード（フック）が、
 * 出典・引用・数値・承認を確認する。止めた書き込みを、時刻・エージェント・理由で一覧にする（監査ログ）。
 * サーバーの GET /api/audit?plan_id= の entries を表示する。サーバーが無いときは、サンプルの記録を出す。
 * 記録の判定（decision）は、deny（フックが止めた）・result（通した）・error（ツールが失敗・拒否した）・info（記録）。
 * args と result は、短くした JSON の文字（途中で切れていることがある）。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const A = EC.app;
  const { h, clear, splitLabel } = EC.u;
  const api = EC.api;
  const L = EC.L;

  const AGENTS = {
    interview: '聞き取りエージェント（AI）',
    redteam: 'レッドチームエージェント（AI。計画の弱点を探す）',
    verify: '検証エージェント（AI）',
    server: '民生委員の操作（完成など）',
  };

  // 書き込みの種類（MCP ツールの名前）の表示名
  const TOOLS = {
    'plan.set_fields': '欄への書き込み',
    'plan.set_supporter': '避難支援者の登録',
    'plan.add_premise': '前提条件の追加',
    'plan.add_finding': '問題点の追加',
    'plan.propose_fix': '改善案の提案',
    'plan.apply_fix': '改善案の反映',
    'plan.finalize': '完成にする',
    'plan.get_plan': '計画の読み取り',
    'sim.run_scenario': 'シナリオの計算',
    'sim.try_fix': '改善案の計算',
    'hazard.find_shelters': '避難先の検索',
    Skill: 'Skill の読み込み',
    StructuredOutput: '結果の提出',
    agent: 'エージェントの実行',
  };

  const DECISIONS = { stop: '中止', fail: '失敗', ok: '問題なし', info: '記録' };

  /** 記録の種類。フックが止めた（deny）ほか、計算のコードが危険な案を断った（error と理由）ものも「止めた」に入れる。 */
  function kindOf(entry) {
    if (entry.decision === 'deny') return 'stop';
    if (entry.decision === 'error') return entry.reason && entry.tool !== 'agent' ? 'stop' : 'fail';
    if (entry.decision === 'info') return 'info';
    return 'ok';
  }

  /** 時刻（ISO の文字、または秒・ミリ秒の数）を「時:分:秒」にする。 */
  function clock(at) {
    let d = null;
    if (typeof at === 'number') d = new Date(at < 1e12 ? at * 1000 : at);
    else if (typeof at === 'string') d = new Date(at);
    if (!d || Number.isNaN(d.getTime())) return '';
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  /** 引数。オブジェクトのほか、短くした JSON の文字でも受ける（途中で切れていれば、欄・シナリオ・改善案の種類だけ拾う）。 */
  function argsOf(entry) {
    const raw = entry.args;
    if (raw && typeof raw === 'object') return raw;
    if (typeof raw !== 'string' || !raw) return {};
    try {
      const v = JSON.parse(raw);
      if (v && typeof v === 'object') return v;
    } catch (e) { /* 長いとき、途中で切れている */ }
    const pick = (key) => { const m = new RegExp(`"${key}":\\s*"([^"]+)"`).exec(raw); return m ? m[1] : null; };
    return { field: pick('field'), scenario_id: pick('scenario_id'), fix_type: pick('fix_type') };
  }

  /** 書き込もうとした欄・シナリオ・改善案の名前。引数の形が違うときは、出さない。 */
  function targetText(entry) {
    const args = argsOf(entry);
    let key = typeof args.field === 'string' ? args.field : null;
    if (!key && Array.isArray(args.items) && args.items[0] && typeof args.items[0].field === 'string') key = args.items[0].field;
    if (key && L.FIELDS[key]) return `（${splitLabel(L.FIELDS[key]).title}）`;
    if (typeof args.fix_type === 'string' && L.FIX_TYPES[args.fix_type]) return `（${L.FIX_TYPES[args.fix_type]}）`;
    if (typeof args.scenario_id === 'string' && L.SCENARIOS[args.scenario_id]) return `（${L.SCENARIOS[args.scenario_id]}）`;
    return '';
  }

  /** 理由の文に出てくる欄の英語のキー（mobility など）を、画面の欄の名前にする。 */
  const FIELD_KEYS = Object.keys(L.FIELDS).sort((a, b) => b.length - a.length);
  function plain(text) {
    let out = String(text);
    for (const key of FIELD_KEYS) out = out.replace(new RegExp(`\\b${key}\\b`, 'g'), splitLabel(L.FIELDS[key]).title);
    // 説明文の数値の差し込み（{need_min} などのプレースホルダー）は、変数の名前を出さずに言い換える
    out = out.replace(/数値は\s*\{\w+\}\s*などの?プレースホルダーで書いてください/g, '数値は、コードが計算した値が入る形で書いてください');
    return out.replace(/\{\w+\}/g, '（値）');
  }

  /** 1 件。止めた記録は、印（×）と文字（止めた）を付け、色だけで伝えない。 */
  function entryNode(entry, T) {
    const kind = kindOf(entry);
    const deny = kind === 'stop';
    const who = AGENTS[entry.agent] || (entry.agent ? String(entry.agent) : '');
    const what = TOOLS[entry.tool] || (entry.tool ? String(entry.tool) : '');
    // 見出しを付けた行にして、何を・だれが・なぜ、を読み分けられるようにする
    const row = (label, text) => h('p', null, h('strong', null, `${label}　`), text);
    return h('div', { class: `audit-entry${deny ? ' is-deny' : ''}${kind === 'fail' ? ' is-fail' : ''}` },
      h('p', { class: 'audit-head' },
        h('span', { class: 'mark', 'aria-hidden': 'true' }, EC.icon(deny ? 'cross' : (kind === 'fail' ? 'exclam' : 'check'), { size: 20, stroke: 3 })),
        h('span', null, DECISIONS[kind]),
        h('span', { class: 'audit-time' }, clock(entry.at))),
      what ? row(deny ? '中止したこと' : '確かめたこと', `${what}${targetText(entry)}`) : null,
      who ? row('だれが', who) : null,
      (deny || kind === 'fail') && entry.reason ? h('p', { class: 'audit-reason' }, h('strong', null, '理由　'), T(plain(entry.reason))) : null);
  }

  A.register('audit', ({ params, scope }) => {
    const rec = A.loadSessionRecord() || A.state.rec;
    if (!rec) { A.go('home', {}, { replace: true }); return; }
    A.state.rec = rec;
    const from = ['talk', 'check', 'verify', 'print'].includes(params.from) ? params.from : ((rec.plan.findings || []).length ? 'check' : 'talk');
    A.setBack(from);
    const T = A.T;

    let showAll = false;
    let entries = [];
    const statusSlot = h('div');
    const summarySlot = h('div');
    const listSlot = h('div', { class: 'audit-list' });
    const switchRow = h('button', { type: 'button', class: 'switch-row', role: 'switch', 'aria-checked': 'false' },
      h('span', { class: 'switch-box', 'aria-hidden': 'true' }),
      h('span', null, '問題がなかった書き込みも見る'),
      h('span', { class: 'switch-state' }, '切'));

    // 目的（何のための記録か）と、中止する書き込みの種類を、一覧より先に出す
    EC.u.add(A.$main,
      h('h1', null, 'AI の書き込みを中止した記録'),
      h('p', { class: 'lead' }, 'AI（Claude）が計画に書き込む前に、プログラムが中身を確かめた記録です。'),
      h('p', null, '根拠のない書き込み、発言の原文にない引用、説明文に直接書いた数値、承認のない変更、危険な改善案は、書き込みを中止して、理由を AI に返します。AI は、直してから書き込み直します。'),
      statusSlot, summarySlot, switchRow, listSlot,
      A.noticeSafety());

    function render() {
      clear(summarySlot);
      clear(listSlot);
      const denies = entries.filter((e) => kindOf(e) === 'stop');
      summarySlot.append(h('div', { class: 'card audit-summary' },
        h('span', { class: 'audit-big', 'aria-hidden': 'true' }, String(denies.length)),
        h('div', null,
          h('p', { class: 'strong' }, `中止した書き込み: ${denies.length}件`),
          h('p', { class: 'sub' }, `プログラムが確かめた書き込み: ${entries.length}件`))));
      // 新しい順。同じ秒の記録は、あとから書かれたものを先に出す
      const shown = (showAll ? entries : denies).map((e, i) => ({ e, i }))
        .sort((a, b) => ((Date.parse(b.e.at) || 0) - (Date.parse(a.e.at) || 0)) || (b.i - a.i))
        .map((x) => x.e);
      if (!shown.length) listSlot.append(h('p', null, showAll ? 'まだ記録がありません。' : '中止した書き込みは、まだありません。AI の書き込みと改善案は、プログラムの確認をすべて通りました。'));
      for (const e of shown) listSlot.append(entryNode(e, T));
    }

    switchRow.addEventListener('click', () => {
      showAll = !showAll;
      switchRow.setAttribute('aria-checked', String(showAll));
      switchRow.querySelector('.switch-state').textContent = showAll ? '入' : '切';
      render();
    });

    async function load() {
      clear(statusSlot);
      statusSlot.append(A.statusBox('記録を読み込んでいます', 'busy'));
      A.setActions([{ id: 'reload', kind: 'primary', label: '読み込んでいます', disabled: true }]);
      try {
        entries = await api.audit(rec.plan.plan_id);
        if (!scope.alive) return;
        clear(statusSlot);
        render();
      } catch (e) {
        if (!scope.alive) return;
        clear(statusSlot);
        statusSlot.append(A.errorBox(A.errorText(e)));
      }
      A.setActions([{ id: 'reload', kind: 'primary', icon: 'swap', label: '記録を読み込み直す', onClick: load }]);
    }

    load();
  });

  EC.auditScreen = { clock, entryNode };
})();
