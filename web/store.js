/*
 * 端末の中の保存（F-14、R-03、R-05）。計画・点検の前と後の版・本名は、この端末のブラウザの保存領域だけに置く。
 * 保存領域が使えないとき（プライベートウィンドウなど）は、このページを開いている間だけ覚える。
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});

  const KEY = 'escapecheck.v1';
  let memory = { plans: {}, settings: {} };
  let persistent = true;

  function read() {
    try {
      const raw = window.localStorage.getItem(KEY);
      if (!raw) return { plans: {}, settings: {} };
      const data = JSON.parse(raw);
      return { plans: data.plans || {}, settings: data.settings || {} };
    } catch (e) {
      persistent = false;
      return memory;
    }
  }

  function write(data) {
    memory = data;
    if (!persistent) return;
    try {
      window.localStorage.setItem(KEY, JSON.stringify(data));
    } catch (e) {
      persistent = false;
    }
  }

  function listPlans() {
    const data = read();
    return Object.values(data.plans)
      .sort((a, b) => String(b.updated || '').localeCompare(String(a.updated || '')));
  }

  function getPlan(id) {
    return read().plans[id] || null;
  }

  /** record: { id, plan, names, versions, sample, updated } */
  function savePlan(record) {
    const data = read();
    record.updated = new Date().toISOString();
    data.plans[record.id] = record;
    write(data);
    return record;
  }

  function deletePlan(id) {
    const data = read();
    delete data.plans[id];
    write(data);
  }

  function getSetting(key, fallback) {
    const v = read().settings[key];
    return v === undefined ? fallback : v;
  }

  function setSetting(key, value) {
    const data = read();
    data.settings[key] = value;
    write(data);
  }

  // 合言葉のトークンと、いま開いている計画は、タブを閉じたら忘れる
  function getSession(key) {
    try {
      return window.sessionStorage.getItem(`${KEY}.${key}`);
    } catch (e) {
      return memory[`session:${key}`] || null;
    }
  }

  function setSession(key, value) {
    try {
      if (value === null || value === undefined) window.sessionStorage.removeItem(`${KEY}.${key}`);
      else window.sessionStorage.setItem(`${KEY}.${key}`, value);
    } catch (e) {
      memory[`session:${key}`] = value;
    }
  }

  EC.store = {
    listPlans, getPlan, savePlan, deletePlan, getSetting, setSetting, getSession, setSession,
    isPersistent: () => persistent,
  };
})();
