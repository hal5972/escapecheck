"""合成音声（ElevenLabs の音声合成）で、アプリの決まった文の音声と、動画用の音声を作る。

使い方（API キーは環境変数 ELEVENLABS_API_KEY から読む。画面にもログにも出さない）:
  python scripts/make_voice.py models                          # 使えるモデルの ID を表示する
  python scripts/make_voice.py questions --voice <声の ID>      # アプリの決まった文の音声 → web/audio/
  python scripts/make_voice.py sample yamada --voice <声の ID>  # サンプルの発言（架空）の音声 → video/voice/yamada/
  python scripts/make_voice.py lines <文の一覧.txt> --voice <声の ID> --out video/voice/narration  # 1行を1つの音声に
  --dry-run を付けると、送る文を表示するだけで、API は呼ばない

- 送る文は、アプリの決まった文と、架空の人物の文だけにする（実在の人の情報を送らない）
- 声は、ElevenLabs の Voice Design で作った架空の声を使う（実在の人の声を複製しない）
- 読み上げで誤りやすい語（何分、間に合う、地名）は、送る前に読み仮名に置き換える（web/readings.json。無ければ既定の一覧）
- 音声のファイル（web/audio/、video/）はリポジトリに入れない（.gitignore で除外）
- 画面は web/audio/manifest.json を読み、表示している文と同じ文の音声があるときだけ再生する。無ければブラウザの読み上げで読む。
  文を直したら、このスクリプトで作り直す（文が変わった物だけを作る）
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.elevenlabs.io"
KEY_ENV = "ELEVENLABS_API_KEY"
DEFAULT_MODEL = "eleven_v4"
OUTPUT_FORMAT = "wav_24000"  # 44.1kHz の WAV は上位の契約が必要
MANIFEST = "manifest.json"

#: 画面の決まった文（web/ の文と同じにする。違うと、画面は合成音声を使わず、ブラウザの読み上げで読む）
SCREEN_TEXTS = [
    "担当する方について、思いつくままに話してください。",  # 最初の質問（新しく作るとき）
    "聞き取りは、ひととおり終わりました。「それでいい」を押すと、点検します。",  # 聞き取りの終わり
]

#: 読み上げで誤りやすい語の読み（web/readings.json が無いときに使う）
DEFAULT_READINGS = {"何分": "なんぷん", "間に合": "まにあ"}


class VoiceError(RuntimeError):
    """音声を作れなかった。メッセージに API キーを含めない。"""


# ---------------------------------------------------------------------------
# 送る文
# ---------------------------------------------------------------------------


def load_readings(path: Path | None = None) -> dict[str, str]:
    """読み仮名の一覧（書き方 → 読み）。画面と同じ web/readings.json を読む。ファイルが無ければ既定の一覧。

    形は {"readings": [{"text": "書き方", "reading": "読み"}, ...]}（画面の web/speech.js と同じ）。
    {"書き方": "読み"} の形も読める。
    """
    path = path or ROOT / "web" / "readings.json"
    if not path.exists():
        return dict(DEFAULT_READINGS)
    data = json.loads(path.read_text(encoding="utf-8"))
    bad = VoiceError(f'{path.name} の形が正しくありません（{{"readings": [{{"text": "書き方", "reading": "読み"}}]}} の形にしてください）')
    if isinstance(data, dict) and isinstance(data.get("readings"), list):
        out: dict[str, str] = {}
        for item in data["readings"]:
            if not (isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"]
                    and isinstance(item.get("reading"), str)):
                raise bad
            out[item["text"]] = item["reading"]
        return out
    if isinstance(data, dict) and all(isinstance(k, str) and isinstance(v, str) and k and not k.startswith("_")
                                      for k, v in data.items()):
        return data
    raise bad


def to_speech(text: str, readings: dict[str, str]) -> str:
    """読み上げに渡す文にする。長い語から順に読み仮名に置き換える（画面の表示は変えない）。"""
    out = text
    for word in sorted(readings, key=len, reverse=True):
        out = out.replace(word, readings[word])
    return out


def question_templates(path: Path | None = None) -> dict[str, str]:
    """app/orchestrator.py の QUESTION_TEMPLATES を、import せずに取り出す。"""
    path = path or ROOT / "app" / "orchestrator.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names, value = [t.id for t in node.targets if isinstance(t, ast.Name)], node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names, value = [node.target.id], node.value
        else:
            continue
        if "QUESTION_TEMPLATES" in names and value is not None:
            return ast.literal_eval(value)
    raise VoiceError("app/orchestrator.py に QUESTION_TEMPLATES が見つかりません")


def sample_texts(path: Path) -> tuple[list[str], list[str]]:
    """サンプル（web/mock/<名前>.json。架空の人物）の、質問の文と発言の文。サンプルでなければ空。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("interview"), list):
        return [], []
    steps = [s for s in data["interview"] if isinstance(s, dict)]
    questions = [data.get("initial_question")] + [s.get("next_question") for s in steps]
    utterances = [s.get("utterance") for s in steps]
    return [q for q in questions if isinstance(q, str) and q], [u for u in utterances if isinstance(u, str) and u]


def unique(texts: list[str]) -> list[str]:
    seen: set[str] = set()
    return [t for t in texts if not (t in seen or seen.add(t))]


def app_texts() -> list[str]:
    """アプリが読み上げる決まった文（追加の質問、最初の質問、終わりの文、サンプルの質問）。"""
    texts = list(question_templates().values()) + SCREEN_TEXTS
    for path in sorted((ROOT / "web" / "mock").glob("*.json")):
        texts += sample_texts(path)[0]
    return unique(texts)


def hashed_name(text: str) -> str:
    """文から決まるファイル名（同じ文なら同じ名前）。"""
    return "v_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] + ".wav"


def numbered_names(texts: list[str]) -> list[tuple[str, str]]:
    return [(t, f"{i:02d}.wav") for i, t in enumerate(texts, start=1)]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def windows_user_env(name: str) -> str:
    """Windows のユーザーの環境変数を読む（設定したあとに起動し直していないプロセスでも読めるように）。"""
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
            value, _ = winreg.QueryValueEx(reg, name)
        return str(value).strip()
    except OSError:
        return ""


def api_key() -> str:
    key = os.environ.get(KEY_ENV, "").strip() or windows_user_env(KEY_ENV)
    if not key:
        raise VoiceError(f"環境変数 {KEY_ENV} に ElevenLabs の API キーがありません")
    return key


def request(method: str, path: str, *, key: str, body: dict | None = None, query: dict | None = None,
            timeout: float = 120.0) -> bytes:
    url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"xi-api-key": key, "Accept": "*/*"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300].replace(key, "***")
            if e.code == 429 and attempt < 3:
                time.sleep(2 ** attempt * 2)
                continue
            raise VoiceError(f"ElevenLabs の API がエラーを返しました（{e.code}）: {detail}") from None
        except urllib.error.URLError as e:
            raise VoiceError(f"ElevenLabs に接続できませんでした: {e.reason}") from None
    raise VoiceError("ElevenLabs の API が混み合っています。少し待ってから実行し直してください")


def synthesizer(*, voice: str, model: str, speed: float | None, seed: int | None) -> Callable[[str], bytes]:
    key = api_key()

    def synth(speech: str) -> bytes:
        body: dict = {"text": speech, "model_id": model, "language_code": "ja"}
        if speed is not None:
            body["voice_settings"] = {"speed": speed}
        if seed is not None:
            body["seed"] = seed
        audio = request("POST", f"/v1/text-to-speech/{urllib.parse.quote(voice, safe='')}", key=key, body=body,
                        query={"output_format": OUTPUT_FORMAT})
        if not audio.startswith(b"RIFF"):
            raise VoiceError("WAV の音声が返りませんでした")
        return audio

    return synth


# ---------------------------------------------------------------------------
# 作る
# ---------------------------------------------------------------------------


def generate(items: list[tuple[str, str]], out_dir: Path, synth: Callable[[str], bytes] | None, *,
             readings: dict[str, str], meta: dict[str, str], force: bool = False, dry_run: bool = False) -> int:
    """items は（表示の文, ファイル名）の一覧。作った数を返す。

    manifest.json に、表示の文 → ファイル名・読み上げの文・声・モデルを記録する。
    同じ読み上げの文・声・モデルの音声がすでにあれば、作り直さない（force のときは作り直す）。
    """
    manifest_path = out_dir / MANIFEST
    manifest = {"items": {}}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries: dict = manifest.setdefault("items", {})
    made = 0
    for text, name in items:
        speech = to_speech(text, readings)
        entry = {"file": name, "speech": speech, **meta}
        if not force and entries.get(text) == entry and (out_dir / name).exists():
            continue
        print(f"{name}: {speech}")
        if dry_run:
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / name).write_bytes(synth(speech))
        entries[text] = entry
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        made += 1
    return made


def read_lines(path: Path) -> list[str]:
    """1行を1つの音声にする。空の行と # で始まる行は使わない。"""
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ElevenLabs の音声合成で、アプリと動画の音声を作る")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models", help="使えるモデルの ID を表示する")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--voice", required=True, help="声の ID（Voice Design で作った架空の声）")
        p.add_argument("--model", default=DEFAULT_MODEL, help=f"モデルの ID（既定: {DEFAULT_MODEL}）")
        p.add_argument("--speed", type=float, default=None, help="話す速さ（1.0 が標準。遅くするなら 0.9 など）")
        p.add_argument("--seed", type=int, default=1, help="同じ文から同じ音声に近づける値")
        p.add_argument("--force", action="store_true", help="すでにある音声も作り直す")
        p.add_argument("--dry-run", action="store_true", help="送る文を表示するだけで、API を呼ばない")

    p_questions = sub.add_parser("questions", help="アプリの決まった文の音声を作る")
    common(p_questions)
    p_questions.add_argument("--out", default=str(ROOT / "web" / "audio"))
    p_sample = sub.add_parser("sample", help="サンプル（架空の人物）の発言の音声を作る")
    p_sample.add_argument("name", help="web/mock/ の JSON の名前（例: yamada）")
    common(p_sample)
    p_sample.add_argument("--out", default=None)
    p_lines = sub.add_parser("lines", help="文の一覧のファイル（1行に1文）から音声を作る")
    p_lines.add_argument("file")
    common(p_lines)
    p_lines.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    try:
        if args.cmd == "models":
            for m in json.loads(request("GET", "/v1/models", key=api_key())):
                ja = any(lang.get("language_id") == "ja" for lang in m.get("languages", []))
                print(f"{m.get('model_id')} | {m.get('name')} | 日本語: {'あり' if ja else '一覧に無い'}")
            return 0
        if args.cmd == "questions":
            items = [(t, hashed_name(t)) for t in app_texts()]
            out_dir = Path(args.out)
        elif args.cmd == "sample":
            path = ROOT / "web" / "mock" / f"{args.name}.json"
            if not path.exists():
                raise VoiceError(f"サンプルが見つかりません: web/mock/{args.name}.json")
            items = numbered_names(sample_texts(path)[1])
            out_dir = Path(args.out) if args.out else ROOT / "video" / "voice" / args.name
        else:
            items = numbered_names(read_lines(Path(args.file)))
            out_dir = Path(args.out)
        synth = None if args.dry_run else synthesizer(voice=args.voice, model=args.model, speed=args.speed,
                                                      seed=args.seed)
        meta = {"voice": args.voice, "model": args.model, "speed": str(args.speed)}
        made = generate(items, out_dir, synth, readings=load_readings(), meta=meta, force=args.force,
                        dry_run=args.dry_run)
        print(f"{'送る文を表示しました' if args.dry_run else f'{made}件の音声を作りました'}（{len(items)}件中）: {out_dir}")
        return 0
    except VoiceError as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
