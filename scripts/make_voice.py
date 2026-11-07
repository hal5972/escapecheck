"""合成音声（ElevenLabs の音声合成）で、アプリの決まった文の音声と、動画用の音声を作る。

使い方（API キーは環境変数 ELEVENLABS_API_KEY から読む。画面にもログにも出さない）:
  python scripts/make_voice.py models                                        # 使えるモデルの ID を表示する
  python scripts/make_voice.py questions --voice <声の ID>                    # アプリの決まった文の音声 → web/audio/
  python scripts/make_voice.py lines <台本.md> --voice <声の ID> --out <フォルダ>  # 台本の1行を1つの音声に（01.wav…）
  --dry-run を付けると、送る文を表示するだけで、API は呼ばない。--limit 1 で、最初の1件だけを作る（声の試し）

- 合成音声に送るのは、台本の読みの文。読みは漢字かな交じりで書き、読み誤りが分かっている語（何分、間に合う、地名など。
  画面の読み上げと同じ web/readings.json の語）だけを、かなで書く。一覧の語が漢字のまま残っていれば、作らずに止める
  （ひらがなだけの読みは、聞き取りにくい語があった）
- 台本は Markdown の表で、見出しに「表示の文」と「読み」の列を持つ。表示の文は画面と字幕に出す文、読みは合成音声に送る文。
  「#」の列があれば、その番号を音声のファイル名にする。「確認」の列に「要確認」とある行は、音声を作らない。
  アプリの決まった文の台本は scripts/voice_app.md。アプリの文が台本に無ければ、作らずに止める
- 送る文は、アプリの決まった文と、架空の人物の文だけにする（実在の人の情報を送らない）
- 声は、ElevenLabs の音声ライブラリで共有されている声を使う。実在の人の声を、こちらで複製しない
- 音声のファイル（web/audio/、動画の素材）はリポジトリに入れない（.gitignore で除外）
- 画面は web/audio/manifest.json を読み、表示している文と同じ文の音声があるときだけ再生する。無ければブラウザの読み上げで読む。
  文を直したら、台本の表示の文と読みも直して、このスクリプトで作り直す（文・読み・声の設定が変わった物だけを作る）
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.elevenlabs.io"
KEY_ENV = "ELEVENLABS_API_KEY"
DEFAULT_MODEL = "eleven_v4"
DEFAULT_FORMAT = "wav_24000"  # 44.1kHz の WAV は上位の契約が必要
DEFAULT_STABILITY = 0.5  # ElevenLabs のサイトの既定と同じ（依頼者が試した設定）
DEFAULT_SIMILARITY = 0.75
APP_SCRIPT = ROOT / "scripts" / "voice_app.md"
READINGS = ROOT / "web" / "readings.json"
MANIFEST = "manifest.json"

#: 画面の決まった文（web/ の文と同じにする。違うと、画面は合成音声を使わず、ブラウザの読み上げで読む）
SCREEN_TEXTS = [
    "担当する方について、思いつくままに話してください。",  # 最初の質問（新しく作るとき）
    "聞き取りは、ひととおり終わりました。「それでいい」を押すと、点検します。",  # 聞き取りの終わり
]



class VoiceError(RuntimeError):
    """音声を作れなかった。メッセージに API キーを含めない。"""


# ---------------------------------------------------------------------------
# 台本
# ---------------------------------------------------------------------------


class Line(NamedTuple):
    """台本の1行。"""

    number: str  # 「#」の列の値（無ければ表の順の番号）。音声のファイル名に使う
    text: str  # 表示の文（画面と字幕に出す文）
    speech: str  # 読み（合成音声に送る文）
    pending: bool  # 「確認」の列が「要確認」（確認するまで音声を作らない）


def read_script(path: Path) -> list[Line]:
    """台本（Markdown の表）の行を、表の順に読む。

    見出しの行に「表示の文」と「読み」の列がある表だけを読む。「#」の列があれば、その値を音声のファイル名にする
    （行を足したり、要確認の行を飛ばしたりしても、ほかの行のファイル名が変わらないように）。
    「確認」の列に「要確認」とある行は、pending にする。セルの中に | は書けない。
    """
    if not path.exists():
        raise VoiceError(f"台本が見つかりません: {path}")
    rows: list[Line] = []
    cols: dict[str, int] | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        s = line.strip()
        if not s.startswith("|"):
            cols = None  # 表の外
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cols is None:
            if "表示の文" in cells and "読み" in cells:
                cols = {name: cells.index(name) for name in ("#", "表示の文", "読み", "確認") if name in cells}
            continue
        if all(c and set(c) <= set("-: ") for c in cells):  # 見出しの下の区切りの行
            continue
        if max(cols.values()) >= len(cells):
            raise VoiceError(f"{path.name} の {number} 行目: 列の数が見出しと合いません")
        text, speech = cells[cols["表示の文"]], cells[cols["読み"]]
        if not text and not speech:
            continue
        if not (text and speech):
            raise VoiceError(f"{path.name} の {number} 行目: 表示の文か読みが空です")
        num = cells[cols["#"]] if "#" in cols else str(len(rows) + 1)
        if not re.fullmatch(r"[0-9A-Za-z_-]{1,20}", num):
            raise VoiceError(f"{path.name} の {number} 行目: 「#」の列は、数字か英字にしてください")
        pending = "確認" in cols and "要確認" in cells[cols["確認"]]
        rows.append(Line(num, text, speech, pending))
    if not rows:
        raise VoiceError(f"{path.name} に、「表示の文」と「読み」の列がある表がありません")
    numbers = [r.number for r in rows]
    if len(numbers) != len(set(numbers)):
        raise VoiceError(f"{path.name}: 「#」の列に、同じ番号があります")
    return rows


def load_readings(path: Path | None = None) -> dict[str, str]:
    """読み誤りが分かっている語（書き方 → 読み）。画面の読み上げと同じ web/readings.json を読む。

    形は {"readings": [{"text": "書き方", "reading": "読み"}, ...]}（画面の web/speech.js と同じ）。
    """
    path = path or READINGS
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data["readings"]
        out = {i["text"]: i["reading"] for i in items}
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise VoiceError(f"{path.name} を読めません（読み誤りが分かっている語の一覧）: {e}") from None
    if not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in out.items()):
        raise VoiceError(f"{path.name} の形が正しくありません")
    return out


def check_speech(speech: str, readings: dict[str, str]) -> None:
    """読み誤りが分かっている語が、漢字のまま読みに残っていれば止める（その語は、かなで書く）。"""
    found = [w for w in sorted(readings, key=len, reverse=True) if w in speech]
    if found:
        hint = "、".join(f"{w} → {readings[w]}" for w in found)
        raise VoiceError(f"読みに、読み誤りが分かっている語が残っています（{hint}）: {speech}")


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


def app_items(script: Path, ext: str) -> list[tuple[str, str, str]]:
    """アプリの決まった文の（表示の文, 読み, ファイル名）。台本に無い文（要確認の行を含む）があれば、すべて挙げて止める。"""
    speeches = {r.text: r.speech for r in read_script(script) if not r.pending}
    missing = [t for t in app_texts() if t not in speeches]
    if missing:
        raise VoiceError(f"{script.name} に読みが無い文があります（表示の文を、画面の文と同じにしてください）:\n"
                         + "\n".join(f"  {t}" for t in missing))
    return [(t, speeches[t], hashed_name(t, ext)) for t in app_texts()]


def line_items(rows: list[Line], ext: str) -> tuple[list[tuple[str, str, str]], list[Line]]:
    """台本の行の（表示の文, 読み, ファイル名）と、要確認のため作らない行。"""
    names = [(r.number.zfill(2) if r.number.isdigit() else r.number) + ext for r in rows]
    items = [(r.text, r.speech, name) for r, name in zip(rows, names) if not r.pending]
    return items, [r for r in rows if r.pending]


def hashed_name(text: str, ext: str = ".wav") -> str:
    """文から決まるファイル名（同じ文なら同じ名前）。"""
    return "v_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] + ext


def extension(output_format: str) -> str:
    return ".mp3" if output_format.startswith("mp3") else ".wav"


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


def is_audio(data: bytes, output_format: str) -> bool:
    if output_format.startswith("mp3"):
        return data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    return data.startswith(b"RIFF")


def synthesizer(*, voice: str, model: str, speed: float | None, seed: int | None,
                stability: float = DEFAULT_STABILITY, similarity: float = DEFAULT_SIMILARITY,
                output_format: str = DEFAULT_FORMAT) -> Callable[[str], bytes]:
    key = api_key()

    def synth(speech: str) -> bytes:
        settings: dict = {"stability": stability, "similarity_boost": similarity}
        if speed is not None:
            settings["speed"] = speed
        body: dict = {"text": speech, "model_id": model, "language_code": "ja", "voice_settings": settings}
        if seed is not None:
            body["seed"] = seed
        audio = request("POST", f"/v1/text-to-speech/{urllib.parse.quote(voice, safe='')}", key=key, body=body,
                        query={"output_format": output_format})
        if not is_audio(audio, output_format):
            raise VoiceError(f"音声（{output_format}）が返りませんでした")
        return audio

    return synth


# ---------------------------------------------------------------------------
# 作る
# ---------------------------------------------------------------------------


def generate(items: list[tuple[str, str, str]], out_dir: Path, synth: Callable[[str], bytes] | None, *,
             meta: dict[str, str], force: bool = False, dry_run: bool = False,
             readings: dict[str, str] | None = None) -> int:
    """items は（表示の文, 読み, ファイル名）の一覧。作った数を返す。

    読みに、読み誤りが分かっている語が漢字のまま残っていれば、1件も作らずに止める。
    manifest.json に、表示の文 → ファイル名・読み・声の設定を記録する。
    同じ読み・声の設定の音声がすでにあれば、作り直さない（force のときは作り直す）。
    """
    readings = load_readings() if readings is None else readings
    for _, speech, _ in items:
        check_speech(speech, readings)
    manifest_path = out_dir / MANIFEST
    manifest = {"items": {}}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries: dict = manifest.setdefault("items", {})
    made = 0
    for text, speech, name in items:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ElevenLabs の音声合成で、アプリと動画の音声を作る")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models", help="使えるモデルの ID を表示する")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--voice", required=True, help="声の ID（ElevenLabs の音声ライブラリの声）")
        p.add_argument("--model", default=DEFAULT_MODEL, help=f"モデルの ID（既定: {DEFAULT_MODEL}）")
        p.add_argument("--speed", type=float, default=None, help="話す速さ（1.0 が標準。遅くするなら 0.9 など）")
        p.add_argument("--stability", type=float, default=DEFAULT_STABILITY, help="安定性（0〜1）")
        p.add_argument("--similarity", type=float, default=DEFAULT_SIMILARITY, help="元の声への近さ（0〜1）")
        p.add_argument("--seed", type=int, default=1, help="同じ文から同じ音声に近づける値")
        p.add_argument("--format", default=DEFAULT_FORMAT, help=f"音声の形式（既定: {DEFAULT_FORMAT}。mp3_44100_128 など）")
        p.add_argument("--limit", type=int, default=None, help="最初の N 件だけを作る（声の試し）")
        p.add_argument("--force", action="store_true", help="すでにある音声も作り直す")
        p.add_argument("--dry-run", action="store_true", help="送る文を表示するだけで、API を呼ばない")

    p_questions = sub.add_parser("questions", help="アプリの決まった文の音声を作る")
    common(p_questions)
    p_questions.add_argument("--script", default=str(APP_SCRIPT), help="台本（既定: scripts/voice_app.md）")
    p_questions.add_argument("--out", default=str(ROOT / "web" / "audio"))
    p_lines = sub.add_parser("lines", help="台本（Markdown の表）の1行を1つの音声にする")
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
        ext = extension(args.format)
        if args.cmd == "questions":
            items = app_items(Path(args.script), ext)
        else:
            items, pending = line_items(read_script(Path(args.file)), ext)
            for r in pending:
                print(f"要確認のため作りません（{r.number}）: {r.text}")
        if args.limit is not None:
            items = items[: max(args.limit, 0)]
        out_dir = Path(args.out)
        synth = None if args.dry_run else synthesizer(
            voice=args.voice, model=args.model, speed=args.speed, seed=args.seed, stability=args.stability,
            similarity=args.similarity, output_format=args.format)
        meta = {"voice": args.voice, "model": args.model, "speed": str(args.speed), "stability": str(args.stability),
                "similarity": str(args.similarity), "format": args.format}
        made = generate(items, out_dir, synth, meta=meta, force=args.force, dry_run=args.dry_run)
        print(f"{'送る文を表示しました' if args.dry_run else f'{made}件の音声を作りました'}（{len(items)}件中）: {out_dir}")
        return 0
    except VoiceError as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
