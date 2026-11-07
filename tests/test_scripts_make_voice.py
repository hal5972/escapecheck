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

SCRIPT = """# 台本

説明の文。

| # | 場面 | 確認 | 表示の文 | 読み |
|---|---|---|---|---|
| 1 | はじめ | | 夜は、何階で寝ていますか。 | よるは、なんがいでねていますか。 |
| 2 | つぎ | 要確認 | 津波は十数分で来ます。 | つなみは、じゅうすうふんできます。 |
| 3 | おわり | | デイサービスはありますか。 | デイサービスはありますか。 |

| 別の表 | 列 |
|---|---|
| 読まない | 行 |
"""


def write(directory: str, text: str, name: str = "script.md") -> Path:
    path = Path(directory) / name
    path.write_text(text, encoding="utf-8")
    return path


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ScriptTest(unittest.TestCase):
    def test_script_rows_are_read_in_order_with_numbers_and_pending(self):
        with tempfile.TemporaryDirectory() as d:
            rows = mv.read_script(write(d, SCRIPT))
        self.assertEqual([r.number for r in rows], ["1", "2", "3"])
        self.assertEqual(rows[0].text, "夜は、何階で寝ていますか。")
        self.assertEqual(rows[0].speech, "よるは、なんがいでねていますか。")
        self.assertEqual([r.pending for r in rows], [False, True, False])

    def test_pending_rows_are_not_made_and_names_keep_their_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            items, pending = mv.line_items(mv.read_script(write(d, SCRIPT)), ".wav")
        self.assertEqual([name for _, _, name in items], ["01.wav", "03.wav"])
        self.assertEqual([r.number for r in pending], ["2"])

    def test_rows_without_a_number_column_are_numbered_in_order(self):
        text = "| 表示の文 | 読み |\n|---|---|\n| 文 | ぶん |\n| 次 | つぎ |\n"
        with tempfile.TemporaryDirectory() as d:
            items, pending = mv.line_items(mv.read_script(write(d, text)), ".mp3")
        self.assertEqual([name for _, _, name in items], ["01.mp3", "02.mp3"])
        self.assertEqual(pending, [])

    def test_broken_scripts_are_rejected(self):
        broken = [
            "説明だけで、表が無い",
            "| 表示の文 | 読み |\n|---|---|\n| 文 | |\n",  # 読みが空
            "| # | 表示の文 | 読み |\n|---|---|---|\n| 1 | 文 | ぶん |\n| 1 | 次 | つぎ |\n",  # 同じ番号
            "| # | 表示の文 | 読み |\n|---|---|---|\n| ../a | 文 | ぶん |\n",  # ファイル名にできない番号
            "| # | 表示の文 | 読み |\n|---|---|---|\n| 1 | 文 |\n",  # 列が足りない
        ]
        with tempfile.TemporaryDirectory() as d:
            for text in broken:
                with self.subTest(text=text), self.assertRaises(mv.VoiceError):
                    mv.read_script(write(d, text))
            with self.assertRaises(mv.VoiceError):
                mv.read_script(Path(d) / "none.md")


class SpeechCheckTest(unittest.TestCase):
    def test_kana_and_punctuation_are_accepted(self):
        for speech in ("ひゃくメートルあるくのに、なんぷんくらいかかりますか。", "「それでいい」をおすと、てんけんします。"):
            mv.check_speech(speech)

    def test_kanji_digits_and_latin_letters_are_rejected(self):
        for speech in ("なん分", "100めーとる", "１０めーとる", "Claudeがみつけます", "ひとびと々"):
            with self.subTest(speech=speech), self.assertRaises(mv.VoiceError):
                mv.check_speech(speech)

    def test_words_in_double_angle_brackets_are_sent_as_written(self):
        speech = "《津波到達時間》と、ひなんさきは、くにのデータをつかいます。"
        mv.check_speech(speech)
        self.assertEqual(mv.to_send(speech), "津波到達時間と、ひなんさきは、くにのデータをつかいます。")
        for bad in ("《津波》到達じかん", "《津波到達時間と", "津波到達時間》と", "《》なし"):
            with self.subTest(speech=bad), self.assertRaises(mv.VoiceError):
                mv.check_speech(bad)


class AppScriptTest(unittest.TestCase):
    def test_every_app_text_has_a_kana_reading_in_the_app_script(self):
        items = mv.app_items(mv.APP_SCRIPT, ".wav")
        self.assertEqual([text for text, _, _ in items], mv.app_texts())
        for text, speech, _ in items:
            with self.subTest(text=text):
                mv.check_speech(speech)

    def test_a_text_missing_from_the_script_stops_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            path = write(d, "| 表示の文 | 読み |\n|---|---|\n| 文 | ぶん |\n")
            with self.assertRaises(mv.VoiceError) as ctx:
                mv.app_items(path, ".wav")
        self.assertIn(orchestrator.QUESTION_TEMPLATES["walk_speed"], str(ctx.exception))


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
        self.assertTrue(mv.hashed_name("あ", ".mp3").endswith(".mp3"))
        self.assertEqual(mv.extension("mp3_44100_128"), ".mp3")
        self.assertEqual(mv.extension("wav_24000"), ".wav")


class GenerateTest(unittest.TestCase):
    def test_unchanged_texts_are_not_made_again(self):
        calls = []

        def synth(speech):
            calls.append(speech)
            return b"RIFF" + speech.encode("utf-8")

        with tempfile.TemporaryDirectory() as d, mock.patch("sys.stdout", io.StringIO()):
            out = Path(d)
            items = [("何分くらいですか。", "なんぷんくらいですか。", "a.wav"), ("間に合います。", "まにあいます。", "b.wav")]
            meta = {"voice": "v1", "model": "m", "speed": "None"}
            self.assertEqual(mv.generate(items, out, synth, meta=meta), 2)
            self.assertEqual(mv.generate(items, out, synth, meta=meta), 0)
            self.assertEqual(calls, ["なんぷんくらいですか。", "まにあいます。"])
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["items"]["何分くらいですか。"]["file"], "a.wav")
            self.assertEqual(manifest["items"]["何分くらいですか。"]["speech"], "なんぷんくらいですか。")
            # 声を変えたら作り直す。読みを直したら、その文だけ作り直す
            self.assertEqual(mv.generate(items, out, synth, meta=dict(meta, voice="v2")), 2)
            items[1] = ("間に合います。", "まにあいますよ。", "b.wav")
            self.assertEqual(mv.generate(items, out, synth, meta=dict(meta, voice="v2")), 1)

    def test_the_marks_are_removed_before_sending(self):
        sent = []

        def synth(speech):
            sent.append(speech)
            return b"RIFF"

        with tempfile.TemporaryDirectory() as d, mock.patch("sys.stdout", io.StringIO()):
            items = [("津波到達時間と避難先", "《津波到達時間》と、ひなんさき", "a.wav")]
            self.assertEqual(mv.generate(items, Path(d), synth, meta={}), 1)
            manifest = json.loads((Path(d) / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(sent, ["津波到達時間と、ひなんさき"])
        self.assertEqual(manifest["items"]["津波到達時間と避難先"]["speech"], "《津波到達時間》と、ひなんさき")

    def test_a_reading_with_kanji_stops_before_any_audio_is_made(self):
        def synth(speech):
            raise AssertionError("音声を作ってはいけない")

        with tempfile.TemporaryDirectory() as d, mock.patch("sys.stdout", io.StringIO()):
            items = [("文", "ぶん", "a.wav"), ("何分", "何ぷん", "b.wav")]
            with self.assertRaises(mv.VoiceError):
                mv.generate(items, Path(d), synth, meta={})
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_dry_run_calls_no_api_and_writes_nothing(self):
        def fail(*args, **kwargs):
            raise AssertionError("API を呼んではいけない")

        with tempfile.TemporaryDirectory() as d, mock.patch.object(mv.urllib.request, "urlopen", fail), \
                mock.patch.dict(os.environ, {mv.KEY_ENV: ""}), mock.patch("sys.stdout", io.StringIO()) as out:
            out_dir = Path(d) / "out"
            self.assertEqual(mv.main(["questions", "--voice", "x", "--dry-run", "--out", str(out_dir)]), 0)
            script = write(d, SCRIPT)
            self.assertEqual(mv.main(["lines", str(script), "--voice", "x", "--dry-run", "--out", str(out_dir)]), 0)
            self.assertFalse(out_dir.exists())
        self.assertIn("要確認のため作りません（2）", out.getvalue())

    def test_limit_makes_only_the_first_items(self):
        made = []

        def synth(speech):
            made.append(speech)
            return b"RIFF"

        with tempfile.TemporaryDirectory() as d, mock.patch.object(mv, "synthesizer", return_value=synth), \
                mock.patch("sys.stdout", io.StringIO()):
            script = write(d, SCRIPT)
            self.assertEqual(mv.main(["lines", str(script), "--voice", "x", "--limit", "1", "--out", d]), 0)
        self.assertEqual(made, ["よるは、なんがいでねていますか。"])


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

    def test_request_sends_the_voice_settings(self):
        sent = []

        def urlopen(req, timeout):
            sent.append(req)
            return Response(b"RIFF....")

        with mock.patch.dict(os.environ, {mv.KEY_ENV: FAKE_KEY}), \
                mock.patch.object(mv.urllib.request, "urlopen", side_effect=urlopen):
            synth = mv.synthesizer(voice="v", model="m", speed=0.9, seed=1)
            synth("こんにちは")
        body = json.loads(sent[0].data.decode("utf-8"))
        self.assertEqual(body["text"], "こんにちは")
        self.assertEqual(body["voice_settings"], {"stability": 0.5, "similarity_boost": 0.75, "speed": 0.9})
        self.assertIn("output_format=wav_24000", sent[0].full_url)
        self.assertNotIn(FAKE_KEY, sent[0].full_url)

    def test_non_audio_response_is_rejected(self):
        with mock.patch.dict(os.environ, {mv.KEY_ENV: FAKE_KEY}), \
                mock.patch.object(mv.urllib.request, "urlopen", return_value=Response(b"ID3mp3")):
            synth = mv.synthesizer(voice="v", model="m", speed=None, seed=1)
            with self.assertRaises(mv.VoiceError):
                synth("こんにちは")
        with mock.patch.dict(os.environ, {mv.KEY_ENV: FAKE_KEY}), \
                mock.patch.object(mv.urllib.request, "urlopen", return_value=Response(b"ID3mp3")):
            synth = mv.synthesizer(voice="v", model="m", speed=None, seed=1, output_format="mp3_44100_128")
            self.assertEqual(synth("こんにちは"), b"ID3mp3")


if __name__ == "__main__":
    unittest.main()
