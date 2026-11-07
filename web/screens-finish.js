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

  async function ensureFacts(rec) {
    if (A.state.facts) return A.state.facts;
    const f = await api.facts(rec.plan);
    A.state.facts = f || { facts: null, shelters: [], sources: {} };
    return A.state.facts;
  }

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
      h('h1', null, '確認'),
      statusSlot, itemSlot, listSlot, A.noticeSafety());

    let listening = null;
    let working = false;
    let forceText = false;
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
      const issues = rec.plan.issues || [];
      const found = issues.find((i) => i.issue_id === issueId);
      if (found) Object.assign(found, patch, { acknowledged: true });
      else issues.push({ issue_id: issueId, message: '', severity: '要確認', ...patch, acknowledged: true });
      rec.plan.issues = issues;
      A.save();
    }

    function buildQueue() {
      const queue = [];
      for (const f of rec.plan.findings || []) {
        const key = verdictView(worstResult(f.result)).key;
        if (key === 'short' || key === 'unknown') {
          const id = `consult_${f.finding_id}`;
          const done = (rec.plan.issues || []).find((i) => i.issue_id === id && i.acknowledged);
          if (!done) queue.push({ type: 'consult', id, finding: f });
        }
      }
      for (const i of rec.plan.issues || []) {
        if (!i.issue_id.startsWith('consult_') && !i.acknowledged) queue.push({ type: 'issue', id: i.issue_id, issue: i });
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
      const reds = (plan.findings || []).filter((f) => ['short', 'unknown'].includes(verdictView(worstResult(f.result)).key));
      const issues = (plan.issues || []).filter((i) => !i.issue_id.startsWith('consult_'));
      const openIssues = issues.filter((i) => !i.acknowledged);

      const row = (ok, text) => h('li', { class: ok ? 'is-ok' : 'is-open' },
        h('span', { class: 'mark', 'aria-hidden': 'true' }, ok ? '○' : '△'), h('span', null, text));
      listSlot.append(h('h2', null, '確認の結果'), h('ul', { class: 'check-list' },
        row(noSource.length === 0, noSource.length === 0 ? `すべての欄に出典があります（${filled.length}欄）` : `出典がない欄が${noSource.length}つあります`),
        row(unconfirmed.length === 0, unconfirmed.length === 0 ? '未確認の欄は、ありません' : `未確認の欄: ${unconfirmed.length}つ（印刷には「未確認」と出ます）`),
        row(reds.length === 0, reds.length === 0 ? '間に合わないシナリオは、ありません' : `まだ間に合わないシナリオ: ${reds.length}つ（ご本人・ご家族・市と相談します）`),
        row(openIssues.length === 0, issues.length ? `検証エージェントの指摘: ${issues.length}件のうち、${issues.length - openIssues.length}件を確認ずみ` : '検証エージェントの指摘は、ありません')));
    }

    // --- 1 件ずつ確認する ---------------------------------------------------------

    function showItem() {
      clear(itemSlot);
      renderChecklist();
      const queue = buildQueue();
      if (!queue.length) { finish(); return; }
      const item = queue[0];
      clear(statusSlot);

      if (item.type === 'consult') {
        const v = verdictView(worstResult(item.finding.result));
        const text = `${ui.scenarioTitle(rec.plan, item.finding)}は、${v.label}${v.detail ? `（${v.detail}）` : ''}です。ご本人・ご家族・市と相談します。`;
        itemSlot.append(h('div', { class: 'card item-card' },
          h('p', { class: 'strong' }, `残りの確認 ${queue.length}件`),
          ui.verdictBadge(worstResult(item.finding.result)),
          h('p', { class: 'explain' }, text)));
        A.say(text);
        A.setActions([{
          id: 'consult', kind: 'primary', label: '相談することにする',
          onClick: () => { acknowledge(item.id, { message: text, resolution: 'consult' }); showItem(); },
        }]);
        return;
      }

      const issue = item.issue;
      const fieldLabel = issue.field && L.FIELDS[issue.field] ? EC.u.splitLabel(L.FIELDS[issue.field]).title : '確認';
      const textarea = h('textarea', { class: 'textarea', id: 'answer', rows: '3', placeholder: 'ここに文字で入力できます' });
      const typedWrap = h('div', { hidden: true }, h('label', { class: 'field-label-text', for: 'answer' }, '文字で答える'), textarea);
      const transcript = h('div', { class: 'transcript' }, h('span', { class: 'placeholder' }, 'ここに、答えが文字で出ます'));
      itemSlot.append(h('div', { class: 'card item-card' },
        h('p', { class: 'strong' }, `残りの確認 ${queue.length}件　${fieldLabel}`),
        h('span', { class: 'chip chip-none' }, issue.severity || '要確認'),
        h('p', { class: 'explain' }, T(issue.message))),
      transcript, typedWrap,
      h('p', { class: 'mode-note' }, S.sttSupported ? S.sttNotice() : 'このブラウザや接続では、音声入力を使えません。文字で入力してください。'));
      A.say(issue.message);

      const typedMode = () => !S.sttSupported || forceText;
      typedWrap.hidden = !typedMode();

      const refresh = () => {
        const talk = { id: 'answer-btn', kind: 'primary', label: '話して答える', onClick: onTalk };
        if (working) { talk.label = '欄に入れています'; talk.disabled = true; }
        else if (listening) { talk.label = 'とめる'; talk.listening = true; }
        else if (typedMode()) talk.label = '送る';
        A.setActions([talk, { id: 'skip', label: '未確認のまま', disabled: working, onClick: () => { acknowledge(item.id, { resolution: 'unconfirmed' }); showItem(); } }]);
      };

      const submit = async (text) => {
        working = true;
        clear(statusSlot);
        statusSlot.append(A.statusBox('答えを、欄に入れています'));
        refresh();
        try {
          const res = await api.interview(rec.plan, pseudonymize(text, rec.names), 'minsei');
          if (!scope.alive) return;
          const issues = rec.plan.issues;
          rec.plan = A.mergeGeometry(res.plan, rec.plan);
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
          onInterim: (fin, interim) => { clear(transcript); transcript.append(fin, interim ? h('span', { class: 'interim' }, interim) : null); },
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
      h('p', { class: 'lead' }, 'A4で2枚を印刷します。1枚目は市に出す様式、2枚目は本人用の避難カードです。'),
      slot);

    (async () => {
      await ensureFacts(rec);
      if (!scope.alive) return;
      const sheets = EC.print.buildSheets(rec, { T, facts: A.state.facts });
      slot.replaceWith(sheets);
      A.$main.append(A.noticeSafety());
    })();

    A.setActions([
      { id: 'print', kind: 'primary', label: '印刷する', onClick: () => window.print() },
      { id: 'home', label: 'ホームにもどる', onClick: () => A.go('home') },
    ]);
  });

  EC.finishScreens = { clone };
})();
