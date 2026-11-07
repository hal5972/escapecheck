/*
 * サーバーの API の呼び出し（docs/03-design.md の 8 節）。サーバーに接続できないときは、サンプルデータの
 * 代わりの API（mock-api.js）で動く。画面は、どちらで動いているかを意識しない。
 *
 *   POST /api/login      { passphrase }                       → { token }
 *   POST /api/interview  { plan, utterance, speaker }         → { plan, next_question, changed_fields }
 *   POST /api/check      { plan }                             → { job_id }
 *   GET  /api/jobs/{id}                                       → { job_id, state, events, plan, error }
 *   POST /api/approve    { plan, fix_id, by }                 → { plan, results }   （results: 承認の直後にコードが計算し直した結果）
 *   POST /api/verify     { plan }                             → { issues }
 *   POST /api/finalize   { plan }                             → { ok, reasons, plan }   （サーバーに無いときは画面で確認する）
 *   POST /api/facts      { plan, target }                     → { facts, shelters, sources }
 *   GET  /api/samples, /api/samples/{id}                      → サンプルデータ（架空の人物）
 *
 * 計画（plan）には自宅などの座標が入るが、本名と電話番号は入れない（画面の端末だけが持つ）。
 * 利用者に見せるエラーの文には、キーや内部の情報を含めない（利用者向けの短い文だけを使う）。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});

  const MESSAGES = {
    network: '通信できませんでした。もう一度、試してください。',
    timeout: '時間がかかっています。もう一度、試してください。',
    server: 'うまく動きませんでした。もう一度、試してください。',
    auth: '合言葉の確認が切れました。もう一度、合言葉を入力してください。',
  };

  class ApiError extends Error {
    constructor(kind, status, detail) {
      super(MESSAGES[kind] || MESSAGES.server);
      this.name = 'ApiError';
      this.kind = kind;
      this.status = status || 0;
      this.userMessage = detail || MESSAGES[kind] || MESSAGES.server;
    }
  }

  let mode = 'unknown'; // 'server' | 'mock'
  let token = null;
  let onAuthLost = null;

  async function raw(method, path, body, timeoutMs, authed) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs || 60000);
    try {
      const headers = { Accept: 'application/json' };
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      if (authed && token) headers.Authorization = `Bearer ${token}`;
      const res = await fetch(path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ctrl.signal,
        cache: 'no-store',
        credentials: 'same-origin',
      });
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      return { status: res.status, ok: res.ok, data };
    } catch (e) {
      throw new ApiError(e && e.name === 'AbortError' ? 'timeout' : 'network');
    } finally {
      clearTimeout(timer);
    }
  }

  async function call(method, path, body, timeoutMs) {
    const res = await raw(method, path, body, timeoutMs, true);
    if (res.status === 401 || res.status === 403) {
      if (onAuthLost) onAuthLost();
      throw new ApiError('auth', res.status);
    }
    if (!res.ok) throw new ApiError('server', res.status, res.data && typeof res.data.error === 'string' && res.data.error.length < 120 ? res.data.error : undefined);
    return res.data;
  }

  const forcedMock = () => new URLSearchParams(window.location.search).get('mock') === '1';

  /** 合言葉を確認する。サーバーに接続できないときは、サンプルデータで動く（mode が 'mock' になる）。 */
  async function login(passphrase) {
    if (forcedMock()) {
      await EC.mock.api.login(passphrase);
      mode = 'mock';
      token = 'mock-token';
      return { mode };
    }
    let res;
    try {
      res = await raw('POST', 'api/login', { passphrase }, 8000, false);
    } catch (e) {
      res = { status: 0 };
    }
    if (res.status === 401 || res.status === 403) throw new ApiError('auth', res.status, '合言葉が違います。もう一度、入力してください。');
    if (res.ok && res.data && res.data.token) {
      mode = 'server';
      token = res.data.token;
      return { mode };
    }
    if (res.status === 0 || res.status === 404 || res.status === 405 || res.status === 501) {
      await EC.mock.api.login(passphrase);
      mode = 'mock';
      token = 'mock-token';
      return { mode };
    }
    // 合言葉の間違いが続いたときなど、サーバーが返す利用者向けの文があれば、それを見せる
    throw new ApiError('server', res.status, res.data && typeof res.data.error === 'string' && res.data.error.length < 120 ? res.data.error : undefined);
  }

  function resume(savedToken, savedMode) {
    token = savedToken;
    mode = savedMode;
  }

  const useMock = () => mode === 'mock';

  async function interview(plan, utterance, speaker) {
    if (useMock()) return EC.mock.api.interview(plan, utterance, speaker);
    return call('POST', 'api/interview', { plan, utterance, speaker: speaker || 'minsei' }, 60000);
  }

  async function startCheck(plan) {
    if (useMock()) return EC.mock.api.startCheck(plan);
    const data = await call('POST', 'api/check', { plan }, 30000);
    return data.job_id;
  }

  async function getJob(jobId) {
    if (useMock()) return EC.mock.api.getJob(jobId);
    return call('GET', `api/jobs/${encodeURIComponent(jobId)}`, undefined, 15000);
  }

  /** 承認。{ plan, results } を返す。results は、承認の直後にサーバーのコードが計算し直した結果（シナリオの ID → 結果）。無いこともある。 */
  async function approve(plan, fixId, by) {
    if (useMock()) return EC.mock.api.approve(plan, fixId, by);
    const data = await call('POST', 'api/approve', { plan, fix_id: fixId, by: by || 'minsei' }, 30000);
    if (data && data.plan) return { plan: data.plan, results: data.results && typeof data.results === 'object' ? data.results : null };
    return { plan: data, results: null };
  }

  /** サーバーにあるサンプルデータ（架空の人物）。無いときは空。 */
  async function listSamples() {
    if (useMock()) return [];
    try {
      const data = await call('GET', 'api/samples', undefined, 10000);
      return (data && data.samples) || [];
    } catch (e) {
      return [];
    }
  }

  async function getSample(id) {
    if (useMock()) return null;
    try {
      return await call('GET', `api/samples/${encodeURIComponent(id)}`, undefined, 10000);
    } catch (e) {
      return null;
    }
  }

  async function verify(plan) {
    if (useMock()) return EC.mock.api.verify(plan);
    const data = await call('POST', 'api/verify', { plan }, 60000);
    return Array.isArray(data) ? data : (data && data.issues) || [];
  }

  /** 完成の確認。サーバーに無いときは、未確認の項目が残っていないかだけ画面で確認する。 */
  async function finalize(plan) {
    if (useMock()) return EC.mock.api.finalize(plan);
    try {
      return await call('POST', 'api/finalize', { plan }, 30000);
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 405 || e.status === 501)) {
        const open = (plan.issues || []).filter((i) => !i.acknowledged);
        return open.length ? { ok: false, reasons: open.map((i) => i.message), plan } : { ok: true, reasons: [], plan: { ...plan, status: 'final' } };
      }
      throw e;
    }
  }

  /** 自宅の事実（浸水深・津波到達時間・標高）と、近くの避難先。取得できないときは null（地図は点と線だけにする）。 */
  async function facts(plan) {
    try {
      if (useMock()) return await EC.mock.api.facts(plan);
      const data = await call('POST', 'api/facts', { plan, target: 'home' }, 20000);
      if (!data) return null;
      if (data.facts) return { facts: data.facts, shelters: data.shelters || [], sources: data.sources || {} };
      if (data.target) return { facts: data, shelters: [], sources: {} };
      return null;
    } catch (e) {
      return null;
    }
  }

  EC.api = {
    ApiError,
    login,
    resume,
    interview,
    startCheck,
    getJob,
    approve,
    listSamples,
    getSample,
    verify,
    finalize,
    facts,
    getMode: () => mode,
    getToken: () => token,
    setAuthLostHandler(fn) { onAuthLost = fn; },
    logout() { token = null; },
  };
})();
