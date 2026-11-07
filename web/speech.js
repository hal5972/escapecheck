/*
 * 音声入力（Web Speech API の SpeechRecognition）と音声読み上げ（speechSynthesis）。
 * - 音声入力が使えないブラウザや接続では、利用できない旨を返し、画面は文字の入力欄を出す（E1）
 * - 音声認識を使うときは、音声が Google のサーバーに送られることを画面に出す（R-04。文言は sttNotice）
 * - 音声読み上げは ja-JP、速度 0.9。端末の中の声を優先する（ネットワークの声は、読む文が外部に送られるため）
 * - 読み上げに渡す文だけを、読み仮名に置き換える（readings.json。画面の表示は漢字のまま）
 * - 決まった質問は、音声ファイル（audio/questions/<欄のキー>.wav。initial・done を含む）があれば再生し、無ければ読み上げる
 */
(function () {
  'use strict';
  const EC = (window.EC = window.EC || {});

  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition || null;
  const synth = 'speechSynthesis' in window ? window.speechSynthesis : null;

  // --- 読み上げ ------------------------------------------------------------

  let voice = null;
  function pickVoice() {
    if (!synth) return;
    const voices = synth.getVoices().filter((v) => /^ja/i.test(v.lang));
    voice = voices.find((v) => v.localService) || voices[0] || null;
  }
  if (synth) {
    pickVoice();
    if (synth.addEventListener) synth.addEventListener('voiceschanged', pickVoice);
  }

  let token = 0;
  let speaking = false;
  const listeners = new Set();

  // --- 読み仮名の置き換え ---------------------------------------------------
  // 読み上げに渡す文だけを置き換える。一覧は readings.json（読み込めないときは、下の予備を使う）

  const FALLBACK_READINGS = [{ text: '何分', reading: 'なんぷん' }, { text: '間に合', reading: 'まにあ' }];
  let readings = FALLBACK_READINGS;

  /** 一覧を整える。書き方と読みが文字のものだけを使い、長い書き方から順に置き換える。 */
  function cleanReadings(list) {
    return (Array.isArray(list) ? list : [])
      .filter((r) => r && typeof r.text === 'string' && r.text && typeof r.reading === 'string' && r.reading)
      .sort((a, b) => b.text.length - a.text.length);
  }

  if (typeof fetch === 'function') {
    fetch('readings.json')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { const list = data ? cleanReadings(data.readings) : []; if (list.length) readings = list; })
      .catch(() => { /* 読み込めないときは、予備の一覧を使う */ });
  }

  /** 読み上げる文の、読み仮名への置き換え。 */
  function applyReadings(text) {
    let out = String(text || '');
    for (const r of readings) out = out.split(r.text).join(r.reading);
    return out;
  }

  function setSpeaking(value) {
    if (speaking === value) return;
    speaking = value;
    listeners.forEach((fn) => fn(value));
  }

  /** 長い文は Chrome が途中で止めることがあるので、句点で区切って順に読む（古い Safari で動かない先読みの正規表現は使わない）。 */
  function chunks(text) {
    const out = [];
    let buf = '';
    for (const ch of String(text)) {
      buf += ch;
      if ('。！？\n'.includes(ch) || buf.length >= 90) {
        const t = buf.trim();
        if (t) out.push(t);
        buf = '';
      }
    }
    const rest = buf.trim();
    if (rest) out.push(rest);
    return out;
  }

  function cancel() {
    token += 1;
    if (synth) synth.cancel();
    endClip(null); // 音声ファイルの再生中なら、止める（止められたときは null を返す）
    setSpeaking(false);
  }

  /** 読み上げる。読み終わる（または止められる）と解決する。読み切ったときだけ true。 */
  function speak(text, opts) {
    const o = opts || {};
    return new Promise((resolve) => {
      const parts = chunks(applyReadings(text || ''));
      if (!synth || !parts.length) {
        resolve(false);
        return;
      }
      cancel();
      const mine = ++token;
      let index = 0;
      let failed = false;
      setSpeaking(true);
      const next = () => {
        if (mine !== token) { resolve(false); return; }
        if (failed || index >= parts.length) {
          setSpeaking(false);
          resolve(!failed);
          return;
        }
        const u = new SpeechSynthesisUtterance(parts[index++]);
        u.lang = 'ja-JP';
        u.rate = o.rate || 0.9;
        if (voice) u.voice = voice;
        u.onend = next;
        u.onerror = () => { failed = true; next(); };
        synth.speak(u);
      };
      next();
    });
  }

  // --- 決まった質問の音声ファイル ---------------------------------------------
  // audio/questions/<欄のキー>.wav（initial.wav・done.wav を含む）があれば再生する。無いときは、読み上げる。

  const CLIP_DIR = 'audio/questions/';
  const clipKnown = new Map(); // 欄のキー → 音声ファイルがあるか（true・false）。一度無いと分かれば、もう探さない
  let clipAudio = null;
  let clipResolve = null;

  function endClip(result) {
    const done = clipResolve;
    clipResolve = null;
    if (clipAudio) {
      clipAudio.onplaying = null;
      clipAudio.onended = null;
      clipAudio.onerror = null;
      try { clipAudio.pause(); } catch (e) { /* すでに止まっている */ }
      clipAudio = null;
    }
    if (done) done(result);
  }

  /** 音声ファイルを再生する。最後まで再生したら true、ファイルが無い・再生できないときは false、止められたときは null。 */
  function playClip(key) {
    if (typeof Audio === 'undefined' || !/^[a-z0-9_]+$/i.test(key || '') || clipKnown.get(key) === false) return Promise.resolve(false);
    cancel();
    const mine = ++token;
    return new Promise((resolve) => {
      const audio = new Audio(`${CLIP_DIR}${key}.wav`);
      clipAudio = audio;
      clipResolve = resolve;
      let started = false;
      audio.onplaying = () => { started = true; clipKnown.set(key, true); if (mine === token) setSpeaking(true); };
      audio.onended = () => { setSpeaking(false); endClip(true); };
      audio.onerror = () => { if (!started) clipKnown.set(key, false); setSpeaking(false); endClip(false); };
      const p = audio.play();
      if (p && p.catch) {
        p.catch((err) => {
          // 自動再生が許されないときは、ファイルの有無が分からないので、覚えない
          if (!err || err.name !== 'NotAllowedError') clipKnown.set(key, false);
          if (clipAudio !== audio) return; // すでに終わった（別の再生に替わった）
          setSpeaking(false);
          endClip(false);
        });
      }
    });
  }

  /** 決まった質問を読む。音声ファイルがあれば再生し、無ければ読み上げる。 */
  async function speakQuestion(key, text, opts) {
    const played = await playClip(key);
    if (played === true) return true;
    if (played === null) return false; // 止められた。読み上げには切り替えない
    return speak(text, opts);
  }

  // --- 音声入力 ------------------------------------------------------------

  const sttSupported = Boolean(Recognition) && (window.isSecureContext !== false);

  /**
   * 聞き取りを始める。無音が続くと自動で止まる。
   * opts: { onInterim(確定した文字, 途中の文字), onEnd({ text, error, heard }), silenceMs, noSpeechMs }
   * 返す値: { stop(), abort() }。使えないときは null
   */
  function listen(opts) {
    if (!sttSupported) return null;
    const o = opts || {};
    const rec = new Recognition();
    rec.lang = 'ja-JP';
    rec.interimResults = true;
    rec.continuous = true;
    rec.maxAlternatives = 1;

    let finalText = '';
    let interim = '';
    let heard = false;
    let ended = false;
    let error = null;
    let timer = null;

    const arm = (ms) => {
      clearTimeout(timer);
      timer = setTimeout(() => { try { rec.stop(); } catch (e) { /* すでに止まっている */ } }, ms);
    };

    rec.onstart = () => arm(o.noSpeechMs || 9000);
    rec.onresult = (ev) => {
      heard = true;
      interim = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      if (o.onInterim) o.onInterim(finalText, interim);
      arm(o.silenceMs || 2600);
    };
    rec.onerror = (ev) => { error = ev.error || 'error'; };
    rec.onend = () => {
      if (ended) return;
      ended = true;
      clearTimeout(timer);
      if (o.onEnd) o.onEnd({ text: (finalText + interim).trim(), error, heard });
    };

    try {
      rec.start();
    } catch (e) {
      if (o.onEnd) o.onEnd({ text: '', error: 'start-failed', heard: false });
      return null;
    }
    return {
      stop() { clearTimeout(timer); try { rec.stop(); } catch (e) { /* 無視 */ } },
      abort() { ended = true; clearTimeout(timer); try { rec.abort(); } catch (e) { /* 無視 */ } },
    };
  }

  /** R-04: 音声認識を使うときの表示。Safari 系は Apple の音声認識になる。 */
  function sttNotice() {
    const ua = navigator.userAgent || '';
    const safariFamily = /Safari/.test(ua) && !/Chrome|Chromium|Edg|OPR/.test(ua);
    const iosChrome = /CriOS/.test(ua);
    if (safariFamily || iosChrome) return '音声は、文字起こしのため Apple のサーバーに送信されることがあります。';
    return '音声は、文字起こしのため Google のサーバーに送信されます。';
  }

  /** 音声認識の失敗を、利用者に見せる短い文にする。 */
  function errorMessage(code) {
    switch (code) {
      case 'not-allowed':
      case 'service-not-allowed':
        return 'マイクが使えません。ブラウザのマイクの許可を確認してください。';
      case 'audio-capture':
        return 'マイクが見つかりません。';
      case 'network':
        return '音声認識ができませんでした。通信を確認してください。';
      case 'no-speech':
        return '音声が聞き取れませんでした。もう一度「話す」を押してください。';
      case 'start-failed':
        return '音声入力を始められませんでした。';
      default:
        return '';
    }
  }

  EC.speech = {
    sttSupported,
    ttsSupported: Boolean(synth),
    speak,
    speakQuestion,
    applyReadings,
    cancel,
    listen,
    isSpeaking: () => speaking,
    onSpeakingChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    usesRemoteVoice: () => Boolean(voice) && !voice.localService,
    sttNotice,
    errorMessage,
  };
})();
