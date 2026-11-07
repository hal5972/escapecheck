/*
 * 画面: 話す（F-03〜F-06、F-23）。大きな「話す」ボタン 1 つで音声入力し、文字起こしを大きく表示し、
 * 欄に入った内容（出典つき）を見せ、追加の質問を読み上げる。音声入力が使えないときは文字の欄を出す（E1）。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const A = EC.app;
  const { h, clear, sleep, pseudonymize, isNum } = EC.u;
  const S = EC.speech;
  const ui = EC.ui;
  const api = EC.api;
  const L = EC.L;
  const store = EC.store;

  const DONE_TEXT = '聞き取りは、ひととおり終わりました。「それでいい」を押すと、点検します。';

  const snapshot = (plan) => {
    const out = {};
    for (const [k, v] of Object.entries(plan.fields || {})) out[k] = JSON.stringify([v.value, v.status]);
    return out;
  };
  const diffFields = (before, plan) => Object.keys(plan.fields || {}).filter((k) => before[k] !== JSON.stringify([plan.fields[k].value, plan.fields[k].status]));

  A.register('talk', ({ scope }) => {
    const rec = A.loadSessionRecord() || A.state.rec;
    if (!rec) { A.go('home', {}, { replace: true }); return; }
    A.state.rec = rec;
    A.setBack('home');
    const T = A.T;

    let listening = null;
    let working = false;
    let forceText = false;
    let continuous = Boolean(A.state.settings.continuous) && S.sttSupported;
    let lastChanged = new Set();
    let retryText = null;
    let lastText = '';

    const typedMode = () => !S.sttSupported || forceText;
    const currentQuestion = () => (rec.nextQuestion !== undefined ? rec.nextQuestion : (rec.stage === 'start' ? rec.initialQuestion : null));

    // --- 部品 ---------------------------------------------------------------

    const person = (rec.names || []).find((n) => n.role === 'person');
    const personBar = h('div', { class: 'person-bar' },
      h('span', { class: 'person-name' }, `${(person && person.call) || rec.title || rec.plan.alias}${rec.sample ? '（デモ用の架空の人物）' : ''}`),
      h('span', { class: 'progress-pill', id: 'progress' }));

    const questionTag = h('span', { class: 'question-tag' }, 'つぎの質問');
    const questionText = h('span', { class: 'question-text' });
    const question = h('button', { type: 'button', class: 'question-box', 'aria-describedby': 'question-hint', onclick: () => say() },
      questionTag, questionText, h('span', { class: 'question-hint', id: 'question-hint' }, 'さわると、もう一度読みます'));

    const transcript = h('div', { class: 'transcript', id: 'transcript', 'aria-live': 'polite' });
    const statusSlot = h('div', { class: 'status-slot' });
    const modeNote = h('p', { class: 'mode-note' });
    const textarea = h('textarea', { class: 'textarea', id: 'typed', rows: '4', placeholder: 'ここに文字で入力できます' });
    const typedWrap = h('div', { class: 'typed-wrap' },
      h('label', { class: 'field-label-text', for: 'typed' }, '文字で入力する'),
      textarea,
      h('p', { class: 'sub' }, 'キーボードのマイクのボタンで、話して入力することもできます。'));
    const switchRow = h('button', { type: 'button', class: 'switch-row', role: 'switch', 'aria-checked': String(continuous) },
      h('span', { class: 'switch-box', 'aria-hidden': 'true' }),
      h('span', null, '続けて聞く（質問を読んだあと、自動で聞き取ります）'),
      h('span', { class: 'switch-state' }, continuous ? '入' : '切'));
    const premisesSlot = h('div', { class: 'premises-slot' });
    const fieldsList = h('div', { class: 'fields-list' });
    const examplesSlot = h('div', { class: 'examples-slot' });

    A.$main.append(
      personBar, statusSlot, question, transcript, typedWrap, modeNote,
      S.sttSupported ? switchRow : null,
      premisesSlot,
      h('div', { class: 'fields-head' }, h('h2', null, '計画の欄')),
      fieldsList, examplesSlot, A.noticeSafety());

    // --- 表示の更新 -----------------------------------------------------------

    function renderQuestion() {
      const q = currentQuestion();
      questionTag.textContent = q ? 'つぎの質問' : '聞き取りの状況';
      questionText.textContent = q ? T(q) : DONE_TEXT;
    }

    function renderTranscript(finalText, interim) {
      clear(transcript);
      transcript.classList.toggle('is-live', Boolean(listening));
      if (!finalText && !interim) {
        transcript.append(h('span', { class: 'placeholder' }, lastText || 'ここに、お話が文字で出ます'));
        return;
      }
      transcript.append(finalText, interim ? h('span', { class: 'interim' }, interim) : null);
    }

    function renderNote() {
      clear(modeNote);
      if (typedMode()) {
        modeNote.append(S.sttSupported
          ? '音声入力が使えませんでした。文字で入力してください。'
          : 'このブラウザや接続では、音声入力を使えません。文字で入力してください。');
      } else {
        modeNote.append(S.sttNotice());
      }
      typedWrap.hidden = !typedMode();
    }

    function renderFields() {
      clear(fieldsList);
      const plan = rec.plan;
      const filled = Object.values(plan.fields || {}).filter((f) => ui.isFilled(f)).length;
      const total = Object.keys(L.FIELDS).length;
      const pill = A.$main.querySelector('#progress');
      if (pill) pill.textContent = `埋まった欄 ${filled}／${total}`;

      // 自宅の場所（地図で指定）
      if (plan.home && plan.sources && plan.sources.src_home) {
        const detail = h('div', { class: 'source-detail-wrap', hidden: true });
        const btn = h('button', { type: 'button', class: 'field-row', 'aria-expanded': 'false' },
          h('span', { class: 'field-title' }, '自宅の場所'),
          h('span', { class: 'field-value' }, '地図で決めた位置'),
          h('span', { class: 'field-hint' }, '住所の文字は保存せず、Claude にも送りません。'),
          ui.chip(ui.sourceInfo(plan, 'src_home')));
        btn.addEventListener('click', () => {
          const open = btn.getAttribute('aria-expanded') === 'true';
          btn.setAttribute('aria-expanded', open ? 'false' : 'true');
          if (!open && !detail.firstChild) detail.append(ui.sourceDetail(plan, 'src_home', null, T));
          detail.hidden = open;
        });
        fieldsList.append(h('div', { class: 'field-item' }, btn, detail));
      }

      for (const key of Object.keys(L.FIELDS)) fieldsList.append(ui.fieldRow(plan, key, T, { changed: lastChanged.has(key) }));

      // 聞き取りの中だけにある前提条件（S8）
      clear(premisesSlot);
      if ((plan.premises || []).length) {
        premisesSlot.append(h('h2', null, '話の中から分かった条件'));
        for (const p of plan.premises) {
          premisesSlot.append(h('div', { class: 'premise' },
            h('span', { class: 'premise-code' }, L.PREMISE_CODES[p.code] || p.code),
            h('span', { class: 'quote' }, `「${T(p.quote)}」`)));
        }
      }
    }

    function renderExamples() {
      clear(examplesSlot);
      if (!rec.examples || !rec.examples.length) return;
      const body = h('div', { class: 'examples-body', hidden: true });
      const toggle = h('button', { type: 'button', class: 'btn-row', 'aria-expanded': 'false' }, '話す内容の例を見る（デモ用）');
      toggle.addEventListener('click', () => {
        const open = toggle.getAttribute('aria-expanded') === 'true';
        toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
        body.hidden = open;
      });
      body.append(h('p', { class: 'sub' }, 'デモ用の架空の人物の話し言葉です。押すと、この文を話したことにして、欄に入れます。'));
      rec.examples.forEach((text) => {
        body.append(h('button', { type: 'button', class: 'btn-row', onclick: () => { if (!working) submit(text); } }, text));
      });
      examplesSlot.append(toggle, body);
    }

    function refreshActions() {
      const talkAction = { id: 'talk', kind: 'primary', label: '話す', onClick: onTalk };
      if (working) { talkAction.label = '欄に入れています'; talkAction.disabled = true; }
      else if (retryText !== null) { talkAction.label = 'もう一度'; talkAction.onClick = () => submit(retryText); }
      else if (listening) { talkAction.label = 'とめる'; talkAction.listening = true; }
      else if (typedMode()) { talkAction.label = '送る'; }
      A.setActions([talkAction, { id: 'ok', kind: 'confirm', label: 'それでいい', disabled: working, onClick: () => A.go('check') }]);
    }

    function say() {
      const q = currentQuestion();
      return A.say(q || DONE_TEXT);
    }

    // --- 聞き取り -------------------------------------------------------------

    function startListening() {
      if (listening || working || !scope.alive) return;
      S.cancel();
      clear(statusSlot);
      lastText = '';
      listening = S.listen({
        onInterim: (finalText, interim) => renderTranscript(finalText, interim),
        onEnd: ({ text, error }) => {
          listening = null;
          renderTranscript(text, '');
          lastText = text;
          if (!scope.alive) return;
          if (text) { submit(text); return; }
          refreshActions();
          const message = S.errorMessage(error || 'no-speech');
          clear(statusSlot);
          if (error && error !== 'no-speech' && error !== 'aborted') {
            forceText = true;
            renderNote();
            statusSlot.append(A.errorBox(message || '音声入力を使えませんでした。文字で入力してください。'));
            refreshActions();
          } else {
            statusSlot.append(A.statusBox(message));
          }
        },
      });
      if (!listening) {
        forceText = true;
        renderNote();
        statusSlot.append(A.errorBox(S.errorMessage('start-failed')));
      } else {
        renderTranscript('', '');
      }
      refreshActions();
    }

    function onTalk() {
      if (working) return;
      if (typedMode()) {
        const text = textarea.value.trim();
        if (!text) { textarea.focus(); return; }
        textarea.value = '';
        submit(text);
        return;
      }
      if (listening) { listening.stop(); return; }
      startListening();
    }

    async function submit(text) {
      if (working) return;
      working = true;
      retryText = null;
      lastText = text;
      renderTranscript(text, '');
      clear(statusSlot);
      statusSlot.append(A.statusBox('お話を、欄に入れています'));
      A.announce('お話を、欄に入れています');
      refreshActions();
      const before = snapshot(rec.plan);
      try {
        const pseudo = pseudonymize(text, rec.names);
        const res = await api.interview(rec.plan, pseudo, 'minsei');
        if (!scope.alive) return;
        rec.plan = A.mergeGeometry(res.plan, rec.plan);
        rec.nextQuestion = res.next_question === undefined ? null : res.next_question;
        A.save();
        const changed = res.changed_fields && res.changed_fields.length ? res.changed_fields : diffFields(before, rec.plan);
        lastChanged = new Set(changed);
        clear(statusSlot);
        if (changed.length) {
          const names = changed.map((k) => EC.u.splitLabel(L.FIELDS[k] || k).title);
          statusSlot.append(A.statusBox(`${changed.length}つの欄に入りました: ${names.join('、')}`));
        } else {
          statusSlot.append(A.statusBox('入れる欄が見つかりませんでした。もう一度、話してください。'));
        }
        renderQuestion();
        renderFields();
        const first = fieldsList.querySelector('.field-row.changed');
        if (first && first.scrollIntoView) first.scrollIntoView({ block: 'center', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
        working = false;
        refreshActions();
        const said = A.say(rec.nextQuestion || DONE_TEXT);
        if (continuous && rec.nextQuestion) {
          await said;
          if (scope.alive && continuous && !listening && !working) {
            await sleep(500);
            if (scope.alive && continuous && !listening && !working) startListening();
          }
        }
      } catch (e) {
        if (!scope.alive) return;
        working = false;
        retryText = text;
        clear(statusSlot);
        statusSlot.append(A.errorBox(A.errorText(e)));
        refreshActions();
      }
    }

    // --- 続けて聞く（F-23） ------------------------------------------------------

    switchRow.addEventListener('click', () => {
      continuous = !continuous;
      A.state.settings.continuous = continuous;
      store.setSetting('continuous', continuous);
      switchRow.setAttribute('aria-checked', String(continuous));
      switchRow.querySelector('.switch-state').textContent = continuous ? '入' : '切';
      if (continuous && !listening && !working) {
        const q = currentQuestion();
        Promise.resolve(q ? A.say(q) : false).then(() => { if (scope.alive && continuous && !listening && !working) startListening(); });
      }
      if (!continuous && listening) listening.stop();
    });

    scope.onLeave(() => { if (listening) listening.abort(); listening = null; });

    // --- 初めの表示 -----------------------------------------------------------

    renderQuestion();
    renderTranscript('', '');
    renderNote();
    renderFields();
    renderExamples();
    refreshActions();

    // 画面を開いたとき、質問を読み上げる。続けて聞くが入なら、そのまま聞き取りも始める
    const q = currentQuestion();
    if (q) {
      A.say(q).then(() => {
        if (scope.alive && continuous && !listening && !working) startListening();
      });
    }
  });

  EC.talkScreen = { DONE_TEXT };
})();
