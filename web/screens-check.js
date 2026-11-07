/*
 * 画面: 点検（F-08・F-09・F-16・F-17）と改善（F-10・F-11）。
 * シナリオごとのカードを 1 枚ずつ表示し、地図とタイムラインで避難が止まる箇所を見せる。
 * 数値（分）は、サーバーのコードが算出した結果（ScenarioResult）だけを表示する。この画面では計算しない。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const A = EC.app;
  const { h, clear, sleep, verdictView, severityOf, worstResult, isNum, floorMin, fillTemplate } = EC.u;
  const ui = EC.ui;
  const L = EC.L;
  const S = EC.speech;
  const api = EC.api;

  const clone = (x) => JSON.parse(JSON.stringify(x));

  // ---------------------------------------------------------------------------
  // 計画・結果の読み取り
  // ---------------------------------------------------------------------------

  /** 改善案。画面に出すのは、決まった種類（FIX_TYPES）だけ。承認ずみは除く。最大 3 つ。 */
  function fixesFor(plan, finding) {
    const approved = new Set((plan.approvals || []).map((a) => a.fix_id));
    const field = plan.fields && plan.fields.shelter && plan.fields.shelter.value;
    const currentShelter = field && typeof field === 'object' ? field.shelter_id : null;
    return (plan.fixes || [])
      .filter((f) => f.finding_id === finding.finding_id && L.FIX_TYPES[f.fix_type] && !approved.has(f.fix_id) && f.result
        && !(f.fix_type === 'CHANGE_SHELTER' && currentShelter && f.target_id === currentShelter))
      .sort((a, b) => (b.saved_min || 0) - (a.saved_min || 0))
      .slice(0, 3);
  }

  function isImprovable(plan, finding) {
    const key = verdictView(worstResult(finding.result)).key;
    return key !== 'ok' && fixesFor(plan, finding).length > 0;
  }

  function currentShelterName(plan, T) {
    const f = plan.fields && plan.fields.shelter;
    return ui.isFilled(f) ? ui.valueText(f.value, T) : '';
  }

  function explanationOf(plan, finding, T) {
    if (finding.explanation) return finding.explanation;
    return fillTemplate(finding.explanation_template, finding.result, { shelter_name: currentShelterName(plan, T) });
  }

  function fixExplanation(plan, fix, shelterName) {
    if (fix.explanation) return fix.explanation;
    const extra = { shelter_name: shelterName || '', saved_min: isNum(fix.saved_min) ? String(floorMin(fix.saved_min)) : '？' };
    return fillTemplate(fix.explanation_template, fix.result, extra);
  }

  function shortSpeech(plan, finding) {
    const v = verdictView(worstResult(finding.result));
    return `${ui.scenarioTopic(plan, finding)}は、${v.label}。${v.detail ? `${v.detail}。` : ''}`;
  }

  function targetName(plan, fix, facts) {
    if (fix.fix_type === 'CHANGE_SHELTER') {
      const sh = ui.findShelter(plan, facts, fix.target_id);
      return sh ? sh.name : '';
    }
    if (fix.fix_type === 'ADD_CALLER') {
      const sup = (plan.supporters || []).find((s) => s.supporter_id === fix.target_id);
      return sup ? sup.alias : '';
    }
    return '';
  }

  /** 改善案を反映して計算し直した結果（コードが算出）の説明の文。数値は、結果の値だけを使う。 */
  function afterSentence(result) {
    const r = worstResult(result);
    const v = verdictView(r);
    const need = isNum(result.need_min) ? `${EC.u.ceilMin(result.need_min)}分` : null;
    const avail = isNum(result.avail_min) ? `${floorMin(result.avail_min)}分` : null;
    const head = '改善案を反映して、計算し直しました。';
    const times = need && avail ? `避難にかかる時間は${need}、津波が来るまで${avail}で、` : '';
    if (v.key === 'ok') return `${head}${times}間に合います${v.detail ? `（${v.detail}）` : ''}。`;
    if (v.key === 'tight') return `${head}${times}余裕が少ない状態です${v.detail ? `（${v.detail}）` : ''}。`;
    if (v.key === 'short') return `${head}${times}${v.detail ? `まだ${v.detail}です。` : 'まだ間に合いません。'}`;
    return `${head}計算できませんでした。`;
  }

  /** 承認の直後に、サーバーが計算し直した結果（approve の results）を、点検の結果に反映する。 */
  function applyResults(rec, prevFindings, results) {
    rec.ui = rec.ui || {};
    rec.ui.prevFindings = prevFindings;
    rec.ui.afterText = rec.ui.afterText || {};
    for (const f of rec.plan.findings || []) {
      const next = results[f.scenario_id];
      if (!next) continue;
      const before = prevFindings.find((x) => x.finding_id === f.finding_id);
      f.result = next;
      if (!before || JSON.stringify(before.result) !== JSON.stringify(next)) rec.ui.afterText[f.finding_id] = afterSentence(next);
    }
    rec.versions = rec.versions || [];
    rec.versions.push({ label: '改善後', at: new Date().toISOString(), plan: clone(rec.plan) });
  }

  // ---------------------------------------------------------------------------
  // 点検
  // ---------------------------------------------------------------------------

  A.register('check', ({ params, scope }) => {
    const rec = A.loadSessionRecord() || A.state.rec;
    if (!rec) { A.go('home', {}, { replace: true }); return; }
    A.state.rec = rec;
    A.setBack('talk');
    const T = A.T;

    const progressSlot = h('div', { class: 'progress-slot' });
    const improvedSlot = h('div', { class: 'improved-slot' });
    const summarySlot = h('div', { class: 'summary-slot' });
    const factsSlot = h('div', { class: 'facts-slot' });
    const premisesSlot = h('div', { class: 'premises-slot' });
    const introSlot = h('div', { class: 'intro-slot' });
    const cardsSlot = h('div', { class: 'cards' });
    A.$main.append(h('h1', null, `${A.personLabel(rec)}の点検`), introSlot, progressSlot, improvedSlot, summarySlot, cardsSlot, premisesSlot, factsSlot, A.noticeSafety());

    const cards = new Map(); // finding_id → { el, setOpen, resultKey }
    let openCard = null;
    let revealToken = 0;
    let viewPlan = rec.plan; // 画面に出している計画（点検の実行中は、サーバーから届いた途中の計画）

    // --- 結果の表示 -----------------------------------------------------------

    const bodyText = (finding) => (rec.ui && rec.ui.afterText && rec.ui.afterText[finding.finding_id]) || explanationOf(viewPlan, finding, T);

    function prevResult(scenarioId) {
      const versions = rec.versions || [];
      if (versions.length < 2) return null;
      const prev = versions[versions.length - 2].plan;
      const f = (prev.findings || []).find((x) => x.scenario_id === scenarioId);
      return f ? f.result : null;
    }

    function assumptionsBlock(result) {
      const items = [];
      for (const id of result.assumptions || []) {
        const info = ui.sourceInfo(viewPlan, id);
        if (info.source) items.push(h('li', null, ui.chip(info), T(info.source.label)));
      }
      if (!items.length) return null;
      const list = h('ul', { class: 'assumptions', hidden: true }, ...items);
      const toggle = h('button', { type: 'button', class: 'btn-row', 'aria-expanded': 'false' }, `使った仮定と出典（${items.length}件）`);
      toggle.addEventListener('click', () => {
        const open = toggle.getAttribute('aria-expanded') === 'true';
        toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
        list.hidden = open;
      });
      return h('div', { class: 'assumptions-block' }, toggle, list);
    }

    function buildBody(finding) {
      const result = finding.result;
      const wr = worstResult(result);
      const v = verdictView(wr);
      // 図（タイムライン）を先に出す。スマートフォンの 1 画面で、津波の線と不足が見えるように
      const parts = [EC.timeline.render(result)];
      parts.push(h('p', { class: 'explain' }, T(bodyText(finding))));
      if (finding.scenario_id === 'S8') {
        const premise = (viewPlan.premises || []).find((p) => p.code === finding.hole_type) || (viewPlan.premises || [])[0];
        if (premise) parts.push(h('p', null, '話の中の言葉: ', h('span', { class: 'quote' }, `「${T(premise.quote)}」`)));
      }
      if (finding.hole_type && L.HOLE_TYPES[finding.hole_type]) {
        parts.push(h('p', { class: 'sub' }, `問題点の種類: ${L.HOLE_TYPES[finding.hole_type]}`));
      }
      const before = prevResult(finding.scenario_id);
      if (before && verdictView(worstResult(before)).key !== v.key) {
        const bv = verdictView(worstResult(before));
        parts.push(h('p', { class: 'change-line' }, `前回: ${bv.mark} ${bv.text}　→　今回: ${v.mark} ${v.text}`));
      }
      if (result.supporter_verdict) {
        const sv = verdictView({ verdict: result.supporter_verdict, margin_min: result.supporter_margin_min });
        parts.push(h('p', { class: 'change-line' }, `避難支援者（${T(((viewPlan.supporters || [])[0] || {}).alias || '')}）: ${sv.mark} ${sv.text}`));
      }
      const m = ui.scenarioMap(result, { plan: viewPlan, facts: A.state.facts, T });
      scope.onLeave(() => m.map.destroy());
      parts.push(m.el);
      const assumptions = assumptionsBlock(result);
      if (assumptions) parts.push(assumptions);
      if (!fixesFor(viewPlan, finding).length && v.key !== 'ok') {
        parts.push(h('p', { class: 'strong' }, 'この問題点に使える改善案は、ありません。ご本人・ご家族・市と相談してください。'));
      }
      return parts;
    }

    function buildCard(finding, isNew) {
      const wr = worstResult(finding.result);
      const v = verdictView(wr);
      const body = h('div', { class: 'scen-body', hidden: true });
      const head = h('button', { type: 'button', class: 'scen-head', 'aria-expanded': 'false' },
        h('span', { class: 'scen-title' }, ui.scenarioTitle(viewPlan, finding)),
        ui.verdictBadge(wr));
      const el = h('article', { class: `scen scen-${v.key}${isNew ? ' is-new' : ''}`, dataset: { scenario: finding.scenario_id } }, head, body);
      const card = {
        el,
        built: false,
        setOpen(open) {
          if (open && !card.built) {
            card.built = true;
            body.append(...buildBody(finding));
          }
          body.hidden = !open;
          head.setAttribute('aria-expanded', open ? 'true' : 'false');
        },
      };
      head.addEventListener('click', () => {
        const willOpen = head.getAttribute('aria-expanded') !== 'true';
        if (willOpen && openCard && openCard !== card) openCard.setOpen(false);
        card.setOpen(willOpen);
        openCard = willOpen ? card : null;
        if (willOpen) A.say(bodyText(finding)); // 読み上げには、仮名のままの文を渡す（声の種類によって、本名に戻すかを決める）
      });
      cards.set(finding.finding_id, card);
      return card;
    }

    /** カードを加える。同じ問題点のカードが、違う結果で出ているときは、作り直す。 */
    function addCard(finding, isNew) {
      const key = JSON.stringify(finding.result);
      const old = cards.get(finding.finding_id);
      if (old && old.resultKey === key) return old;
      const card = buildCard(finding, isNew);
      card.resultKey = key;
      if (old) {
        old.el.replaceWith(card.el);
        if (openCard === old) openCard = null;
      } else {
        cardsSlot.append(card.el);
      }
      return card;
    }

    function summaryNode(findings) {
      const count = { short: 0, tight: 0, ok: 0, unknown: 0 };
      for (const f of findings) count[verdictView(worstResult(f.result)).key] += 1;
      const row = (key, mark, label) => h('div', { class: `count-row count-${key}` },
        h('span', { class: 'mark', 'aria-hidden': 'true' }, mark), h('span', { class: 'count-label' }, label), h('span', { class: 'count-num' }, String(count[key])));
      const box = h('div', { class: 'card summary-card' },
        h('p', { class: 'strong' }, `点検したシナリオ（厳しい条件）: ${findings.length}つ`),
        row('short', '×', '間に合わない'), row('tight', '△', '余裕が少ない'), row('ok', '○', '間に合う'),
        count.unknown ? row('unknown', '？', '計算できない') : null,
        h('p', { class: 'sub' }, '時間は、すべて、揺れてからの分です。'));
      if (findings.length && count.short === findings.length) {
        // E5: すべてのシナリオで間に合わない
        box.append(h('p', { class: 'strong' }, 'この方は、現在の条件では間に合いません。'),
          h('p', null, '市や家族と話し合うための資料として、どこで何分足りないかを印刷できます。'));
      }
      return box;
    }

    function renderPremises() {
      clear(premisesSlot);
      const list = viewPlan.premises || [];
      if (!list.length) return;
      premisesSlot.append(h('h2', null, '話の中にだけあった前提条件'),
        h('p', { class: 'sub' }, '様式の欄には書かれていない、聞き取りの中の話です。点検の前提に使いました。'));
      for (const p of list) {
        premisesSlot.append(h('div', { class: 'premise' },
          h('span', { class: 'premise-code' }, L.PREMISE_CODES[p.code] || p.code),
          h('span', { class: 'quote' }, `「${T(p.quote)}」`)));
      }
    }

    function setResultActions() {
      const findings = rec.plan.findings || [];
      const target = findings.find((f) => isImprovable(rec.plan, f));
      if (target) {
        A.setActions([
          { id: 'fix', kind: 'primary', label: '改善案を見る', onClick: () => A.go('fix', { finding: target.finding_id }) },
          { id: 'verify', label: '確認へ進む', onClick: () => A.go('verify') },
        ]);
      } else {
        A.setActions([{ id: 'verify', kind: 'confirm', label: '確認へ進む', onClick: () => A.go('verify') }]);
      }
    }

    /** 概要・前提条件・場所の事実を出す。結果がそろってから呼ぶ。 */
    async function renderFixedParts() {
      await A.ensureFacts(rec);
      if (!scope.alive) return false;
      clear(factsSlot);
      factsSlot.append(ui.factsPanel(A.state.facts.facts));
      renderPremises();
      clear(summarySlot);
      const findings = rec.plan.findings || [];
      if (findings.length) summarySlot.append(summaryNode(findings));
      else summarySlot.append(h('div', { class: 'card' }, h('p', null, '問題点は見つかりませんでした。')));
      return true;
    }

    /** いちばん先に見せるカード（改善できる問題点の先頭。無ければ最初）を開き、下のボタンを出す。 */
    function finishResults() {
      const findings = rec.plan.findings || [];
      const target = findings.find((f) => isImprovable(rec.plan, f)) || findings[0];
      const card = target && cards.get(target.finding_id);
      if (card && openCard !== card) {
        if (openCard) openCard.setOpen(false);
        card.setOpen(true);
        openCard = card;
      }
      setResultActions();
    }

    /** 保存ずみの結果や、改善のあとの結果を、すぐに全部表示する。 */
    async function showResults() {
      const token = ++revealToken;
      clear(cardsSlot);
      clear(introSlot);
      cards.clear();
      openCard = null;
      viewPlan = rec.plan;
      for (const f of rec.plan.findings || []) if (f.result) addCard(f, false);
      if (!(await renderFixedParts()) || token !== revealToken) return;
      finishResults();
    }

    // --- 改善の結果（前 → 後） ---------------------------------------------------

    function showImprovement(prevFindings) {
      clear(improvedSlot);
      const approvals = rec.plan.approvals || [];
      const last = approvals[approvals.length - 1];
      if (!last) return;
      const fix = (rec.plan.fixes || []).find((f) => f.fix_id === last.fix_id);
      if (!fix) return;
      const finding = (rec.plan.findings || []).find((f) => f.finding_id === fix.finding_id);
      const before = (prevFindings || []).find((f) => f.finding_id === fix.finding_id);
      if (!finding || !before) return;
      const bv = verdictView(worstResult(before.result));
      const av = verdictView(worstResult(finding.result));
      improvedSlot.append(h('div', { class: 'result-change' },
        h('p', { class: 'strong' }, '改善の結果'),
        h('p', null, ui.scenarioTitle(rec.plan, finding)),
        h('p', { class: 'sub' }, '改善前'),
        ui.verdictBadge(worstResult(before.result)),
        h('p', { class: 'arrow' }, `↓ ${L.FIX_TYPES[fix.fix_type] || ''}`),
        h('p', { class: 'sub' }, '改善後'),
        ui.verdictBadge(worstResult(finding.result))));
      A.say(`${ui.scenarioTopic(rec.plan, finding)}は、${bv.label}から、${av.label}になりました。${av.detail ? `${av.detail}。` : ''}`);
    }

    // --- 点検の実行 -----------------------------------------------------------

    function fail(e, retry) {
      clear(progressSlot);
      progressSlot.append(A.errorBox(A.errorText(e)));
      A.setActions([
        { id: 'retry', kind: 'primary', label: 'もう一度', onClick: retry },
        { id: 'back', label: '話す画面にもどる', onClick: () => A.go('talk') },
      ]);
    }

    async function runCheck(recheck) {
      clear(progressSlot);
      clear(improvedSlot);
      clear(cardsSlot);
      clear(summarySlot);
      clear(factsSlot);
      clear(premisesSlot);
      clear(introSlot);
      cards.clear();
      openCard = null;
      const token = ++revealToken;
      const prevFindings = clone(rec.plan.findings || []);
      viewPlan = rec.plan;
      A.setActions([{ id: 'run', kind: 'primary', label: '点検しています', disabled: true }]);
      const box = A.statusBox(recheck ? 'もう一度、点検しています' : '点検の準備をしています');
      const log = h('ul', { class: 'event-log' });
      progressSlot.append(box, log);
      A.announce(box.textContent);

      // 結果は、届いたものから 1 枚ずつ出し、読み上げる（U-03）。止めるボタンで、残りをすぐに表示する
      const queue = [];
      const queued = new Set();
      let draining = null;
      let skip = false;
      const stopButton = document.getElementById('btn-voice-stop');
      const onStop = () => { skip = true; };
      stopButton.addEventListener('click', onStop);
      scope.onLeave(() => stopButton.removeEventListener('click', onStop));
      const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

      const drain = async () => {
        while (queue.length && scope.alive && token === revealToken) {
          const f = queue.shift();
          const card = addCard(f, true);
          if (cards.size === 1) { card.setOpen(true); openCard = card; }
          if (card.el.scrollIntoView) card.el.scrollIntoView({ block: 'nearest', behavior: reduceMotion ? 'auto' : 'smooth' });
          if (!skip) {
            await Promise.race([A.say(shortSpeech(viewPlan, f)), sleep(6000)]);
            if (!skip) await sleep(250);
          }
        }
      };
      const enqueue = (list) => {
        for (const f of list || []) {
          if (!f || !f.finding_id || !f.result || !f.result.verdict || queued.has(f.finding_id)) continue;
          queued.add(f.finding_id);
          queue.push(f);
        }
        if (queue.length && !draining) draining = drain().finally(() => { draining = null; });
      };

      let jobId;
      try {
        jobId = await api.startCheck(rec.plan);
      } catch (e) {
        if (scope.alive) fail(e, () => runCheck(recheck));
        return;
      }
      let seen = 0;
      let lastProgress = Date.now();
      for (;;) {
        if (!scope.alive || token !== revealToken) return;
        let job;
        try {
          job = await api.getJob(jobId);
        } catch (e) {
          if (scope.alive) fail(e, () => runCheck(recheck));
          return;
        }
        if (!scope.alive || token !== revealToken) return;
        if (job.events && job.events.length > seen) {
          for (const ev of job.events.slice(seen)) log.append(h('li', null, ev));
          seen = job.events.length;
          lastProgress = Date.now();
          const latest = job.events[seen - 1];
          box.update(latest);
          A.announce(latest);
          if (!S.isSpeaking() && !draining && cards.size === 0) A.say(latest);
        }
        if (job.plan) {
          // 実行中でも、サーバーの計画に結果が入っていれば、すぐに出す
          viewPlan = A.mergeGeometry(job.plan, rec.plan);
          if (job.state !== 'failed') enqueue(viewPlan.findings);
        }
        if (job.state === 'done' && job.plan) {
          rec.plan = viewPlan;
          rec.ui = rec.ui || {};
          rec.ui.afterText = {}; // 新しい点検の説明を使う
          rec.versions = rec.versions || [];
          rec.versions.push({ label: recheck ? '改善後' : '点検', at: new Date().toISOString(), plan: clone(rec.plan) });
          A.save();
          clear(progressSlot);
          if (recheck) showImprovement(prevFindings);
          enqueue(rec.plan.findings);
          if (draining) await draining;
          if (!scope.alive || token !== revealToken) return;
          for (const f of rec.plan.findings || []) if (f.result) addCard(f, false); // 実行中のものと違う結果は、作り直す
          if (!(await renderFixedParts()) || token !== revealToken) return;
          finishResults();
          if (recheck) A.$main.scrollTop = 0;
          return;
        }
        if (job.state === 'failed') {
          fail({ userMessage: typeof job.error === 'string' && job.error.length < 120 ? job.error : undefined }, () => runCheck(recheck));
          return;
        }
        if (Date.now() - lastProgress > 60000) {
          fail({ userMessage: '時間がかかっています。もう一度、試してください。' }, () => runCheck(recheck));
          return;
        }
        await sleep(1200);
      }
    }

    // --- 画面を開いたとき -----------------------------------------------------------

    scope.onLeave(() => { revealToken += 1; });

    if (params.improved === '1') {
      // 改善案を承認した直後: サーバーが計算し直した結果を、すぐに出す
      window.history.replaceState(null, '', '#/check');
      showImprovement((rec.ui && rec.ui.prevFindings) || []);
      showResults().then(() => { if (scope.alive) A.$main.scrollTop = 0; });
    } else if (params.recheck === '1') {
      window.history.replaceState(null, '', '#/check');
      runCheck(true);
    } else if ((rec.plan.findings || []).length) {
      showResults();
    } else {
      introSlot.append(
        h('p', { class: 'lead' }, '津波が来る前に避難できるかを、厳しい条件で確かめます。'),
        h('p', null, 'たとえば、冬の深夜、家族がいない昼、道がふさがったとき、避難支援者が来られないときです。'),
        h('p', { class: 'sub' }, '数値は、プログラムのコードが計算します。AI は計算しません。'));
      A.setActions([{ id: 'run', kind: 'primary', label: '点検する', onClick: () => runCheck(false) }]);
    }
  });

  // ---------------------------------------------------------------------------
  // 改善
  // ---------------------------------------------------------------------------

  A.register('fix', ({ params, scope }) => {
    const rec = A.loadSessionRecord() || A.state.rec;
    if (!rec) { A.go('home', {}, { replace: true }); return; }
    A.state.rec = rec;
    A.setBack('check');
    const T = A.T;
    const plan = rec.plan;
    const finding = (plan.findings || []).find((f) => f.finding_id === params.finding) || (plan.findings || []).find((f) => isImprovable(plan, f));
    if (!finding) { A.go('verify', {}, { replace: true }); return; }
    const fixes = fixesFor(plan, finding);
    if (!fixes.length) { A.go('check', {}, { replace: true }); return; }

    let index = 0;
    let busy = false;
    const body = h('div', { class: 'fix-body' });
    A.$main.append(h('h1', null, `${A.personLabel(rec)}の改善案`), body, A.noticeSafety());

    async function renderFix() {
      const facts = await A.ensureFacts(rec);
      if (!scope.alive) return;
      clear(body);
      const fix = fixes[index];
      const wr = worstResult(finding.result);
      const name = targetName(plan, fix, facts);
      const title = `${L.FIX_TYPES[fix.fix_type]}${name ? `: ${T(name)}` : ''}`;
      const afterView = verdictView(worstResult(fix.result));

      const needs = [finding.result.need_min, fix.result.need_min, finding.result.avail_min, fix.result.avail_min].filter(isNum);
      const scale = needs.length ? Math.max(...needs) : null;
      const isShelterChange = fix.fix_type === 'CHANGE_SHELTER';
      const currentField = plan.fields && plan.fields.shelter && plan.fields.shelter.value;
      const currentId = currentField && typeof currentField === 'object' ? currentField.shelter_id : null;
      const m = ui.scenarioMap(fix.result, {
        plan, facts, T,
        shelterId: isShelterChange ? fix.target_id : undefined,
        oldShelterId: isShelterChange ? currentId : undefined,
      });
      scope.onLeave(() => m.map.destroy());

      body.append(
        h('div', { class: 'fix-card' },
          h('p', { class: 'fix-count' }, `案 ${index + 1}／${fixes.length}${isNum(fix.saved_min) ? `　${floorMin(fix.saved_min)}分、短くなります` : ''}`),
          h('p', { class: 'fix-title' }, title),
          h('p', { class: 'explain' }, T(fixExplanation(plan, fix, name))),
          h('p', { class: 'sub' }, `いまの点検の結果: ${ui.scenarioTitle(plan, finding)}`),
          ui.verdictBadge(wr),
          h('p', { class: 'arrow' }, '↓ この案にすると'),
          ui.verdictBadge(worstResult(fix.result))),
        h('h2', null, 'いまと、この案を比べる'),
        h('div', { class: 'compare' },
          EC.timeline.render(finding.result, { scale, title: 'いま' }),
          EC.timeline.render(fix.result, { scale, title: 'この案にすると', footnote: false })),
        h('h2', null, '地図'),
        m.el,
        h('div', { class: 'notice' }, h('p', null, '選ぶのは、民生委員と本人です。AI は決めません。選ぶまで、計画は変わりません。')));
      A.announce(`案 ${index + 1}。${title}`);
      A.say(`案${index + 1}。${fixExplanation(plan, fix, name)}。この案にすると、${afterView.text}。`);
      refreshActions();
    }

    function refreshActions() {
      const list = [{ id: 'choose', kind: 'confirm', label: 'これにする', disabled: busy, onClick: choose }];
      if (fixes.length > 1) list.push({ id: 'other', label: 'ほかの案を見る', disabled: busy, onClick: () => { index = (index + 1) % fixes.length; renderFix(); A.$main.scrollTop = 0; } });
      A.setActions(list);
    }

    async function choose() {
      if (busy) return;
      busy = true;
      S.cancel();
      refreshActions();
      const box = A.statusBox('計画を変更しています');
      body.prepend(box);
      try {
        const prevFindings = clone(rec.plan.findings || []);
        const res = await api.approve(rec.plan, fixes[index].fix_id, 'minsei');
        if (!scope.alive) return;
        rec.plan = A.mergeGeometry(res.plan, rec.plan);
        if (res.results) {
          // サーバーが、承認の直後にコードで計算し直した結果。すぐに点検の画面へ出す（もう一度、Claude に点検させない）
          applyResults(rec, prevFindings, res.results);
          A.save();
          A.go('check', { improved: '1' });
        } else {
          A.save();
          A.go('check', { recheck: '1' });
        }
      } catch (e) {
        if (!scope.alive) return;
        busy = false;
        box.replaceWith(A.errorBox(A.errorText(e)));
        refreshActions();
      }
    }

    renderFix();
  });

  EC.checkScreen = { fixesFor, isImprovable, explanationOf };
})();
