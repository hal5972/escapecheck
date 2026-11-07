/*
 * 起動: 保存された合言葉のトークンを引き継ぎ、画面を開く。
 * 試験用: ?audit=1 で、文字とボタンの大きさを確認するスクリプト（audit.js）を読み込む。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});
  const A = EC.app;
  const store = EC.store;
  const api = EC.api;

  // 合言葉の確認が切れたとき（サーバーを再起動したときなど）は、合言葉の画面にもどる
  api.setAuthLostHandler(() => {
    api.logout();
    store.setSession('token', null);
    store.setSession('mode', null);
    window.location.hash = '#/login';
  });

  const token = store.getSession('token');
  const mode = store.getSession('mode');
  if (token && mode) api.resume(token, mode);

  if (!window.location.hash) {
    window.history.replaceState(null, '', token ? '#/home' : '#/login');
  }
  A.render();

  const query = new URLSearchParams(window.location.search);
  if (query.get('audit') === '1') {
    const script = document.createElement('script');
    script.src = 'audit.js';
    document.body.appendChild(script);
  }
})();
