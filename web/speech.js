/*
 * 音声入力（Web Speech API の SpeechRecognition）と音声読み上げ（speechSynthesis）。
 * - 音声入力が使えないブラウザや接続では、利用できない旨を返し、画面は文字の入力欄を出す（E1）
 * - 音声認識を使うときは、音声が Google のサーバーに送られることを画面に出す（R-04。文言は sttNotice）
 * - 音声読み上げは ja-JP、速度 0.9。端末の中の声を優先する（ネットワークの声は、読む文が外部に送られるため）
 * - 読み上げに渡す文だけを、読み仮名に置き換える（readings.json の readings。画面の表示は漢字のまま）
 * - 画面に表示する文と同じ文が audio/manifest.json の items にあれば、合成音声のファイル（audio/<file>）を再生し、無ければ読み上げる
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
  // 読み上げに渡す文だけを置き換える（画面の表示は漢字のまま）。一覧は readings.json の readings（text の書き方を reading の読みに）。
  // 合成音声のファイルを作るスクリプトも、同じファイルを読む。読み込めないときは、下の予備を使う

  const FALLBACK_READINGS = { '何分': 'なんぷん', '間に合': 'まにあ' };

  /**
   * 一覧を整える。{"readings": [{ "text": 書き方, "reading": 読み }]} の形を読む（{"書き方": "読み"} の形も受ける）。
   * 書き方と読みが文字のものだけを使い、長い書き方から順に置き換える。
   */
  function sortReadings(data) {
    let pairs = [];
    if (data && Array.isArray(data.readings)) {
      pairs = data.readings.filter((r) => r && typeof r.text === 'string' && typeof r.reading === 'string').map((r) => [r.text, r.reading]);
    } else if (data && typeof data === 'object' && !Array.isArray(data)) {
      pairs = Object.entries(data).filter(([, reading]) => typeof reading === 'string');
    }
    return pairs.filter(([word]) => word).sort((a, b) => b[0].length - a[0].length);
  }

  let readings = sortReadings(FALLBACK_READINGS);

  if (typeof fetch === 'function') {
    fetch('readings.json')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { const list = sortReadings(data); if (list.length) readings = list; })
      .catch(() => { /* 読み込めないときは、予備の一覧を使う */ });
  }

  /** 読み上げる文の、読み仮名への置き換え。 */
  function applyReadings(text) {
    let out = String(text || '');
    for (const [word, reading] of readings) out = out.split(word).join(reading);
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
    endClip(null); // 合成音声のファイルの再生中なら、止める（止められたときは null を返す）
    setSpeaking(false);
  }

  // --- 合成音声のファイル ----------------------------------------------------
  // audio/manifest.json の items（画面に表示する文 → { file, ... }）に、読み上げる文と同じ文があれば、audio/<file> を再生する。
  // 無いとき（manifest.json が無いときも）は、ブラウザの読み上げで読む。ファイルは、合成音声のスクリプトで作る

  const CLIP_DIR = 'audio/';
  let clipItems = null; // 画面に表示する文 → { file, ... }

  let clipLoaded = false;
  const clipReady = (typeof fetch === 'function'
    ? fetch(`${CLIP_DIR}manifest.json`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (data && data.items && typeof data.items === 'object' && !Array.isArray(data.items)) clipItems = data.items; })
      .catch(() => { /* 音声ファイルが無いときは、読み上げる */ })
    : Promise.resolve()
  ).then(() => { clipLoaded = true; });

  /** 画面に表示する文に合う音声ファイルの名前。無いとき、名前が安全でないときは null。 */
  function clipFileFor(shownText) {
    if (!clipItems || !Object.prototype.hasOwnProperty.call(clipItems, shownText)) return null;
    const item = clipItems[shownText];
    const file = item && typeof item.file === 'string' ? item.file : '';
    return /^[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(file) && !file.includes('..') ? file : null;
  }

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

  /** 音声ファイルを再生する。最後まで再生したら true、再生できないときは false、止められたときは null。 */
  function playClip(file) {
    if (typeof Audio === 'undefined') return Promise.resolve(false);
    cancel();
    const mine = ++token;
    return new Promise((resolve) => {
      const audio = new Audio(`${CLIP_DIR}${file}`);
      clipAudio = audio;
      clipResolve = resolve;
      audio.onplaying = () => { if (mine === token) setSpeaking(true); };
      audio.onended = () => { setSpeaking(false); endClip(true); };
      audio.onerror = () => { setSpeaking(false); endClip(false); };
      const p = audio.play();
      if (p && p.catch) {
        p.catch(() => {
          // 自動再生が許されないとき・ファイルが読めないとき: 読み上げに切り替える
          if (clipAudio !== audio) return; // すでに終わった（別の再生に替わった）
          setSpeaking(false);
          endClip(false);
        });
      }
    });
  }

  /** ブラウザの読み上げ。読み終わる（または止められる）と解決する。読み切ったときだけ true。 */
  function speakWithVoice(text, o) {
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

  /**
   * 読み上げる。読み終わる（または止められる）と解決する。読み切ったときだけ true。
   * opts: { clipText（画面に表示する文。音声ファイルを探す文。省略すると text）, rate }
   * 音声ファイルがあれば再生し、無い（再生できない）ときは、ブラウザの読み上げで読む。止められたときは、読み上げに切り替えない。
   */
  async function speak(text, opts) {
    const o = opts || {};
    const shown = typeof o.clipText === 'string' ? o.clipText : String(text || '');
    if (shown) {
      if (!clipLoaded) await Promise.race([clipReady, new Promise((resolve) => setTimeout(resolve, 1500))]); // 起動の直後は、一覧を待つ
      const file = clipFileFor(shown);
      if (file) {
        const played = await playClip(file);
        if (played === true) return true;
        if (played === null) return false; // 止められた
      }
    }
    return speakWithVoice(text, o);
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
