/*
 * 画面: 合言葉・ホーム・サンプルデータ・新しい人（自宅の場所）。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const A = EC.app;
  const { h, clear, uid, floorMin, isNum, formatClock } = EC.u;
  const store = EC.store;
  const api = EC.api;

  // 土佐市の宇佐を初めに映す
  const DEFAULT_CENTER = { lat: 33.4508, lon: 133.4354 };
  const DEFAULT_AREA = '宇佐';

  // ---------------------------------------------------------------------------
  // 合言葉（F-01）
  // ---------------------------------------------------------------------------

  A.register('login', ({ params }) => {
    A.setBack(null);
    const input = h('input', {
      class: 'input', id: 'passphrase', type: 'password', autocomplete: 'off', autocapitalize: 'off',
      spellcheck: 'false', 'aria-describedby': 'login-msg',
    });
    const msg = h('div', { id: 'login-msg', 'aria-live': 'polite' });
    A.$main.append(
      h('h1', null, '逃げ道の点検屋'),
      h('p', { class: 'lead' }, '話して計画を作り、点検して、改善します。'),
      A.noticePrivacy(),
      h('label', { class: 'field-label-text', for: 'passphrase' }, '合言葉'),
      input,
      msg);

    let busy = false;
    const submit = async () => {
      if (busy) return;
      busy = true;
      clear(msg);
      const buttons = A.setActions([{ label: '確認しています', kind: 'primary', id: 'login', disabled: true }]);
      try {
        const res = await api.login(input.value.trim());
        store.setSession('token', api.getToken());
        store.setSession('mode', res.mode);
        // 合言葉の前に開こうとしていた画面があれば、そこへ進む（URL を変えていないので、そのまま読み直す）
        const wanted = A.parseHash();
        A.go(wanted.name && wanted.name !== 'login' ? wanted.name : 'home', wanted.name && wanted.name !== 'login' ? wanted.params : {}, { replace: true });
      } catch (e) {
        msg.append(A.errorBox(A.errorText(e)));
        busy = false;
        A.setActions([{ label: '次へ', kind: 'primary', id: 'login', onClick: submit }]);
        input.focus();
      }
      return buttons;
    };
    A.setActions([{ label: '次へ', kind: 'primary', id: 'login', onClick: submit }]);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
    if (!window.matchMedia || !window.matchMedia('(pointer: coarse)').matches) input.focus();

    // 試験用: ?mock=1&autologin=1 で合言葉を省く（画面の確認・撮影用）
    if (params && new URLSearchParams(window.location.search).get('autologin') === '1') {
      input.value = 'demo';
      submit();
    }
  });

  // ---------------------------------------------------------------------------
  // ホーム
  // ---------------------------------------------------------------------------

  function recStatus(rec) {
    const plan = rec.plan;
    if (plan.status === 'final') return '完成';
    if ((plan.findings || []).length) return '点検ずみ';
    const filled = Object.values(plan.fields || {}).filter((f) => f.status === 'filled').length;
    return `聞き取り中（${filled}欄）`;
  }

  function openRecord(rec) {
    A.openRecord(rec);
    const plan = rec.plan;
    A.go((plan.findings || []).length ? 'check' : 'talk');
  }

  A.register('home', () => {
    A.setBack(null);
    A.$main.append(
      h('h1', null, '逃げ道の点検屋'),
      h('p', { class: 'lead' }, '話して個別避難計画を作り、厳しい条件で点検して、改善します。下の大きなボタンから、選んでください。'),
      A.noticePrivacy());

    const all = store.listPlans();
    const plans = all.slice(0, 5); // 新しい順に 5 件まで
    if (plans.length) {
      A.$main.append(h('h2', null, '前の計画'));
      for (const rec of plans) {
        const names = rec.names || [];
        const person = names.find((n) => n.role === 'person');
        const title = (person && person.call) || rec.title || rec.plan.alias;
        const row = h('div', { class: 'saved-row' },
          h('button', { type: 'button', class: 'btn-row saved-open', onclick: () => openRecord(rec) },
            h('span', { class: 'field-title' }, `${title}${rec.sample ? '（架空の人物）' : ''}`),
            h('span', { class: 'field-hint' }, `${recStatus(rec)}　${formatClock(rec.updated)}`)),
          h('button', {
            type: 'button', class: 'btn-row saved-delete',
            onclick: async () => {
              const ok = await EC.ui.confirmDialog({
                message: `${title}の計画を、この端末から消します。元には戻せません。`,
                okLabel: '消す', cancelLabel: 'やめる',
              });
              if (ok) {
                store.deletePlan(rec.id);
                if (A.state.rec && A.state.rec.id === rec.id) A.state.rec = null;
                A.go('home');
              }
            },
          }, '消す'));
        A.$main.append(row);
      }
      A.$main.append(h('p', { class: 'sub' }, `計画は、この端末の中だけに保存されています。${all.length > plans.length ? `（ほかに${all.length - plans.length}件）` : ''}`));
    }

    // 「はじめに」のアニメーション（別のセッションが作成する）があるときだけ、案内を出す
    const introSlot = h('div', { class: 'intro-slot' });
    A.$main.append(introSlot);
    fetch('intro/index.html', { method: 'HEAD', cache: 'no-store' }).then((res) => {
      if (res.ok) introSlot.append(h('a', { class: 'btn-link', href: 'intro/' }, 'はじめに（30秒のアニメーション）を見る'));
    }).catch(() => { /* ページが無いときは出さない */ });

    A.setActions([
      { label: 'サンプルデータで試す', kind: 'primary', id: 'samples', onClick: () => A.go('samples') },
      { label: '新しい計画を作る', id: 'new', onClick: () => A.go('new') },
    ]);
  });

  // ---------------------------------------------------------------------------
  // サンプルデータ（F-02）。架空の人物
  // ---------------------------------------------------------------------------

  A.register('samples', ({ scope }) => {
    A.setBack('home');
    A.$main.append(
      h('h1', null, 'サンプルデータ'),
      h('p', { class: 'lead' }, 'デモ用の架空の人物です。実在の人物とは関係ありません。'));
    const list = h('div', { class: 'sample-list' });
    A.$main.append(list, h('p', { class: 'sub' }, '誰を選ぶかで、点検の結果が変わります。'));

    EC.mock.samples.list().then((items) => {
      if (!scope.alive) return;
      for (const item of items) {
        list.append(h('button', { type: 'button', class: 'btn-row', onclick: () => A.go('sample', { id: item.id }) },
          h('span', { class: 'field-title' }, `${item.title}（架空の人物）`),
          h('span', { class: 'field-hint' }, item.summary)));
      }
    }).catch(() => {
      list.append(A.errorBox('サンプルデータを読み込めませんでした。'));
    });
  });

  A.register('sample', ({ params, scope }) => {
    A.setBack('samples');
    const id = params.id || 'yamada';

    const start = async (stage) => {
      A.setActions([{ label: '準備しています', kind: 'primary', disabled: true }]);
      try {
        const rec = await EC.mock.samples.build(id, stage);
        if (!scope.alive) return;
        A.openRecord(rec);
        const target = params.goto || (stage === 'start' ? 'talk' : 'check');
        A.go(target, params.finding ? { finding: params.finding } : {}, { replace: true });
      } catch (e) {
        A.$main.append(A.errorBox('サンプルデータを読み込めませんでした。'));
        A.setActions([{ label: 'もう一度', kind: 'primary', onClick: () => start(stage) }]);
      }
    };

    // 試験・撮影用: #/sample?id=yamada&stage=checked で、途中の状態から開く
    if (params.stage) {
      A.$main.append(h('h1', null, '準備しています'));
      start(params.stage);
      return;
    }

    EC.mock.samples.list().then((items) => {
      if (!scope.alive) return;
      const item = items.find((x) => x.id === id) || { title: id, summary: '' };
      A.$main.append(
        h('h1', null, `${item.title}（架空の人物）`),
        h('p', { class: 'lead' }, item.summary),
        h('p', null, 'どこから試しますか。'),
        h('p', { class: 'sub' }, '「話す手順から」は、話した内容が欄に入るところから。「途中から」は、聞き取りが終わった計画を点検するところからです。'));
      A.setActions([
        { label: '話す手順から', kind: 'primary', id: 'from-start', onClick: () => start('start') },
        { label: '途中から（点検する）', id: 'from-talked', onClick: () => start('talked') },
      ]);
    });
  });

  // ---------------------------------------------------------------------------
  // 新しい人: 自宅の場所を地図で決める（F-06）。住所の文字は保存せず、Claude にも送らない
  // ---------------------------------------------------------------------------

  A.register('new', ({ scope }) => {
    A.setBack('home');
    let pick = null;
    const personInput = h('input', { class: 'input', id: 'name-person', type: 'text', autocomplete: 'off', placeholder: '例: 山田' });
    const supporterInput = h('input', { class: 'input', id: 'name-supporter', type: 'text', autocomplete: 'off', placeholder: '例: 西村（なくてもよい）' });
    const factsSlot = h('div', { class: 'facts-slot' });
    const pickHint = h('p', { class: 'strong', id: 'pick-hint' }, '地図をタップすると、下の「ここに決める」を押せるようになります。');

    const map = EC.map.create({
      interactive: true, height: 340, center: DEFAULT_CENTER, zoom: 16, label: '自宅の場所を決める地図',
      onTap(point) {
        pick = point;
        pickHint.hidden = true;
        map.setShapes([{ t: 'marker', kind: 'home', at: point, label: '自宅', pos: 'r' }]);
        enable();
        loadFacts(point);
      },
    });
    scope.onLeave(() => map.destroy());

    const loadFacts = async (point) => {
      clear(factsSlot);
      factsSlot.append(A.statusBox('地図で調べています'));
      const probe = EC.mock.newPlan('Aさん', DEFAULT_AREA, point);
      const res = await api.facts(probe);
      if (!scope.alive || pick !== point) return;
      clear(factsSlot);
      factsSlot.append(EC.ui.factsPanel(res ? res.facts : null));
    };

    A.$main.append(
      h('h1', null, '新しい計画を作る'),
      h('p', null, '地図をタップ（指で軽く押す）して、自宅の場所を決めてください。指でなぞると、地図が動きます。'),
      map.el, map.credit, pickHint, factsSlot,
      h('h2', null, 'お名前（なくてもよい）'),
      h('p', { class: 'sub' }, 'デモ用の架空の名前を入れてください。実在の人の名前は入れないでください。この端末にだけ保存し、サーバーと AI（Claude）には、仮名（Aさん）で送ります。'),
      h('label', { class: 'field-label-text', for: 'name-person' }, '本人（苗字だけ）'),
      personInput,
      h('label', { class: 'field-label-text', for: 'name-supporter' }, '避難支援者（苗字だけ）'),
      supporterInput);

    const enable = () => {
      A.setActions([{ label: 'ここに決める', kind: 'primary', id: 'decide', disabled: !pick, onClick: decide }]);
    };

    const decide = () => {
      if (!pick) return;
      const plan = EC.mock.newPlan('Aさん', DEFAULT_AREA, pick);
      plan.sources.src_home = { kind: 'map_tap', label: '自宅の場所（地図で指定）' };
      const names = [];
      const person = personInput.value.trim();
      const supporter = supporterInput.value.trim();
      if (person) names.push({ alias: 'Aさん', real: person, call: `${person}さん`, full: person, role: 'person' });
      if (supporter) names.push({ alias: 'Bさん', real: supporter, call: `${supporter}さん`, full: supporter, role: 'supporter' });
      const rec = {
        id: uid('rec'), sample: null, title: names[0] ? names[0].call : 'Aさん', names,
        initialQuestion: '担当する方について、思いつくままに話してください。',
        nextQuestion: '担当する方について、思いつくままに話してください。',
        examples: [], plan, versions: [], stage: 'start',
      };
      A.openRecord(rec);
      A.go('talk');
    };
    enable();
  });

  EC.homeScreens = { DEFAULT_CENTER, DEFAULT_AREA, recStatus };
})();
