"""合成音声を作るスクリプト（scripts/make_voice.py）のテスト。ElevenLabs の API は呼ばない。"""

import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from app import orchestrator

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("make_voice", ROOT / "scripts" / "make_voice.py")
mv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mv)

FAKE_KEY = "el-test-" + "k" * 24


class SpeechTextTest(unittest.TestCase):
    def test_longer_words_are_replaced_first(self):
        readings = {"何": "なに", "何分": "なんぷん", "間に合": "まにあ"}
        self.assertEqual(mv.to_speech("何分くらいで間に合いますか。何が", readings),
                         "なんぷんくらいでまにあいますか。なにが")

    def test_default_readings_fix_known_misreadings(self):
        speech = mv.to_speech("家を出るまで何分くらいかかりますか。4分で間に合います。間に合わない", mv.DEFAULT_READINGS)
        self.assertIn("なんぷん", speech)
        self.assertNotIn("何分", speech)
        self.assertNotIn("間に合", speech)
        self.assertIn("まにあわない", speech)

    def test_readings_file_uses_the_same_format_as_the_screen(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "readings.json"
            screen_format = {"_notice": "説明", "readings": [{"text": "新居", "reading": "にい"}]}
            path.write_text(json.dumps(screen_format, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(mv.load_readings(path), {"新居": "にい"})
            path.write_text(json.dumps({"新居": "にい"}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(mv.load_readings(path), {"新居": "にい"})
            for bad in (["新居", "にい"], {"readings": [{"text": "新居"}]}, {"readings": [{"text": "", "reading": "x"}]}):
                path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(mv.VoiceError):
                    mv.load_readings(path)
            self.assertEqual(mv.load_readings(Path(d) / "none.json"), mv.DEFAULT_READINGS)

    def test_the_screen_readings_file_can_be_read(self):
        readings = mv.load_readings()
        self.assertEqual(readings.get("何分"), "なんぷん")
        self.assertEqual(readings.get("間に合"), "まにあ")


class TextsTest(unittest.TestCase):
    def test_question_templates_are_read_without_import(self):
        self.assertEqual(mv.question_templates(), orchestrator.QUESTION_TEMPLATES)

    def test_app_texts_include_questions_and_screen_texts_once(self):
        texts = mv.app_texts()
        for text in list(orchestrator.QUESTION_TEMPLATES.values()) + mv.SCREEN_TEXTS:
            self.assertIn(text, texts)
        self.assertEqual(len(texts), len(set(texts)))

    def test_sample_texts_are_the_fictional_utterances(self):
        questions, utterances = mv.sample_texts(ROOT / "web" / "mock" / "yamada.json")
        self.assertTrue(questions)
        self.assertTrue(utterances)
        self.assertTrue(any("山田さん" in u for u in utterances))

    def test_hashed_name_is_stable_and_differs_by_text(self):
        self.assertEqual(mv.hashed_name("あ"), mv.hashed_name("あ"))
        self.assertNotEqual(mv.hashed_name("あ"), mv.hashed_name("い"))
        self.assertTrue(mv.hashed_name("あ").endswith(".wav"))


class GenerateTest(unittest.TestCase):
    def test_unchanged_texts_are_not_made_again(self):
        calls = []

        def synth(speech):
            calls.append(speech)
            return b"RIFF" + speech.encode("utf-8")

        with tempfile.TemporaryDirectory() as d, mock.patch("sys.stdout", io.StringIO()):
            out = Path(d)
            items = [("何分くらいですか。", "a.wav"), ("間に合います。", "b.wav")]
            meta = {"voice": "v1", "model": "m", "speed": "None"}
            self.assertEqual(mv.generate(items, out, synth, readings=mv.DEFAULT_READINGS, meta=meta), 2)
            self.assertEqual(mv.generate(items, out, synth, readings=mv.DEFAULT_READINGS, meta=meta), 0)
            self.assertEqual(calls, ["なんぷんくらいですか。", "まにあいます。"])
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["items"]["何分くらいですか。"]["file"], "a.wav")
            self.assertEqual(manifest["items"]["何分くらいですか。"]["speech"], "なんぷんくらいですか。")
            # 声を変えたら作り直す
            meta2 = dict(meta, voice="v2")
            self.assertEqual(mv.generate(items, out, synth, readings=mv.DEFAULT_READINGS, meta=meta2), 2)

    def test_dry_run_calls_no_api_and_writes_nothing(self):
        def fail(*args, **kwargs):
            raise AssertionError("API を呼んではいけない")

        with tempfile.TemporaryDirectory() as d, mock.patch.object(mv.urllib.request, "urlopen", fail), \
                mock.patch.dict(os.environ, {mv.KEY_ENV: ""}), mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(mv.main(["questions", "--voice", "x", "--dry-run", "--out", d]), 0)
            self.assertEqual(list(Path(d).iterdir()), [])


class ApiTest(unittest.TestCase):
    def test_missing_key_is_reported(self):
        with mock.patch.dict(os.environ, {mv.KEY_ENV: ""}), mock.patch.object(mv, "windows_user_env", return_value=""):
            with self.assertRaises(mv.VoiceError) as ctx:
                mv.api_key()
        self.assertIn(mv.KEY_ENV, str(ctx.exception))

    def test_api_error_message_does_not_contain_the_key(self):
        body = io.BytesIO(f"invalid key {FAKE_KEY}".encode("utf-8"))
        error = urllib.error.HTTPError("https://api.elevenlabs.io/v1/models", 401, "Unauthorized", {}, body)
        with mock.patch.object(mv.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(mv.VoiceError) as ctx:
                mv.request("GET", "/v1/models", key=FAKE_KEY)
        self.assertNotIn(FAKE_KEY, str(ctx.exception))
        self.assertIn("401", str(ctx.exception))

    def test_non_wav_response_is_rejected(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch.dict(os.environ, {mv.KEY_ENV: FAKE_KEY}), \
                mock.patch.object(mv.urllib.request, "urlopen", return_value=Response(b"ID3mp3")):
            synth = mv.synthesizer(voice="v", model="m", speed=None, seed=1)
            with self.assertRaises(mv.VoiceError):
                synth("こんにちは")


if __name__ == "__main__":
    unittest.main()
