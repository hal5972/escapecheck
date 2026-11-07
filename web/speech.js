/*
 * 音声入力（Web Speech API の SpeechRecognition）と音声読み上げ（speechSynthesis）。
 * - 音声入力が使えないブラウザや接続では、利用できない旨を返し、画面は文字の入力欄を出す（E1）
 * - 音声認識を使うときは、音声が Google のサーバーに送られることを画面に出す（R-04。文言は sttNotice）
 * - 音声読み上げは ja-JP、速度 0.9。端末の中の声を優先する（ネットワークの声は、読む文が外部に送られるため）
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

  function setSpeaking(value) {
    if (speaking === value) return;
    speaking = value;
    listeners.forEach((fn) => fn(value));
  }

  /** 長い文は Chrome が途中で止めることがあるので、句点で区切って順に読む。 */
  function chunks(text) {
    const out = [];
    String(text).split(/(?<=[。！？\n])/).forEach((part) => {
      const t = part.trim();
      if (!t) return;
      if (t.length <= 90) out.push(t);
      else for (let i = 0; i < t.length; i += 90) out.push(t.slice(i, i + 90));
    });
    return out;
  }

  function cancel() {
    token += 1;
    if (synth) synth.cancel();
    setSpeaking(false);
  }

  /** 読み上げる。読み終わる（または止められる）と解決する。読み切ったときだけ true。 */
  function speak(text, opts) {
    const o = opts || {};
    return new Promise((resolve) => {
      const parts = chunks(text || '');
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
        return '音声を文字にできませんでした。通信を確認してください。';
      case 'no-speech':
        return '声が聞こえませんでした。もう一度「話す」を押してください。';
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
    cancel,
    listen,
    isSpeaking: () => speaking,
    onSpeakingChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    usesRemoteVoice: () => Boolean(voice) && !voice.localService,
    sttNotice,
    errorMessage,
  };
})();
