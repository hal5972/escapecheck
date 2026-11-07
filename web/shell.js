/*
 * 画面の枠と共通の動き: 画面の切り替え（#/名前?キー=値）、戻る、下の大きなボタン（1 画面 2 つまで）、読み上げ、保存、名前の置き換え。
 * 各画面は EC.app.register(名前, 関数) で登録する（screens-*.js）。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const { h, clear, reveal } = EC.u;
  const S = EC.speech;
  const store = EC.store;

  const $main = document.getElementById('main');
  const $actions = document.getElementById('actions');
  const $back = document.getElementById('btn-back');
  const $stop = document.getElementById('btn-voice-stop');
  const $banner = document.getElementById('banner');
  const $live = document.getElementById('live');

  const state = {
    rec: null, // いま開いている計画の記録 { id, plan, names, versions, sample, ... }
    facts: null, // { facts, shelters, sources }
    settings: { tts: store.getSetting('tts', true), continuous: store.getSetting('continuous', false) },
    screen: null,
  };

  const screens = {};
  let scope = null;
  let backTarget = null;

  /** 画面ごとの範囲。画面を離れるとき、読み上げ・聞き取り・地図などを止める。 */
  function newScope() {
    if (scope) scope.end();
    scope = {
      alive: true,
      fns: [],
      onLeave(fn) { this.fns.push(fn); },
      end() {
        this.alive = false;
        for (const fn of this.fns) { try { fn(); } catch (e) { /* 片づけの失敗は無視 */ } }
        this.fns = [];
      },
    };
    return scope;
  }

  // --- 名前の置き換え（R-02） -------------------------------------------------

  const T = (text) => reveal(text, state.rec ? state.rec.names : []);

  // --- 読み上げ（U-03。止めるボタンは、読み上げている間だけ上に出す） -----------------

  function say(rawText) {
    if (!state.settings.tts || !S.ttsSupported || !rawText) return Promise.resolve(false);
    // ネットワークの声のときは、読む文が外部に送られるので、本名に戻さずに読む
    return S.speak(S.usesRemoteVoice() ? rawText : T(rawText));
  }

  S.onSpeakingChange((speaking) => { $stop.hidden = !speaking; });
  $stop.addEventListener('click', () => S.cancel());

  function announce(text) {
    $live.textContent = '';
    window.setTimeout(() => { $live.textContent = text; }, 30);
  }

  // --- 画面の切り替え -----------------------------------------------------

  function parseHash() {
    const raw = window.location.hash.replace(/^#\/?/, '');
    const [name, query] = raw.split('?');
    const params = {};
    for (const [k, v] of new URLSearchParams(query || '')) params[k] = v;
    return { name: name || '', params };
  }

  function go(name, params, opts) {
    const q = params && Object.keys(params).length ? `?${new URLSearchParams(params).toString()}` : '';
    const hash = `#/${name}${q}`;
    if (window.location.hash === hash) render();
    else if (opts && opts.replace) window.location.replace(hash);
    else window.location.hash = hash;
  }

  function setBack(target, params) {
    backTarget = target ? { name: target, params } : null;
    $back.setAttribute('aria-disabled', backTarget ? 'false' : 'true');
  }

  $back.addEventListener('click', () => {
    if (!backTarget) return;
    go(backTarget.name, backTarget.params);
  });

  /** 下のボタン。1 画面で押すボタンは 2 つまで（話す・それでいい など）。 */
  function setActions(list) {
    clear($actions);
    const nodes = {};
    for (const a of list || []) {
      const cls = ['btn'];
      if (a.kind === 'primary') cls.push('btn-primary');
      if (a.kind === 'confirm') cls.push('btn-confirm');
      if (a.listening) cls.push('btn-listening');
      const button = h('button', { type: 'button', class: cls.join(' '), id: a.id || null, disabled: a.disabled || null },
        a.listening ? h('span', { class: 'btn-dot', 'aria-hidden': 'true' }) : null, a.label);
      button.addEventListener('click', () => { if (!button.disabled && a.onClick) a.onClick(button); });
      $actions.append(button);
      if (a.id) nodes[a.id] = button;
    }
    return nodes;
  }

  function setBanner() {
    const mock = EC.api.getMode() === 'mock';
    $banner.hidden = !mock;
    $banner.textContent = mock ? '試験用: サーバー未接続。サンプルの値で動いています。' : '';
  }

  // --- 計画の保存（端末の中だけ） ----------------------------------------------

  function save() {
    if (state.rec) {
      store.savePlan(state.rec);
      store.setSession('rec', state.rec.id);
    }
  }

  function openRecord(rec) {
    // サンプルの計画は、同じ人について 1 件だけ残す（試すたびに一覧が増えないように）
    if (rec.sample) {
      for (const old of store.listPlans()) {
        if (old.sample === rec.sample && old.id !== rec.id) store.deletePlan(old.id);
      }
    }
    state.rec = rec;
    state.facts = null;
    save();
  }

  function loadSessionRecord() {
    const id = store.getSession('rec');
    if (!state.rec && id) state.rec = store.getPlan(id);
    return state.rec;
  }

  /** サーバーが返した計画に、座標が無いときは、手元の座標を戻す（座標は端末とサーバーの間だけで扱う）。 */
  function mergeGeometry(next, prev) {
    if (!next.home && prev.home) next.home = prev.home;
    for (const s of next.supporters || []) {
      if (!s.location) {
        const old = (prev.supporters || []).find((x) => x.supporter_id === s.supporter_id);
        if (old && old.location) s.location = old.location;
      }
    }
    return next;
  }

  /** 「記録」（フックが止めた書き込み）の画面への入口。ボタンの数には含めない、本文の中の行。 */
  function auditLink(from) {
    return h('button', { type: 'button', class: 'btn-row nav-row', onclick: () => go('audit', { from }) }, 'AI の書き込みを止めた記録を見る');
  }

  /** 画面の見出しに使う、計画の人の呼び方（本名は端末の中だけ）。 */
  function personLabel(rec) {
    const person = (rec.names || []).find((n) => n.role === 'person');
    return (person && person.call) || rec.title || rec.plan.alias;
  }

  /** 自宅の事実（浸水深・津波到達時間・標高）と、近くの避難先。取得できないときは facts を null にして、地図は点と線だけにする。 */
  async function ensureFacts(rec) {
    if (state.facts) return state.facts;
    const f = await EC.api.facts(rec.plan);
    state.facts = f || { facts: null, shelters: [], sources: {} };
    // 出典の表示に使うので、計画に無い出典は、計画の出典リストに足す
    for (const [id, source] of Object.entries(state.facts.sources || {})) {
      if (rec.plan.sources && !rec.plan.sources[id]) rec.plan.sources[id] = source;
    }
    return state.facts;
  }

  // --- 共通の表示 ----------------------------------------------------------

  const noticePrivacy = () => h('div', { class: 'notice' },
    h('p', null, 'この画面には、実在の要支援者の情報を入力しないでください。'),
    h('p', null, '民生委員には守秘義務があります（民生委員法第15条）。この版は、架空の人物で試すためのものです。'));

  const noticeSafety = () => h('div', { class: 'notice notice-safety' },
    h('p', null, 'この計画は話し合いのたたき台です。揺れたら、すぐ高い所へ。市の情報に従ってください。'));

  /** 処理の内容を言葉で表示する箱（U-05）。 */
  function statusBox(message) {
    const box = h('div', { class: 'status-box', role: 'status' }, message);
    box.update = (text) => { box.textContent = text; };
    return box;
  }

  /** 失敗したときの表示。「もう一度」は、下のボタンで出す。入力した欄は消えない（E3）。 */
  function errorBox(message) {
    return h('div', { class: 'error-box', role: 'alert' }, message);
  }

  function errorText(e) {
    return (e && e.userMessage) || 'うまく動きませんでした。もう一度、試してください。';
  }

  // --- 画面の描画 -----------------------------------------------------------

  function register(name, fn) {
    screens[name] = fn;
  }

  function render() {
    const { name, params } = parseHash();
    const s = newScope();
    S.cancel();
    const fn = screens[name || 'home'] || screens.home;
    const loggedIn = Boolean(EC.api.getToken());
    const target = loggedIn || name === 'login' ? fn : screens.login;
    state.screen = target === screens.login ? 'login' : name || 'home';
    clear($main);
    clear($actions);
    setBanner();
    document.title = '逃げ道の点検屋';
    target({ params, scope: s });
    $main.scrollTop = 0;
    $main.focus({ preventScroll: true });
  }

  window.addEventListener('hashchange', render);

  EC.app = {
    state, $main, $actions, T, say, announce, go, setBack, setActions, setBanner, newScope,
    save, openRecord, loadSessionRecord, mergeGeometry, ensureFacts, personLabel, auditLink,
    noticePrivacy, noticeSafety, statusBox, errorBox, errorText,
    register, render, parseHash,
    scope: () => scope,
  };
})();
