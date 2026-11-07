/*
 * 説明のアニメーションの再生（再生・一時停止・最初から）。
 * アニメーションは CSS の @keyframes で作成している。ここでは Web Animations API で、それらをまとめて操作する。
 * 画面への表示は textContent で行う。
 *
 * キーボード: スペースで再生と一時停止、R または Home で最初から（?rec を付けた録画用の表示でも使える）
 */
(function () {
  'use strict';

  const frame = document.querySelector('.frame');
  const clockEl = document.getElementById('clock');
  const btnMain = document.getElementById('btn-main');
  const btnSub = document.getElementById('btn-sub');
  const linkApp = document.getElementById('link-app');
  const timeEl = document.getElementById('time');
  const controls = document.querySelector('.controls');

  if (new URLSearchParams(window.location.search).has('rec')) document.body.classList.add('rec');

  // Web Animations API が無い古いブラウザでは、アニメーションは自動で再生し、ボタンは隠す
  if (!frame || !clockEl || typeof document.getAnimations !== 'function') {
    if (controls) controls.hidden = true;
    return;
  }

  /** 枠の中の CSS アニメーションをすべて返す。 */
  function animations() {
    return document.getAnimations().filter((a) => {
      const target = a.effect && a.effect.target;
      return target && frame.contains(target);
    });
  }

  const clock = animations().find((a) => a.effect.target === clockEl);
  if (!clock) {
    if (controls) controls.hidden = true;
    return;
  }
  const totalMs = clock.effect.getComputedTiming().endTime;

  function play() {
    if (clock.playState === 'finished') restart();
    else animations().forEach((a) => a.play());
  }
  function pause() {
    animations().forEach((a) => a.pause());
  }
  function restart() {
    animations().forEach((a) => {
      a.currentTime = 0;
      a.play();
    });
  }
  function toggle() {
    if (clock.playState === 'running') pause();
    else play();
  }

  function formatTime(ms) {
    const sec = Math.min(Math.max(Math.floor(ms / 1000), 0), 5999);
    return String(Math.floor(sec / 60)) + ':' + String(sec % 60).padStart(2, '0');
  }

  const LABELS = { playing: '一時停止', paused: '再生', ended: 'もう一度' };
  let shownState = '';
  let shownTime = '';

  function stateOf() {
    if (clock.playState === 'finished') return 'ended';
    return clock.playState === 'paused' ? 'paused' : 'playing';
  }

  function render() {
    const state = stateOf();
    if (state !== shownState) {
      shownState = state;
      if (btnMain) btnMain.textContent = LABELS[state];
      // 終わったら、「最初から」の代わりに、アプリを開くリンクを出す（リンクが無いページでは、そのまま）
      if (linkApp && btnSub) {
        linkApp.hidden = state !== 'ended';
        btnSub.hidden = state === 'ended';
      }
    }
    const text = formatTime(clock.currentTime || 0) + ' / ' + formatTime(totalMs);
    if (timeEl && text !== shownTime) {
      shownTime = text;
      timeEl.textContent = text;
    }
    window.requestAnimationFrame(render);
  }

  if (btnMain) btnMain.addEventListener('click', toggle);
  if (btnSub) btnSub.addEventListener('click', restart);

  document.addEventListener('keydown', (event) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    const onControl = event.target instanceof Element && event.target.closest('button, a');
    if ((event.key === ' ' || event.code === 'Space') && !onControl) {
      event.preventDefault();
      toggle();
    } else if (event.key === 'r' || event.key === 'R' || event.key === 'Home') {
      restart();
    }
  });

  // 動きを減らす設定のときは、止めた状態で始める（利用者が「再生」を押す）
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) pause();

  render();
})();
