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

  // 文字起こしの長さの上限（X-03。サーバーも 2,000 字までを受け付ける）
  const MAX_CHARS = 2000;
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
    A.setStage(1);
    const T = A.T;

    let listening = null;
    let working = false;
    let forceText = false;
    let sttFailed = false; // 音声入力を試して、使えなかった（文字の入力に切り替えた理由の表示に使う）
    let continuous = Boolean(A.state.settings.continuous) && S.sttSupported;
    let lastChanged = new Set();
    let retryText = null;
    let lastText = '';
    // 前に話した内容があれば、文字起こしの欄に、薄く出しておく
    const spoken = Object.values(rec.plan.sources || {}).filter((src) => src.kind === 'utterance' && src.text);
    spoken.sort((a, b) => String(a.at || '').localeCompare(String(b.at || '')));
    if (spoken.length) lastText = `前に話した内容: ${T(spoken[spoken.length - 1].text)}`;

    const typedMode = () => !S.sttSupported || forceText;
    const currentQuestion = () => (rec.nextQuestion !== undefined ? rec.nextQuestion : (rec.stage === 'start' ? rec.initialQuestion : null));

    // --- 部品 ---------------------------------------------------------------

    const person = (rec.names || []).find((n) => n.role === 'person');
    const personBar = h('div', { class: 'person-bar' },
      h('span', { class: 'person-avatar', 'aria-hidden': 'true' }, EC.icon('person', { size: 26 })),
      h('div', { class: 'person-text' },
        h('span', { class: 'person-name' }, (person && person.call) || rec.title || rec.plan.alias),
        rec.sample ? h('span', { class: 'sub' }, 'デモ用の架空の人物') : null),
      h('div', { class: 'progress-wrap' },
        h('span', { class: 'progress-pill', id: 'progress' }),
        h('span', { class: 'meter', 'aria-hidden': 'true' }, h('span', { class: 'meter-fill', id: 'meter-fill' }))));

    const questionTagText = h('span', null, '最初の質問');
    const questionTag = h('span', { class: 'question-tag' }, EC.icon('speaker', { size: 26 }), questionTagText);
    const questionText = h('span', { class: 'question-text' });
    const question = h('button', { type: 'button', class: 'question-box', 'aria-describedby': 'question-hint', onclick: () => say() },
      questionTag, questionText, h('span', { class: 'question-hint', id: 'question-hint' }, EC.icon('play', { size: 20 }), 'もう一度聞く'));

    // 聞き取り中の表示（脈を打つ印と文字）。声の入力を受けていることを、ボタンの色だけに頼らず伝える
    const liveBadge = h('div', { class: 'live-badge', hidden: true }, h('span', { class: 'live-dot', 'aria-hidden': 'true' }), '聞いています');
    const transcript = h('div', { class: 'transcript', id: 'transcript', 'aria-live': 'polite' });
    const statusSlot = h('div', { class: 'status-slot' });
    const modeNote = h('p', { class: 'mode-note' });
    const textarea = h('textarea', { class: 'textarea', id: 'typed', rows: '4', maxlength: String(MAX_CHARS), placeholder: 'ここに文字で入力できます' });
    const typedWrap = h('div', { class: 'typed-wrap' },
      h('label', { class: 'field-label-text', for: 'typed' }, '文字で入力する'),
      textarea,
      h('p', { class: 'sub' }, 'キーボードのマイクのボタンで、話して入力することもできます。'));
    // 声と文字の切り替え（声がうまくいかないときの入口）。音声入力が使えない環境では、文字だけなので出さない
    const typedToggleText = h('span', null, '文字で入力する');
    const typedToggle = S.sttSupported
      ? h('button', { type: 'button', class: 'btn-row row-action' }, EC.icon('keyboard', { size: 26 }), typedToggleText)
      : null;
    const switchRow = h('button', { type: 'button', class: 'switch-row', role: 'switch', 'aria-checked': String(continuous) },
      h('span', { class: 'switch-box', 'aria-hidden': 'true' }),
      h('span', null, '続けて聞く（質問を読んだあと、自動で聞き取ります）'),
      h('span', { class: 'switch-state' }, continuous ? '入' : '切'));
    // 電話で聞き取るときの設定（続けて聞く）は、ふだんは閉じておく
    const phoneBody = h('div', { class: 'phone-body', hidden: !continuous },
      switchRow,
      h('p', { class: 'sub' }, '電話では、スピーカーにして使います。質問を読み上げたあと、答えを自動で聞き取ります。'));
    const phoneToggle = h('button', { type: 'button', class: 'btn-row', 'aria-expanded': String(continuous) }, '電話での聞き取りの設定');
    phoneToggle.addEventListener('click', () => {
      const open = phoneToggle.getAttribute('aria-expanded') === 'true';
      phoneToggle.setAttribute('aria-expanded', open ? 'false' : 'true');
      phoneBody.hidden = open;
    });
    const phoneSettings = h('div', { class: 'phone-settings' }, phoneToggle, phoneBody);
    const premisesSlot = h('div', { class: 'premises-slot' });
    const fieldsList = h('div', { class: 'fields-list' });
    const examplesSlot = h('div', { class: 'examples-slot' });

    EC.u.add(A.$main,
      personBar, statusSlot, question, typedToggle, typedWrap, liveBadge, transcript, modeNote,
      S.sttSupported ? phoneSettings : null,
      premisesSlot,
      h('div', { class: 'fields-head' }, h('h2', null, '計画の欄')),
      fieldsList, examplesSlot, A.auditLink('talk'), A.noticeSafety());

    // --- 表示の更新 -----------------------------------------------------------

    function renderQuestion() {
      const q = currentQuestion();
      const anyFilled = Object.values(rec.plan.fields || {}).some((f) => ui.isFilled(f));
      questionTagText.textContent = q ? (anyFilled ? '追加の質問' : '最初の質問') : '聞き取りの状況';
      questionText.textContent = q ? T(q) : DONE_TEXT;
    }

    function renderTranscript(finalText, interim) {
      clear(transcript);
      transcript.classList.toggle('is-live', Boolean(listening));
      liveBadge.hidden = !listening;
      if (!finalText && !interim) {
        transcript.append(h('span', { class: 'placeholder' }, lastText || 'ここに、文字起こし（話した内容の文字）が出ます'));
        return;
      }
      EC.u.add(transcript, finalText, interim ? h('span', { class: 'interim' }, interim) : null);
    }

    function renderNote() {
      clear(modeNote);
      if (typedMode()) {
        modeNote.append(!S.sttSupported
          ? 'このブラウザや接続では、音声入力を使えません。文字で入力してください。'
          : (sttFailed ? '音声入力が使えませんでした。文字で入力してください。' : '文字で入力しています。話すときは、「声で話す」を押してください。'));
      } else {
        modeNote.append(S.sttNotice());
      }
      typedWrap.hidden = !typedMode();
      if (typedToggle) typedToggleText.textContent = forceText ? '声で話す' : '文字で入力する';
    }

    function renderFields() {
      clear(fieldsList);
      const plan = rec.plan;
      const filled = Object.values(plan.fields || {}).filter((f) => ui.isFilled(f)).length;
      const total = Object.keys(L.FIELDS).length;
      const pill = A.$main.querySelector('#progress');
      if (pill) pill.textContent = `埋まった欄 ${filled}／${total}`;
      const fill = A.$main.querySelector('#meter-fill');
      if (fill) fill.style.width = `${total ? Math.round((filled / total) * 100) : 0}%`;

      // 自宅の場所（地図で指定）
      if (plan.home && plan.sources && plan.sources.src_home) {
        const detail = h('div', { class: 'source-detail-wrap', hidden: true });
        const btn = h('button', { type: 'button', class: 'field-row', 'aria-expanded': 'false' },
          h('span', { class: 'field-title' }, '自宅の場所'),
          h('span', { class: 'field-value' }, '地図で決めた位置'),
          h('span', { class: 'field-hint' }, '住所の文字は保存せず、AI にも送りません。'),
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
      // 聞き取りが終わったあとは、先へ進む「それでいい」を主なボタンにする（話している間は、話すボタンが主）
      const finished = !currentQuestion();
      const talkMain = !finished || Boolean(listening) || working || retryText !== null;
      const talkAction = { id: 'talk', kind: talkMain ? 'primary' : undefined, icon: 'mic', label: '話す', onClick: onTalk };
      if (working) { talkAction.label = '欄に入れています'; talkAction.disabled = true; talkAction.icon = null; }
      else if (retryText !== null) { talkAction.label = 'もう一度'; talkAction.icon = 'swap'; talkAction.onClick = () => submit(retryText); }
      else if (listening) { talkAction.label = '止める'; talkAction.listening = true; }
      else if (typedMode()) { talkAction.label = '送る'; talkAction.icon = 'send'; }
      A.setActions([talkAction, { id: 'ok', kind: talkMain ? undefined : 'primary', trailing: 'chevron-right', label: 'それでいい', disabled: working || Boolean(listening), onClick: () => A.go('check') }]);
    }

    /** いまの質問を読み上げる（合成音声のファイルがあれば、それを再生する）。 */
    function say() {
      const q = currentQuestion();
      return A.say(q || DONE_TEXT);
    }

    /**
     * 答えを欄に入れたあとは、次の質問の文が全部見える位置へ移動する。読み上げる文と、見えている文を合わせる。
     * どの欄に入ったかの結果は、質問の直前に出る。欄の一覧は、その下にある。
     * 上から順に、「画面の先頭」「結果の行」「質問」のどこから始めれば、質問の終わりまで 1 画面に入るかを選ぶ。
     */
    function showQuestion() {
      const main = A.$main;
      const topOf = (el) => el.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop;
      const questionTop = topOf(question);
      const questionBottom = questionTop + question.offsetHeight;
      const resultTop = statusSlot.firstElementChild ? topOf(statusSlot.firstElementChild) : questionTop;
      const gap = 8;
      const start = [0, resultTop - gap, questionTop - gap].find((t) => questionBottom - t <= main.clientHeight - gap);
      // 欄の一覧が長いので、滑らかに動かさず、読み上げが始まる前に移動する
      main.scrollTo({ top: Math.max(0, start === undefined ? questionTop - gap : start), behavior: 'auto' });
    }

    /** first から last までが見える位置にする（すでに見えているときは動かさない。見える高さに入らないときは first を先頭にする）。 */
    function reveal(first, last) {
      const main = A.$main;
      const topOf = (el) => el.getBoundingClientRect().top - main.getBoundingClientRect().top + main.scrollTop;
      const top = topOf(first) - 8;
      const bottom = topOf(last) + last.offsetHeight + 8;
      if (top < main.scrollTop) main.scrollTo({ top: Math.max(0, top), behavior: 'auto' });
      else if (bottom > main.scrollTop + main.clientHeight) main.scrollTo({ top: Math.max(0, Math.min(top, bottom - main.clientHeight)), behavior: 'auto' });
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
            sttFailed = true;
            renderNote();
            statusSlot.append(A.errorBox(message || '音声入力を使えませんでした。文字で入力してください。'));
            refreshActions();
            reveal(statusSlot, typedWrap); // 文字を入れる欄が、すぐ見えるように
          } else {
            statusSlot.append(A.statusBox(message));
          }
        },
      });
      if (!listening) {
        forceText = true;
        sttFailed = true;
        renderNote();
        statusSlot.append(A.errorBox(S.errorMessage('start-failed')));
        reveal(statusSlot, typedWrap);
      } else {
        renderTranscript('', '');
        reveal(liveBadge, transcript); // 「聞いています」と文字起こしが見える位置へ
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

    async function submit(raw) {
      if (working) return;
      const text = String(raw).slice(0, MAX_CHARS);
      working = true;
      retryText = null;
      lastText = text;
      renderTranscript(text, '');
      clear(statusSlot);
      statusSlot.append(A.statusBox('お話を、欄に入れています', 'busy'));
      A.announce('お話を、欄に入れています');
      refreshActions();
      const before = snapshot(rec.plan);
      try {
        const pseudo = pseudonymize(text, rec.names);
        const res = await api.interview(rec.plan, pseudo, 'minsei', rec.askedField);
        if (!scope.alive) return;
        rec.plan = A.mergeGeometry(res.plan, rec.plan);
        rec.askedField = res.next_field || null; // 次の応答で asked_field として送る
        rec.nextQuestion = res.next_question === undefined ? null : res.next_question;
        A.save();
        const changed = res.changed_fields && res.changed_fields.length ? res.changed_fields : diffFields(before, rec.plan);
        lastChanged = new Set(changed);
        clear(statusSlot);
        if (changed.length) {
          // 結果は短くして、次の質問と一緒に見えるようにする（欄の名前は 3 つまで。全部は、下の一覧で見られる）
          const names = changed.map((k) => EC.u.splitLabel(L.FIELDS[k] || k).title);
          const shown = names.length > 3 ? `${names.slice(0, 3).join('、')} ほか` : names.join('、');
          statusSlot.append(A.statusBox(`${changed.length}つの欄に入りました: ${shown}`, 'done'));
        } else {
          statusSlot.append(A.statusBox('入れる欄が見つかりませんでした。もう一度、話してください。'));
        }
        renderQuestion();
        renderFields();
        showQuestion();
        working = false;
        refreshActions();
        const said = say();
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
        Promise.resolve(q ? say() : false).then(() => { if (scope.alive && continuous && !listening && !working) startListening(); });
      }
      if (!continuous && listening) listening.stop();
    });

    scope.onLeave(() => { if (listening) listening.abort(); listening = null; });

    // 声と文字の切り替え。文字にするときは、入力欄へ移る。声にもどすときは、ふたたび声を試す
    if (typedToggle) {
      typedToggle.addEventListener('click', () => {
        if (working) return;
        if (listening) listening.abort();
        listening = null;
        forceText = !forceText;
        if (!forceText) sttFailed = false;
        clear(statusSlot);
        renderTranscript('', '');
        renderNote();
        refreshActions();
        if (forceText) { reveal(question, typedWrap); textarea.focus({ preventScroll: true }); }
      });
    }

    // 質問を読み上げている間は、質問の札の印を動かす
    question.classList.toggle('is-speaking', S.isSpeaking());
    scope.onLeave(S.onSpeakingChange((speaking) => question.classList.toggle('is-speaking', speaking)));

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
      say().then(() => {
        if (scope.alive && continuous && !listening && !working) startListening();
      });
    }
  });

  EC.talkScreen = { DONE_TEXT };
})();
