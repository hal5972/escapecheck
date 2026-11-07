/*
 * 画面: 確認（F-12。検証エージェントの点検と、コードによる点検）と、印刷（F-13）。
 * 未解決の項目が残る間は「完成」にしない。残った「要確認」には、話して答えるか「未確認のまま」と印を付ける。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const A = EC.app;
  const { h, clear, pseudonymize, verdictView, worstResult } = EC.u;
  const ui = EC.ui;
  const L = EC.L;
  const S = EC.speech;
  const api = EC.api;

  const clone = (x) => JSON.parse(JSON.stringify(x));

  // ---------------------------------------------------------------------------
  // 確認
  // ---------------------------------------------------------------------------

  A.register('verify', ({ scope }) => {
    const rec = A.loadSessionRecord() || A.state.rec;
    if (!rec) { A.go('home', {}, { replace: true }); return; }
    A.state.rec = rec;
    A.setBack('check');
    const T = A.T;

    const statusSlot = h('div');
    const listSlot = h('div', { class: 'checklist-slot' });
    const itemSlot = h('div', { class: 'item-slot' });
    A.$main.append(
      h('h1', null, `${A.personLabel(rec)}の確認`),
      statusSlot, itemSlot, listSlot, A.auditLink('verify'), A.noticeSafety());

    let listening = null;
    let working = false;
    let forceText = false;
    let resolved = 0; // 確認ずみにした項目の数（「確認 2／3」の表示に使う）
    scope.onLeave(() => { if (listening) listening.abort(); listening = null; });

    // --- 確認の結果を計画に控える -------------------------------------------------

    /** 検証エージェントの指摘に、人が確認した印を引き継ぐ。「相談する」の印も残す。 */
    function mergeIssues(fromServer) {
      const keep = new Map((rec.plan.issues || []).map((i) => [i.issue_id, i]));
      const merged = (fromServer || []).map((i) => ({ ...i, acknowledged: keep.has(i.issue_id) ? keep.get(i.issue_id).acknowledged : Boolean(i.acknowledged) }));
      for (const [id, i] of keep) if (id.startsWith('consult_')) merged.push(i);
      rec.plan.issues = merged;
    }

    function acknowledge(issueId, patch) {
      resolved += 1;
      const issues = rec.plan.issues || [];
      const found = issues.find((i) => i.issue_id === issueId);
      if (found) Object.assign(found, patch, { acknowledged: true });
      else issues.push({ issue_id: issueId, message: '', severity: '要確認', ...patch, acknowledged: true });
      rec.plan.issues = issues;
      A.save();
    }

    function buildQueue() {
      const queue = [];
      const issues = rec.plan.issues || [];
      for (const f of rec.plan.findings || []) {
        const key = verdictView(EC.u.findingResult(rec.plan, f)).key;
        if (key === 'short' || key === 'unknown') {
          const id = `consult_${f.finding_id}`;
          const done = issues.find((i) => i.issue_id === id && i.acknowledged);
          if (!done) queue.push({ type: 'consult', id, finding: f });
        }
      }
      for (const i of issues) {
        if (i.issue_id.startsWith('consult_') || i.acknowledged) continue;
        // サーバーのコードの点検が出す「◯のシナリオで間に合いません」は、上の「相談する」の確認で扱う。
        // 相談の印が付いていれば、その項目も確認ずみにする。付いていなければ、相談の確認が先に出るので、ここには出さない
        const short = /^chk_short_(S\d)$/.exec(i.issue_id);
        // 計画の避難先に上がれない確認（chk_shelter_unusable）も、その問題点のカードの「相談する」の確認で扱う。
        // 相談の確認が出ない（問題点のカードが無い）ときは、この項目で確認する
        const unusable = i.issue_id === 'chk_shelter_unusable';
        const target = short
          ? (rec.plan.findings || []).find((x) => x.scenario_id === short[1])
          : (unusable ? (rec.plan.findings || []).find((x) => x.hole_type === 'CANNOT_CLIMB') : null);
        const viaConsult = Boolean(target) && ['short', 'unknown'].includes(verdictView(EC.u.findingResult(rec.plan, target)).key);
        if (short || (unusable && viaConsult)) {
          const f = target;
          if (f && issues.some((x) => x.issue_id === `consult_${f.finding_id}` && x.acknowledged)) {
            i.acknowledged = true;
            i.resolution = 'consult';
          }
          continue;
        }
        queue.push({ type: 'issue', id: i.issue_id, issue: i });
      }
      return queue;
    }

    // --- 点検の一覧（コードによる点検） -------------------------------------------

    function renderChecklist() {
      clear(listSlot);
      const plan = rec.plan;
      const keys = Object.keys(L.FIELDS);
      const filled = keys.filter((k) => ui.isFilled(plan.fields[k]));
      const noSource = filled.filter((k) => !plan.sources || !plan.sources[plan.fields[k].source_id]);
      const unconfirmed = keys.filter((k) => !ui.isFilled(plan.fields[k]));
      const reds = (plan.findings || []).filter((f) => ['short', 'unknown'].includes(verdictView(EC.u.findingResult(plan, f)).key));
      const allIssues = plan.issues || [];
      // 相談することにした（確認ずみの）シナリオ
      const consulted = reds.filter((f) => allIssues.some((i) => i.issue_id === `consult_${f.finding_id}` && i.acknowledged));
      // 検証エージェントの指摘。コードの点検の項目（chk_ で始まる）は、上の未確認の欄・間に合わないシナリオの行に含まれる
      const agentIssues = allIssues.filter((i) => !i.issue_id.startsWith('consult_') && !i.issue_id.startsWith('chk_'));
      const agentOpen = agentIssues.filter((i) => !i.acknowledged);

      const row = (ok, text) => h('li', { class: ok ? 'is-ok' : 'is-open' },
        h('span', { class: 'mark', 'aria-hidden': 'true' }, ok ? '○' : '△'), h('span', null, text));
      listSlot.append(h('h2', null, '確認の結果'), h('ul', { class: 'check-list' },
        row(noSource.length === 0, noSource.length === 0 ? `すべての欄に出典があります（${filled.length}欄）` : `出典がない欄が${noSource.length}つあります`),
        row(unconfirmed.length === 0, unconfirmed.length === 0 ? '未確認の欄は、ありません' : `未確認の欄: ${unconfirmed.length}つ（印刷には「未確認」と出ます）`),
        row(consulted.length === reds.length, reds.length === 0
          ? '間に合わないシナリオは、ありません'
          : (consulted.length === reds.length
            ? `間に合わないシナリオ: ${reds.length}つ（ご本人・ご家族・市と相談することにしました）`
            : `まだ間に合わないシナリオ: ${reds.length}つ（ご本人・ご家族・市と相談します）`)),
        row(agentOpen.length === 0, agentIssues.length ? `検証エージェントの指摘: ${agentIssues.length}件のうち、${agentIssues.length - agentOpen.length}件を確認ずみ` : '検証エージェントの指摘は、ありません')));
    }

    // --- 1 件ずつ確認する ---------------------------------------------------------

    function showItem() {
      clear(itemSlot);
      const queue = buildQueue(); // 先に作る（コードの点検の「間に合わない」の項目に、確認ずみの印を付けるため）
      renderChecklist();
      if (!queue.length) { finish(); return; }
      const item = queue[0];
      clear(statusSlot);

      if (item.type === 'consult') {
        const fr = EC.u.findingResult(rec.plan, item.finding);
        const v = verdictView(fr);
        const text = EC.u.isBlockedShelter(rec.plan, item.finding)
          ? '計画の避難先には、上がれません。ご本人・ご家族・市と相談します。'
          : `${ui.scenarioTopic(rec.plan, item.finding)}は、${v.label}${v.detail ? `（${v.detail}）` : ''}です。ご本人・ご家族・市と相談します。`;
        itemSlot.append(h('div', { class: 'card item-card' },
          h('p', { class: 'strong' }, `確認 ${resolved + 1}／${resolved + queue.length}`),
          ui.verdictBadge(fr),
          h('p', { class: 'explain' }, text)));
        A.say(text);
        A.setActions([{
          id: 'consult', kind: 'primary', label: '相談することにする',
          onClick: () => { acknowledge(item.id, { message: text, resolution: 'consult' }); showItem(); },
        }]);
        return;
      }

      const issue = item.issue;
      if (issue.issue_id === 'chk_shelter_unusable') {
        // 計画の避難先に上がれない: 答える欄が無い。避難先を変える案を見るか、相談する
        const fixTarget = (rec.plan.findings || []).find((x) => x.hole_type === 'CANNOT_CLIMB' && EC.checkScreen.isImprovable(rec.plan, x));
        itemSlot.append(h('div', { class: 'card item-card' },
          h('p', { class: 'strong' }, `確認 ${resolved + 1}／${resolved + queue.length}　避難先`),
          h('span', { class: 'chip chip-none' }, issue.severity || '要確認'),
          h('p', { class: 'explain' }, T(issue.message))));
        A.say(issue.message);
        A.setActions([
          fixTarget
            ? { id: 'fix', kind: 'primary', label: '避難先を変える案を見る', onClick: () => A.go('fix', { finding: fixTarget.finding_id }) }
            : { id: 'check', kind: 'primary', label: '点検の画面へ', onClick: () => A.go('check') },
          { id: 'consult', label: '本人・家族と相談する', onClick: () => { acknowledge(item.id, { resolution: 'consult' }); showItem(); } },
        ]);
        return;
      }
      const fieldLabel = issue.field && L.FIELDS[issue.field] ? EC.u.splitLabel(L.FIELDS[issue.field]).title : '確認';
      // 欄に内容があるときは、いまの内容を見せる（検証エージェントの指摘の文は、短い定型の文のため）
      const current = issue.field ? rec.plan.fields[issue.field] : null;
      const filledNow = Boolean(current) && ui.isFilled(current);
      const textarea = h('textarea', { class: 'textarea', id: 'answer', rows: '3', maxlength: '2000', placeholder: 'ここに文字で入力できます' });
      const typedWrap = h('div', { hidden: true }, h('label', { class: 'field-label-text', for: 'answer' }, '文字で答える'), textarea);
      const transcript = h('div', { class: 'transcript' }, h('span', { class: 'placeholder' }, 'ここに、文字起こし（答えの文字）が出ます'));
      itemSlot.append(h('div', { class: 'card item-card' },
        h('p', { class: 'strong' }, `確認 ${resolved + 1}／${resolved + queue.length}　${fieldLabel}`),
        h('span', { class: 'chip chip-none' }, issue.severity || '要確認'),
        h('p', { class: 'explain' }, T(issue.message)),
        filledNow ? h('p', null, `いまの欄の内容: ${ui.valueText(current.value, T)}`) : null),
      transcript, typedWrap,
      h('p', { class: 'mode-note' }, S.sttSupported ? S.sttNotice() : 'このブラウザや接続では、音声入力を使えません。文字で入力してください。'));
      A.say(issue.message);

      const typedMode = () => !S.sttSupported || forceText;
      typedWrap.hidden = !typedMode();

      const refresh = () => {
        const talk = { id: 'answer-btn', kind: 'primary', label: '話して答える', onClick: onTalk };
        if (working) { talk.label = '欄に入れています'; talk.disabled = true; }
        else if (listening) { talk.label = '止める'; talk.listening = true; }
        else if (typedMode()) talk.label = '送る';
        A.setActions([talk, {
          id: 'skip', label: filledNow ? 'このままにする' : '未確認のまま', disabled: working,
          onClick: () => { acknowledge(item.id, { resolution: filledNow ? 'kept' : 'unconfirmed' }); showItem(); },
        }]);
      };

      const submit = async (text) => {
        working = true;
        clear(statusSlot);
        statusSlot.append(A.statusBox('答えを、欄に入れています'));
        refresh();
        try {
          const res = await api.interview(rec.plan, pseudonymize(String(text).slice(0, 2000), rec.names), 'minsei', issue.field);
          if (!scope.alive) return;
          const issues = rec.plan.issues;
          rec.plan = A.mergeGeometry(res.plan, rec.plan);
          rec.askedField = res.next_field || null;
          rec.plan.issues = rec.plan.issues && rec.plan.issues.length ? rec.plan.issues : issues;
          working = false;
          acknowledge(item.id, { resolution: 'answered' });
          showItem();
        } catch (e) {
          if (!scope.alive) return;
          working = false;
          clear(statusSlot);
          statusSlot.append(A.errorBox(A.errorText(e)));
          refresh();
        }
      };

      function onTalk() {
        if (working) return;
        if (typedMode()) {
          const text = textarea.value.trim();
          if (!text) { textarea.focus(); return; }
          submit(text);
          return;
        }
        if (listening) { listening.stop(); return; }
        listening = S.listen({
          onInterim: (fin, interim) => { clear(transcript); EC.u.add(transcript, fin, interim ? h('span', { class: 'interim' }, interim) : null); },
          onEnd: ({ text, error }) => {
            listening = null;
            if (!scope.alive) return;
            if (text) { submit(text); return; }
            forceText = Boolean(error && error !== 'no-speech');
            typedWrap.hidden = !typedMode();
            clear(statusSlot);
            statusSlot.append(forceText ? A.errorBox(S.errorMessage(error) || '音声入力を使えませんでした。文字で入力してください。') : A.statusBox(S.errorMessage('no-speech')));
            refresh();
          },
        });
        if (!listening) { forceText = true; typedWrap.hidden = false; }
        refresh();
      }
      refresh();
    }

    // --- 完成 -----------------------------------------------------------------

    async function finish() {
      clear(itemSlot);
      renderChecklist();
      clear(statusSlot);
      statusSlot.append(A.statusBox('完成にできるか、確認しています'));
      A.setActions([{ id: 'wait', kind: 'primary', label: '確認しています', disabled: true }]);
      try {
        const res = await api.finalize(rec.plan);
        if (!scope.alive) return;
        clear(statusSlot);
        if (res.ok) {
          rec.plan = A.mergeGeometry(res.plan || rec.plan, rec.plan);
          rec.plan.status = 'final';
          A.save();
          itemSlot.append(h('div', { class: 'card item-card' },
            h('p', { class: 'strong' }, '確認が終わりました。'),
            h('p', null, '計画を完成にしました。市に出す様式と、本人用の避難カードを印刷できます。')));
          A.say('確認が終わりました。印刷できます。');
          A.setActions([{ id: 'print', kind: 'confirm', label: '印刷へ進む', onClick: () => A.go('print') }]);
        } else {
          statusSlot.append(A.errorBox(`まだ完成にできません: ${(res.reasons || []).map(T).join('、') || '確認の残りがあります'}`));
          A.setActions([{ id: 'again', kind: 'primary', label: 'もう一度確認する', onClick: () => start() }]);
        }
      } catch (e) {
        if (!scope.alive) return;
        clear(statusSlot);
        statusSlot.append(A.errorBox(A.errorText(e)));
        A.setActions([{ id: 'again', kind: 'primary', label: 'もう一度', onClick: () => finish() }]);
      }
    }

    async function start() {
      clear(statusSlot);
      statusSlot.append(A.statusBox('検証エージェントが、出典と原文の食い違いを確認しています'));
      A.announce('確認しています');
      A.setActions([{ id: 'wait', kind: 'primary', label: '確認しています', disabled: true }]);
      try {
        const issues = await api.verify(rec.plan);
        if (!scope.alive) return;
        mergeIssues(issues);
        A.save();
        showItem();
      } catch (e) {
        if (!scope.alive) return;
        clear(statusSlot);
        statusSlot.append(A.errorBox(A.errorText(e)));
        A.setActions([{ id: 'again', kind: 'primary', label: 'もう一度', onClick: start }]);
      }
    }

    start();
  });

  // ---------------------------------------------------------------------------
  // 印刷
  // ---------------------------------------------------------------------------

  A.register('print', ({ scope }) => {
    const rec = A.loadSessionRecord() || A.state.rec;
    if (!rec) { A.go('home', {}, { replace: true }); return; }
    A.state.rec = rec;
    A.setBack('verify');
    const T = A.T;

    const slot = h('div', { class: 'sheets-slot' });
    A.$main.append(
      h('h1', null, '印刷'),
      h('p', { class: 'lead' }, 'A4で2枚を印刷します。本人用の避難カードと、市に出す様式です。'),
      h('p', { class: 'sub' }, '印刷の順は、1枚目が市の様式、2枚目が避難カードです。'),
      slot);

    (async () => {
      await A.ensureFacts(rec);
      if (!scope.alive) return;
      const sheets = EC.print.buildSheets(rec, { T, facts: A.state.facts });
      slot.replaceWith(sheets);
      A.$main.append(A.noticeSafety());
    })();

    A.setActions([
      { id: 'print', kind: 'primary', label: '印刷する', onClick: () => window.print() },
      { id: 'home', label: 'ホームに戻る', onClick: () => A.go('home') },
    ]);
  });

  EC.finishScreens = { clone };
})();
