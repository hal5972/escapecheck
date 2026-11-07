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
    return (plan.fixes || [])
      .filter((f) => f.finding_id === finding.finding_id && L.FIX_TYPES[f.fix_type] && !approved.has(f.fix_id) && f.result)
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
    return `${ui.scenarioTitle(plan, finding)}は、${v.label}。${v.detail ? `${v.detail}。` : ''}`;
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

  async function ensureFacts(rec) {
    if (A.state.facts) return A.state.facts;
    const f = await api.facts(rec.plan);
    A.state.facts = f || { facts: null, shelters: [], sources: {} };
    return A.state.facts;
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
    A.$main.append(h('h1', null, '点検'), introSlot, progressSlot, improvedSlot, summarySlot, factsSlot, premisesSlot, cardsSlot, A.noticeSafety());

    const cards = new Map(); // finding_id → { el, setOpen }
    let openCard = null;
    let revealToken = 0;

    // --- 結果の表示 -----------------------------------------------------------

    function prevResult(scenarioId) {
      const versions = rec.versions || [];
      if (versions.length < 2) return null;
      const prev = versions[versions.length - 2].plan;
      const f = (prev.findings || []).find((x) => x.scenario_id === scenarioId);
      return f ? f.result : null;
    }

    function assumptionsBlock(result) {
      const ul = h('ul', { class: 'assumptions' });
      for (const id of result.assumptions || []) {
        const info = ui.sourceInfo(rec.plan, id);
        if (!info.source) continue;
        ul.append(h('li', null, ui.chip(info), T(info.source.label)));
      }
      if (!ul.firstChild) return null;
      return h('div', null, h('p', { class: 'strong' }, '使った仮定と出典'), ul);
    }

    function buildBody(finding) {
      const result = finding.result;
      const wr = worstResult(result);
      const v = verdictView(wr);
      const parts = [h('p', { class: 'explain' }, T(explanationOf(rec.plan, finding, T)))];
      if (finding.hole_type && L.HOLE_TYPES[finding.hole_type]) {
        parts.push(h('p', { class: 'sub' }, `問題点の種類: ${L.HOLE_TYPES[finding.hole_type]}`));
      }
      const before = prevResult(finding.scenario_id);
      if (before && verdictView(worstResult(before)).key !== v.key) {
        const bv = verdictView(worstResult(before));
        parts.push(h('p', { class: 'change-line' }, `前回: ${bv.mark} ${bv.text}　→　今回: ${v.mark} ${v.text}`));
      }
      parts.push(EC.timeline.render(result));
      if (result.supporter_verdict) {
        const sv = verdictView({ verdict: result.supporter_verdict, margin_min: result.supporter_margin_min });
        parts.push(h('p', { class: 'change-line' }, `避難支援者（${T((rec.plan.supporters[0] || {}).alias || '')}）: ${sv.mark} ${sv.text}`));
      }
      const m = ui.scenarioMap(result, { plan: rec.plan, facts: A.state.facts, T });
      scope.onLeave(() => m.map.destroy());
      parts.push(m.el);
      const assumptions = assumptionsBlock(result);
      if (assumptions) parts.push(assumptions);
      if (!fixesFor(rec.plan, finding).length && v.key !== 'ok') {
        parts.push(h('p', { class: 'strong' }, 'この問題点に使える改善案は、ありません。ご本人・ご家族・市と相談してください。'));
      }
      return parts;
    }

    function buildCard(finding, isNew) {
      const wr = worstResult(finding.result);
      const v = verdictView(wr);
      const body = h('div', { class: 'scen-body', hidden: true });
      const head = h('button', { type: 'button', class: 'scen-head', 'aria-expanded': 'false' },
        h('span', { class: 'scen-title' }, ui.scenarioTitle(rec.plan, finding)),
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
        if (willOpen) A.say(`${T(explanationOf(rec.plan, finding, T))}`);
      });
      cards.set(finding.finding_id, card);
      return card;
    }

    function summaryNode(findings) {
      const count = { short: 0, tight: 0, ok: 0, unknown: 0 };
      for (const f of findings) count[verdictView(worstResult(f.result)).key] += 1;
      const box = h('div', { class: 'card summary-card' },
        h('p', { class: 'strong' }, `点検したシナリオ: ${findings.length}つ`),
        h('p', null, `× 間に合わない ${count.short}　△ 余裕が少ない ${count.tight}　○ 間に合う ${count.ok}`,
          count.unknown ? `　？ 計算できない ${count.unknown}` : ''));
      if (findings.length && count.short === findings.length) {
        // E5: すべてのシナリオで間に合わない
        box.append(h('p', { class: 'strong' }, 'この人は、現在の条件では間に合いません。'),
          h('p', null, '市や家族と話し合うための資料として、どこで何分足りないかを印刷できます。'));
      }
      return box;
    }

    function renderPremises() {
      clear(premisesSlot);
      const list = rec.plan.premises || [];
      if (!list.length) return;
      premisesSlot.append(h('h2', null, '話の中にだけあった条件'),
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

    async function showResults(animate) {
      const findings = rec.plan.findings || [];
      clear(cardsSlot);
      clear(summarySlot);
      clear(introSlot);
      cards.clear();
      openCard = null;
      const token = ++revealToken;

      await ensureFacts(rec);
      if (!scope.alive || token !== revealToken) return;
      clear(factsSlot);
      factsSlot.append(ui.factsPanel(A.state.facts.facts));
      renderPremises();

      if (!findings.length) {
        summarySlot.append(h('div', { class: 'card' }, h('p', null, '問題点は見つかりませんでした。')));
        setResultActions();
        return;
      }
      summarySlot.append(summaryNode(findings));

      const firstOpen = findings.find((f) => isImprovable(rec.plan, f)) || findings[0];
      if (!animate) {
        for (const f of findings) cards.get(f.finding_id) || cardsSlot.append(buildCard(f, false).el);
        const c = cards.get(firstOpen.finding_id);
        c.setOpen(true);
        openCard = c;
        setResultActions();
        return;
      }

      // 1 枚ずつ表示し、読み上げる（U-03）。止めるボタンで、残りをすぐに表示する
      A.setActions([{ id: 'wait', kind: 'primary', label: '結果を表示しています', disabled: true }]);
      let skip = false;
      const stopHandler = () => { skip = true; };
      document.getElementById('btn-voice-stop').addEventListener('click', stopHandler);
      scope.onLeave(() => document.getElementById('btn-voice-stop').removeEventListener('click', stopHandler));
      for (let i = 0; i < findings.length; i++) {
        if (!scope.alive || token !== revealToken) return;
        const f = findings[i];
        const card = buildCard(f, true);
        cardsSlot.append(card.el);
        if (i === 0) { card.setOpen(true); openCard = card; }
        if (card.el.scrollIntoView) card.el.scrollIntoView({ block: 'nearest', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
        if (!skip) {
          await Promise.race([A.say(shortSpeech(rec.plan, f)), sleep(6000)]);
          if (!skip) await sleep(250);
        }
      }
      if (!scope.alive || token !== revealToken) return;
      const first = cards.get(firstOpen.finding_id);
      if (first && openCard !== first) {
        if (openCard) openCard.setOpen(false);
        first.setOpen(true);
        openCard = first;
      }
      setResultActions();
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
      A.say(`${ui.scenarioTitle(rec.plan, finding)}は、${bv.label}から、${av.label}になりました。${av.detail ? `${av.detail}。` : ''}`);
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
      revealToken += 1;
      const prevFindings = clone(rec.plan.findings || []);
      A.setActions([{ id: 'run', kind: 'primary', label: '点検しています', disabled: true }]);
      const box = A.statusBox(recheck ? 'もう一度、点検しています' : '点検の準備をしています');
      const log = h('ul', { class: 'event-log' });
      progressSlot.append(box, log);
      A.announce(box.textContent);

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
        if (!scope.alive) return;
        let job;
        try {
          job = await api.getJob(jobId);
        } catch (e) {
          if (scope.alive) fail(e, () => runCheck(recheck));
          return;
        }
        if (!scope.alive) return;
        if (job.events && job.events.length > seen) {
          for (const ev of job.events.slice(seen)) log.append(h('li', null, ev));
          seen = job.events.length;
          lastProgress = Date.now();
          const latest = job.events[seen - 1];
          box.update(latest);
          A.announce(latest);
          if (!S.isSpeaking()) A.say(latest);
        }
        if (job.state === 'done' && job.plan) {
          rec.plan = A.mergeGeometry(job.plan, rec.plan);
          rec.versions = rec.versions || [];
          rec.versions.push({ label: recheck ? '改善後' : '点検', at: new Date().toISOString(), plan: clone(rec.plan) });
          A.save();
          clear(progressSlot);
          S.cancel();
          if (recheck) showImprovement(prevFindings);
          await showResults(true);
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

    if (params.recheck === '1') {
      window.history.replaceState(null, '', '#/check');
      runCheck(true);
    } else if ((rec.plan.findings || []).length) {
      showResults(false);
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
    A.$main.append(h('h1', null, '改善案'), body, A.noticeSafety());

    async function renderFix() {
      const facts = await ensureFacts(rec);
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
          h('p', { class: 'fix-count' }, `案 ${index + 1}／${fixes.length}`),
          h('p', { class: 'fix-title' }, title),
          h('p', { class: 'explain' }, T(fixExplanation(plan, fix, name))),
          isNum(fix.saved_min) ? h('p', { class: 'saved-line' }, `${floorMin(fix.saved_min)}分、短くなります`) : null,
          h('p', { class: 'strong' }, 'この案にすると'),
          ui.verdictBadge(worstResult(fix.result))),
        h('div', { class: 'card' },
          h('p', { class: 'strong' }, '直したい点検の結果'),
          h('p', null, ui.scenarioTitle(plan, finding)),
          ui.verdictBadge(wr)),
        h('h2', null, 'いまと、この案の比べ方'),
        h('div', { class: 'compare' },
          EC.timeline.render(finding.result, { scale, title: 'いま' }),
          EC.timeline.render(fix.result, { scale, title: 'この案にすると' })),
        h('h2', null, '地図'),
        m.el,
        h('div', { class: 'notice' }, h('p', null, '選ぶのは、民生委員と本人です。AI は決めません。選ぶまで、計画は変わりません。')));
      A.announce(`案 ${index + 1}。${title}`);
      A.say(`案${index + 1}。${T(fixExplanation(plan, fix, name))}。この案にすると、${afterView.text}。`);
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
        const next = await api.approve(rec.plan, fixes[index].fix_id, 'minsei');
        if (!scope.alive) return;
        rec.plan = A.mergeGeometry(next, rec.plan);
        A.save();
        A.go('check', { recheck: '1' });
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
