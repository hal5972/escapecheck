"""作品の中のフック。

PreToolUse で MCP ツールの引数を確認し、規則に反する呼び出しを理由つきで拒否する。理由は Claude に返り、Claude が修正する。
PostToolUse ですべての MCP ツールの呼び出しを監査ログに記録する。拒否も記録する（AC-02）。

| 対象 | 確認すること |
|---|---|
| plan.set_fields | 出典 ID があり出典リストにあるか（S-02）。発言が出典なら、引用が原文にそのまま含まれ、値の数値が引用にあるか（AC-10）。データ・仮定が出典なら、数値が出典リストの値と一致するか（AC-10） |
| plan.set_supporter | 同上（発言の引用） |
| plan.add_premise | 引用が発言の原文にそのまま含まれるか |
| plan.add_finding・plan.propose_fix | 説明文に数値を直接記載していないか（プレースホルダーの外の数値は拒否。S-01）。危険な改善案でないか（S-03） |
| plan.apply_fix | 承認の記録があるか（F-11） |
| plan.finalize | コードによる完成のチェック（F-12・AC-03） |

確認の関数は純粋な関数にして、単体テストで確かめる（tests/test_app_hooks.py、tests/test_app_finalize.py）。
MCP ツールの本体（app/mcp_inprocess.py）も、書き込む直前に同じ関数で確認し直す（フックが動かなかった場合の備え）。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from claude_agent_sdk import HookMatcher

from app import placeholders as ph
from app.audit import tool_label
from app.session import PlanSession
from tools import plan_model as pm

#: 完成に必要な欄（埋まっているか「未確認のまま」の印が必要）
REQUIRED_FIELDS: list[str] = [
    "household",
    "mobility",
    "walk_speed",
    "sleep_place",
    "time_to_leave_day",
    "time_to_leave_night",
    "hearing_and_sight",
    "supporters",
    "support_method",
    "shelter",
    "route",
    "contact",
]

#: 「計算できない」のうち、その計画には当てはまらないことを表す理由（確認の印を求めない）
NOT_APPLICABLE_REASONS = frozenset({"NO_SUPPORTER", "NOT_APPLICABLE", "NO_PREMISE", "NO_PREMISES"})

MIN_QUOTE_CHARS = 2
MAX_ITEMS_PER_CALL = 20
MAX_VALUE_CHARS = 300
MAX_EXPLANATION_CHARS = 400
MAX_FIXES_PER_FINDING = 3

#: 許可する組み込みツール: Skills を読む Skill と、構造化出力を返す StructuredOutput だけ
#: （X-02。ほかの組み込みツールはオプションでも外している）
ALLOWED_BUILTIN_TOOLS = frozenset({"Skill", "StructuredOutput"})

# ---------------------------------------------------------------------------
# 数値と引用の照合
# ---------------------------------------------------------------------------

_KANJI_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_KANJI_UNITS = {"十": 10, "百": 100, "千": 1000}
_KANJI_NUM_RE = re.compile(r"[〇零一二三四五六七八九十百千]+")
_ARABIC_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
#: 漢数字のうち、単位が続くもの（「十二分」「三百メートル」）。「一人」「一緒」は数値として扱わない
_KANJI_WITH_UNIT_RE = re.compile(
    r"[〇零一二三四五六七八九十百千]+\s*(?:分|秒|時間|時|メートル|m|キロ|km|階|パーセント|%|倍|歳|才)"
)
_QUOTE_IGNORED_RE = re.compile(r"[\s「」『』\"'“”‘’]")

#: 改善案の説明文に出してはいけない言い方（S-03。危険な改善案はコードでも止める）
_DANGEROUS_FIX_RE = re.compile(
    r"(車で|自動車|軽トラ|様子を見|揺れが収ま|収まってから|垂直避難|上の階へ|上の階に避難|二階へ|二階に避難|屋根|自宅にとどま|家にとどま)"
)


#: 問題点の説明で、勧めてはいけない行動（S-03）。計画に書かれた危険な内容を「」で引用するのはよい
_DANGEROUS_ADVICE_RE = re.compile(
    r"(車で(避難|逃げ|行|向か)|車に乗|様子を見|揺れが(収ま|おさま)ってから|上の階へ(避難|逃げ)|(2|２|二)階へ(避難|逃げ|上が)|"
    r"自宅(に|で)(とどま|待))"
)
_RECOMMEND_RE = re.compile(r"(ましょう|てください|がよい|が良い|をおすすめ|を勧め|すすめます|勧めます|ほうがよい|ほうが良い)")
_QUOTED_RE = re.compile(r"「[^」]*」")


def dangerous_recommendation(text: str) -> str | None:
    """危険な行動を勧める文があれば、その言い回しを返す。

    「」の中（計画に書かれた内容の引用）は除き、1文ごとに「危険な行動」と「勧める言い方」が両方あるかを見る。
    「様子を見ているあいだに津波が来るおそれ」のような注意の文は、勧める言い方が無いので通す。
    """
    unquoted = _QUOTED_RE.sub("", text or "")
    for sentence in re.split(r"[。！？!?\n]", unquoted):
        match = _DANGEROUS_ADVICE_RE.search(sentence)
        if match and _RECOMMEND_RE.search(sentence):
            return match.group(0)
    return None


def _parse_kanji_number(text: str) -> int | None:
    total, current, seen = 0, 0, False
    for ch in text:
        if ch in _KANJI_DIGITS:
            current = current * 10 + _KANJI_DIGITS[ch]
            seen = True
        elif ch in _KANJI_UNITS:
            total += (current or 1) * _KANJI_UNITS[ch]
            current = 0
            seen = True
    return total + current if seen else None


def numbers_in_text(text: str, *, kanji: bool = False) -> list[float]:
    """文の中の数値。全角の数字は半角にそろえる。kanji=True なら漢数字（三、十五）も数える。"""
    normalized = unicodedata.normalize("NFKC", text or "")
    numbers = [float(m) for m in _ARABIC_NUM_RE.findall(normalized)]
    if kanji:
        for m in _KANJI_NUM_RE.findall(normalized):
            value = _parse_kanji_number(m)
            if value is not None:
                numbers.append(float(value))
    return numbers


def numbers_in_value(value: Any) -> list[float]:
    """欄の値（文字列・数値・dict・list）の中の数値。値の中の漢数字は「一人暮らし」などの語が多いので数えない。"""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, str):
        return numbers_in_text(value)
    if isinstance(value, dict):
        # shelter_id などの ID（"E3920500119201"）は数値ではないので数えない
        return [n for k, v in value.items() if not str(k).endswith("_id") for n in numbers_in_value(v)]
    if isinstance(value, (list, tuple)):
        return [n for v in value for n in numbers_in_value(v)]
    return []


def _missing_numbers(needed: list[float], pool: list[float]) -> list[str]:
    missing = []
    for n in needed:
        if not any(abs(n - p) < 1e-9 for p in pool):
            text = f"{n:g}"
            if text not in missing:
                missing.append(text)
    return missing


def normalize_quote(text: str) -> str:
    return _QUOTE_IGNORED_RE.sub("", unicodedata.normalize("NFKC", text or ""))


def quote_in_text(quote: str, text: str) -> bool:
    """引用が原文にそのまま含まれるか。空白と、引用を囲む括弧だけは無視する。"""
    q = normalize_quote(quote)
    return len(q) >= MIN_QUOTE_CHARS and q in normalize_quote(text)


def _value_too_long(value: Any) -> bool:
    if isinstance(value, str):
        return len(value) > MAX_VALUE_CHARS
    if isinstance(value, dict):
        return any(_value_too_long(v) for v in value.values()) or len(value) > 8
    if isinstance(value, list):
        return len(value) > 10 or any(_value_too_long(v) for v in value)
    return False


def _check_utterance_quote(plan: pm.Plan, source_id: Any, quote: Any, what: str) -> tuple[list[str], dict | None]:
    """出典が発言であり、引用が原文にそのまま含まれるか。問題が無ければ ([], source)。"""
    if not source_id:
        return [f"出典の ID がありません: {what}"], None
    source = plan.get("sources", {}).get(source_id)
    if source is None:
        return [f"出典リストに無い出典の ID です: {what}（{source_id}）"], None
    if source.get("kind") != "utterance":
        return [f"出典が発言ではありません: {what}（話の中の内容は、発言の出典 ID を指定してください）"], None
    if not quote or not isinstance(quote, str):
        return [f"発言の引用（quote）がありません: {what}"], None
    if not quote_in_text(quote, source.get("text", "")):
        return [f"引用が発言の原文にありません: {what}「{quote}」。原文からそのまま抜き出してください"], None
    return [], source


# ---------------------------------------------------------------------------
# MCP ツールごとの確認（純粋な関数。問題の理由の一覧を返す。空なら問題なし）
# ---------------------------------------------------------------------------


def check_set_fields(
    plan: pm.Plan, items: Any, known_shelter_ids: set[str] | None = None, *, from_claude: bool = True
) -> list[str]:
    """plan.set_fields の確認（S-02・AC-02・AC-10）。

    from_claude=False は、画面（地図のタップ）が書いた欄を完成のチェックで確認し直すとき。地図の座標の数値を拒否しない。
    """
    if not isinstance(items, list) or not items:
        return ["items が空です。書き込む欄を1つ以上指定してください"]
    if len(items) > MAX_ITEMS_PER_CALL:
        return [f"一度に書き込める欄は {MAX_ITEMS_PER_CALL} 件までです"]
    reasons: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            reasons.append("items の要素の形が正しくありません")
            continue
        field = item.get("field")
        if field not in pm.FIELDS:
            reasons.append(f"様式に無い欄です: {field}")
            continue
        value = item.get("value")
        if value is None or value == "" or value == {}:
            reasons.append(f"値が空です: {field}（聞き取れていない欄は書き込まず、未確認のままにしてください）")
            continue
        if _value_too_long(value):
            reasons.append(f"値が長すぎます: {field}")
            continue
        source_id = item.get("source_id")
        if not source_id:
            reasons.append(f"出典の ID がありません: {field}")
            continue
        source = plan.get("sources", {}).get(source_id)
        if source is None:
            reasons.append(f"出典リストに無い出典の ID です: {field}（{source_id}）")
            continue
        kind = source.get("kind")
        if kind == "utterance":
            quote_reasons, _ = _check_utterance_quote(plan, source_id, item.get("quote"), field)
            if quote_reasons:
                reasons += quote_reasons
                continue
            missing = _missing_numbers(numbers_in_value(value), numbers_in_text(item["quote"], kanji=True))
            if missing:
                reasons.append(
                    f"値の数値が引用にありません: {field}（{', '.join(missing)}）。話していない数値は書き込まないでください"
                )
                continue
        elif kind in ("dataset", "tool", "assumption"):
            source_value = source.get("value")
            pool = numbers_in_value(source_value) if source_value is not None else []
            missing = _missing_numbers(numbers_in_value(value), pool)
            if missing:
                reasons.append(f"値の数値が出典リストの値と一致しません: {field}（{', '.join(missing)}）")
                continue
        elif kind == "map_tap":
            if from_claude and numbers_in_value(value):
                reasons.append(f"地図の位置の欄に数値は書き込めません: {field}（地図の位置は画面が書き込みます）")
                continue
        else:
            reasons.append(f"出典の種類が正しくありません: {field}")
            continue
        if field == "shelter" and isinstance(value, dict) and value.get("shelter_id") and known_shelter_ids is not None:
            if value["shelter_id"] not in known_shelter_ids:
                reasons.append("避難先の shelter_id が、hazard.find_shelters の結果にありません")
    return reasons


def check_set_supporter(plan: pm.Plan, args: dict[str, Any]) -> list[str]:
    """plan.set_supporter の確認。避難支援者の情報も、発言の引用を根拠にする。"""
    reasons: list[str] = []
    supporter_id = args.get("supporter_id")
    if supporter_id and supporter_id not in {s.get("supporter_id") for s in plan.get("supporters", [])}:
        reasons.append(f"計画に無い避難支援者です: {supporter_id}")
    alias = args.get("alias")
    if not supporter_id and (not isinstance(alias, str) or not alias.strip()):
        reasons.append("新しい避難支援者には、仮名（alias。例: Bさん）が必要です")
    if isinstance(alias, str) and (len(alias) > 12 or numbers_in_text(alias)):
        reasons.append("仮名（alias）は12文字までで、数字を含めないでください")
    for key in ("relation", "age_band"):
        if args.get(key) is not None and (not isinstance(args[key], str) or len(args[key]) > 20):
            reasons.append(f"{key} は20文字までの文字列にしてください")
    for key in ("available_day", "available_night"):
        if args.get(key) is not None and not isinstance(args[key], bool):
            reasons.append(f"{key} は true か false にしてください")
    quote_reasons, _ = _check_utterance_quote(plan, args.get("source_id"), args.get("quote"), "避難支援者")
    reasons += quote_reasons
    if not quote_reasons:
        missing = _missing_numbers(
            numbers_in_value({k: args.get(k) for k in ("alias", "relation", "age_band")}),
            numbers_in_text(args["quote"], kanji=True),
        )
        if missing:
            reasons.append(f"避難支援者の情報の数値が引用にありません（{', '.join(missing)}）")
    return reasons


def check_add_premise(plan: pm.Plan, args: dict[str, Any]) -> list[str]:
    """plan.add_premise の確認: quote が話の原文にそのまま含まれるか（AC-10）。"""
    reasons: list[str] = []
    code = args.get("premise_code")
    if code not in pm.PREMISE_CODES:
        reasons.append(f"前提条件のコードが正しくありません: {code}（{', '.join(pm.PREMISE_CODES)} のどれか）")
    quote_reasons, _ = _check_utterance_quote(plan, args.get("source_id"), args.get("quote"), f"前提条件 {code}")
    reasons += quote_reasons
    about = args.get("about")
    if about is not None and (not isinstance(about, str) or len(about) > 60):
        reasons.append("about は60文字までの文字列にしてください")
    if not reasons:
        for premise in plan.get("premises", []):
            if premise.get("code") == code and normalize_quote(premise.get("quote", "")) == normalize_quote(args["quote"]):
                reasons.append(f"同じ前提条件がすでに登録されています: {premise.get('premise_id')}")
                break
    return reasons


def check_explanation(text: Any) -> list[str]:
    """説明文の確認: 数値はプレースホルダーで記載し、直接記載しない（S-01・AC-10）。"""
    if not isinstance(text, str) or not text.strip():
        return ["説明文が空です"]
    reasons: list[str] = []
    if len(text) > MAX_EXPLANATION_CHARS:
        reasons.append(f"説明文が長すぎます（{MAX_EXPLANATION_CHARS}字まで）")
    unknown = [name for name in ph.used_placeholders(text) if name not in ph.PLACEHOLDERS]
    if unknown:
        reasons.append(
            "使えないプレースホルダーです: "
            + ", ".join("{" + n + "}" for n in unknown)
            + "（使えるもの: "
            + ", ".join("{" + n + "}" for n in ph.PLACEHOLDERS)
            + "）"
        )
    outside = unicodedata.normalize("NFKC", ph.PLACEHOLDER_RE.sub("", text))
    if re.search(r"\d", outside) or _KANJI_WITH_UNIT_RE.search(outside):
        reasons.append(
            "説明文に数値が直接書かれています。数値は {need_min}・{avail_min}・{shortage_min} などのプレースホルダーで書いてください"
            "（階や時刻も数字で書かず「上の階」「深夜」と書く）"
        )
    if "{" in outside or "}" in outside:
        reasons.append("プレースホルダーの書き方が正しくありません（例: {need_min}）")
    return reasons


def _placeholder_hints(missing: list[str], values: dict[str, str | None]) -> str:
    hints = []
    if "shortage_min" in missing and values.get("margin_min") is not None:
        hints.append("このシナリオは間に合うので {margin_min} を使ってください")
    if "margin_min" in missing and values.get("shortage_min") is not None:
        hints.append("このシナリオは間に合わないので {shortage_min} を使ってください")
    if "supporter_shortage_min" in missing and values.get("supporter_margin_min") is not None:
        hints.append("避難支援者は間に合うので {supporter_margin_min} を使ってください")
    if "supporter_margin_min" in missing and values.get("supporter_shortage_min") is not None:
        hints.append("避難支援者は間に合わないので {supporter_shortage_min} を使ってください")
    if "shelter_name" in missing:
        hints.append("計画の避難先がデータの避難先と結び付いていないため、{shelter_name} は使えません。「避難先」と書いてください")
    numeric = {"need_min", "avail_min", "margin_min", "shortage_min"}
    if numeric & set(missing) and all(values.get(n) is None for n in numeric):
        hints.append("この結果は時間を計算できない種類です（一人では避難できない、声をかける人がいない、など）。数値のプレースホルダーを使わずに説明してください")
    return "。".join(hints)


def _check_placeholders_against(template: str, values: dict[str, str | None]) -> list[str]:
    missing = [n for n in ph.unresolved(template, values) if n in ph.PLACEHOLDERS]
    if not missing:
        return []
    hint = _placeholder_hints(missing, values)
    text = "この結果には値が無いプレースホルダーです: " + ", ".join("{" + n + "}" for n in missing)
    return [text + (f"（{hint}）" if hint else "")]


def check_add_finding(plan: pm.Plan, args: dict[str, Any], result: pm.ScenarioResult | None = None) -> list[str]:
    """plan.add_finding の確認。result を渡すと、プレースホルダーが結果と食い違わないかも確認する。"""
    reasons: list[str] = []
    if args.get("scenario_id") not in pm.SCENARIOS:
        reasons.append(f"シナリオの ID が正しくありません: {args.get('scenario_id')}（S1〜S8）")
    if args.get("hole_type") not in pm.HOLE_TYPES:
        reasons.append(f"問題点の種類のコードが正しくありません: {args.get('hole_type')}")
    reasons += check_explanation(args.get("explanation"))
    phrase = dangerous_recommendation(args.get("explanation") or "")
    if phrase:
        reasons.append(f"危険な行動を勧める言い回しがあります（{phrase}）。計画に書かれた危険な内容は「」で引用し、"
                       "勧める文には入れないでください")
    source_ids = args.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids:
        reasons.append("根拠の出典 ID（source_ids）を1つ以上指定してください")
    else:
        unknown = [s for s in source_ids if s not in plan.get("sources", {})]
        if unknown:
            reasons.append(f"出典リストに無い出典の ID です: {', '.join(map(str, unknown))}")
    if not reasons and result is not None:
        reasons += _check_placeholders_against(args["explanation"], ph.placeholder_values(result, plan))
    return reasons


def find_finding(plan: pm.Plan, finding_id: Any) -> pm.Finding | None:
    return next((f for f in plan.get("findings", []) if f.get("finding_id") == finding_id), None)


def find_fix(plan: pm.Plan, fix_id: Any) -> pm.Fix | None:
    return next((f for f in plan.get("fixes", []) if f.get("fix_id") == fix_id), None)


def replaces_unusable_shelter(plan: pm.Plan, fix_type: Any, target_id: Any) -> bool:
    """避難先を変更する案が、この人には使えない計画の避難先（前提条件 CANNOT_CLIMB の対象）を置き換えるか。

    sim は、上がれない避難先を除いて次の避難先で計算する。そのため、計画の避難先を直す案は、時間が短縮されなくても必要になる。
    about が無い CANNOT_CLIMB は、計画の避難先を指すとみなす（tools/sim.py と同じ扱い）。
    """
    if fix_type != "CHANGE_SHELTER" or not target_id:
        return False
    value = (plan.get("fields", {}).get("shelter") or {}).get("value")
    planned = value.get("shelter_id") if isinstance(value, dict) else None
    if not planned or planned == target_id:
        return False
    return any(p.get("code") == "CANNOT_CLIMB" and p.get("about") in (planned, None, "")
               for p in plan.get("premises", []))


def saved_minutes(finding_result: pm.ScenarioResult, fix_result: pm.ScenarioResult) -> float | None:
    """改善案で短縮できる時間（余裕時間の増え方）。計算できなければ None。"""
    before, after = finding_result.get("margin_min"), fix_result.get("margin_min")
    if before is None or after is None:
        return None
    return round(after - before, 6)


def check_propose_fix(
    plan: pm.Plan,
    args: dict[str, Any],
    result: pm.ScenarioResult | None = None,
    known_shelters: dict[str, pm.Shelter] | None = None,
    baseline: pm.ScenarioResult | None = None,
) -> list[str]:
    """plan.propose_fix の確認。result（sim.try_fix の結果）を渡すと、効き目と危険な案も確認する（S-03）。

    baseline は効き目を比べる元の結果（try_fix と同じ条件の run_scenario）。省略すると問題点の結果と比べる。
    """
    reasons: list[str] = []
    finding = find_finding(plan, args.get("finding_id"))
    if finding is None:
        reasons.append(f"計画に無い問題点です: {args.get('finding_id')}")
    fix_type = args.get("fix_type")
    if fix_type not in pm.FIX_TYPES:
        reasons.append(f"改善案の種類が正しくありません: {fix_type}（{', '.join(pm.FIX_TYPES)} のどれか）")
    explanation = args.get("explanation")
    reasons += check_explanation(explanation)
    if isinstance(explanation, str) and _DANGEROUS_FIX_RE.search(explanation):
        reasons.append("危険な改善案の言い方が含まれています（車での避難、様子を見る、自宅の上の階への避難などは出さない）")
    target_id = args.get("target_id")
    if fix_type == "CHANGE_SHELTER":
        if not target_id:
            reasons.append("避難先を変更する案には、target_id（hazard.find_shelters の shelter_id）が必要です")
        elif known_shelters is not None:
            shelter = known_shelters.get(target_id)
            if shelter is None:
                reasons.append("target_id が hazard.find_shelters の結果にありません")
            elif not shelter.get("tsunami_ok"):
                reasons.append("津波の指定緊急避難場所ではない避難先は選べません")
    if fix_type == "ADD_CALLER":
        if not target_id:
            reasons.append("声かけをする人を追加する案には、target_id（計画の避難支援者の supporter_id）が必要です")
        elif target_id not in {s.get("supporter_id") for s in plan.get("supporters", [])}:
            reasons.append(f"計画に無い避難支援者です: {target_id}")
    if finding is not None:
        count = sum(1 for f in plan.get("fixes", []) if f.get("finding_id") == finding["finding_id"])
        if count >= MAX_FIXES_PER_FINDING:
            reasons.append(f"1つの問題点に出せる改善案は {MAX_FIXES_PER_FINDING} つまでです")
    if not reasons and result is not None and finding is not None:
        if result.get("supporter_verdict") == "short":
            reasons.append("この改善案では、避難支援者が間に合わなくなります（出さない案です）")
        saved = saved_minutes(baseline or finding["result"], result)
        if saved is not None and saved <= 0 and not replaces_unusable_shelter(plan, fix_type, target_id):
            reasons.append("計算の結果、この改善案では時間が短縮されません")
        if not reasons:
            name = None
            if fix_type == "CHANGE_SHELTER" and known_shelters and target_id in known_shelters:
                name = known_shelters[target_id].get("name")
            values = ph.placeholder_values(result, plan, saved_min=saved, shelter_name=name)
            reasons += _check_placeholders_against(explanation, values)
    return reasons


def check_apply_fix(plan: pm.Plan, fix_id: Any) -> list[str]:
    """plan.apply_fix の確認: 人（民生委員・本人）の承認の記録があるか（F-11）。"""
    if find_fix(plan, fix_id) is None:
        return [f"計画に無い改善案です: {fix_id}"]
    if not any(a.get("fix_id") == fix_id for a in plan.get("approvals", [])):
        return [f"承認の記録がありません: {fix_id}。民生委員が「これにする」を押すまで、計画は変更できません"]
    return []


# ---------------------------------------------------------------------------
# 完成のチェック（コードによる点検。F-12・AC-03）
# ---------------------------------------------------------------------------


def _field_source_problem(plan: pm.Plan, key: str, field: pm.FieldValue) -> str | None:
    reasons = check_set_fields(
        plan,
        [{"field": key, "value": field.get("value"), "source_id": field.get("source_id"), "quote": field.get("quote")}],
        from_claude=False,
    )
    return reasons[0] if reasons else None


def unusable_planned_shelter(plan: pm.Plan) -> str | None:
    """計画の避難先が、前提条件 CANNOT_CLIMB で「上がれない」とされているなら、その避難先の名前を返す。

    about が無い CANNOT_CLIMB は、計画の避難先を指すとみなす。避難先を変える案を承認して欄が変われば、None になる。
    """
    value = (plan.get("fields", {}).get("shelter") or {}).get("value")
    if not isinstance(value, dict) or not value.get("shelter_id"):
        return None
    planned = value["shelter_id"]
    if any(p.get("code") == "CANNOT_CLIMB" and p.get("about") in (planned, None, "") for p in plan.get("premises", [])):
        return str(value.get("name") or value.get("text") or "避難先")
    return None


def code_issues(plan: pm.Plan, backend: Any = None) -> list[pm.Issue]:
    """コードで見つかる要確認の項目。issue_id は毎回同じにする（人の確認の印を保つため）。

    severity が「誤り」の項目は、確認の印では解消できない（出典の誤りは聞き取り直す）。
    """
    issues: list[pm.Issue] = []
    fields = plan.get("fields", {})
    for key in REQUIRED_FIELDS:
        field = fields.get(key) or {}
        if field.get("status") != "filled":
            issues.append(pm.Issue(
                issue_id=f"chk_unconfirmed_{key}", field=key, severity="要確認", acknowledged=False,
                message=f"「{pm.FIELDS[key]}」が未確認です。聞き取るか、「未確認のまま」の印を付けてください",
            ))
    for key, field in fields.items():
        if key in pm.FIELDS and field.get("status") == "filled":
            problem = _field_source_problem(plan, key, field)
            if problem:
                issues.append(pm.Issue(
                    issue_id=f"chk_source_{key}", field=key, severity="誤り", acknowledged=False,
                    message=f"「{pm.FIELDS[key]}」の出典を確認できません。聞き取り直してください（{problem}）",
                ))
    unusable = unusable_planned_shelter(plan)
    if unusable:
        # 計算は上がれない避難先を除いて次の避難先で行うので「間に合う」と出るが、計画（と避難カード）の避難先は使えない
        issues.append(pm.Issue(
            issue_id="chk_shelter_unusable", severity="要確認", acknowledged=False,
            message=f"計画の避難先（{unusable}）には、本人が上がれないと話しています。"
                    "避難先を変える案を選ぶか、「本人・家族と相談する」の印を付けてください",
        ))
    if backend is not None:
        for scenario_id, label in pm.SCENARIOS.items():
            if scenario_id == "S8" and not plan.get("premises"):
                continue  # 話の中だけの前提条件が無ければ、S8 は当てはまらない
            try:
                # premise_ids を省略: 登録済みの前提条件をすべて当てはめる（安全側。sim の取り決め）
                result = backend.run_scenario(plan, scenario_id, None)
            except Exception:  # noqa: BLE001  計算のモジュールの不具合でも、完成にはさせない
                issues.append(pm.Issue(
                    issue_id=f"chk_error_{scenario_id}", severity="誤り", acknowledged=False,
                    message=f"「{label}」のシナリオを計算できませんでした。計算の準備ができてから、もう一度確認してください",
                ))
                continue
            verdict = result.get("verdict")
            if verdict == "short":
                issues.append(pm.Issue(
                    issue_id=f"chk_short_{scenario_id}", severity="要確認", acknowledged=False,
                    message=f"「{label}」のシナリオで間に合いません。改善案を選ぶか、「本人・家族と相談する」の印を付けてください",
                ))
            elif verdict == "cannot_compute" and result.get("reason_code") not in NOT_APPLICABLE_REASONS:
                issues.append(pm.Issue(
                    issue_id=f"chk_cannot_{scenario_id}", severity="要確認", acknowledged=False,
                    message=f"「{label}」のシナリオは計算できませんでした。足りない情報を確認するか、「未確認のまま」の印を付けてください",
                ))
    return issues


def _consulted_findings(plan: pm.Plan) -> list[pm.Finding]:
    """画面で「本人・家族と相談する」を押した問題点（issue_id が consult_<finding_id> で確認済み）。"""
    acked = {i.get("issue_id") for i in plan.get("issues", []) if i.get("acknowledged")}
    return [f for f in plan.get("findings", []) if f"consult_{f.get('finding_id')}" in acked]


def _consulted_scenarios(plan: pm.Plan) -> set[str]:
    return {f["scenario_id"] for f in _consulted_findings(plan)}


def check_finalize(plan: pm.Plan, backend: Any = None) -> list[str]:
    """完成にできるか。できなければ理由の一覧（AC-03・F-12）。"""
    reasons: list[str] = []
    existing = {i.get("issue_id"): i for i in plan.get("issues", [])}
    consulted = _consulted_scenarios(plan)
    computed = code_issues(plan, backend)
    computed_ids = {i["issue_id"] for i in computed}
    for issue in computed:
        if issue["severity"] == "誤り":
            reasons.append(issue["message"])
        elif issue["issue_id"].startswith("chk_short_") and issue["issue_id"][len("chk_short_"):] in consulted:
            continue  # 問題点のカードで「本人・家族と相談する」の印が付いている
        elif issue["issue_id"] == "chk_shelter_unusable" and any(
                f.get("hole_type") == "CANNOT_CLIMB" for f in _consulted_findings(plan)):
            continue  # 上がれない避難先の問題点のカードで「本人・家族と相談する」の印が付いている
        elif not existing.get(issue["issue_id"], {}).get("acknowledged"):
            reasons.append(issue["message"])
    for issue in plan.get("issues", []):
        if issue.get("issue_id") in computed_ids or str(issue.get("issue_id", "")).startswith("chk_"):
            continue  # コードの点検の項目は、上で計算し直した結果で判断する
        if not issue.get("acknowledged"):
            reasons.append(f"検証エージェントの要確認の項目が残っています: {issue.get('message', '')}")
    return reasons


# ---------------------------------------------------------------------------
# Agent SDK のフック
# ---------------------------------------------------------------------------


def deny_output(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def scenario_result_for(session: PlanSession, scenario_id: str, premise_ids: list[str] | None = None) -> pm.ScenarioResult:
    """問題点に付ける計算の結果。同じセッションで直前に計算した結果があればそれを使い、無ければ計算する。

    premise_ids を省略すると、登録済みの前提条件をすべて使う（[] なら使わない。sim の取り決め）。
    """
    if premise_ids is None and scenario_id in session.last_results:
        return session.last_results[scenario_id]
    return session.backend.run_scenario(session.plan, scenario_id, premise_ids)


def fix_baseline(session: PlanSession, finding: pm.Finding) -> pm.ScenarioResult:
    """改善案の効き目を比べる元の結果。sim.try_fix と同じ条件（登録済みの前提条件をすべて使う）で計算し直す。"""
    try:
        return session.backend.run_scenario(session.plan, finding["scenario_id"], None)
    except ValueError:
        return finding["result"]


def pre_tool_reasons(session: PlanSession, tool_name: str, args: dict[str, Any]) -> list[str]:
    """PreToolUse で拒否する理由（空なら許可）。"""
    if not tool_name.startswith("mcp__"):
        if tool_name in ALLOWED_BUILTIN_TOOLS:
            return []
        return [f"このエージェントは {tool_name} を使えません。用意された MCP ツールだけを使ってください"]
    label = tool_label(tool_name)
    plan = session.plan
    with session.lock:
        if label == "plan.set_fields":
            shelter_ids = set(session.known_shelters) if session.known_shelters else None
            return check_set_fields(plan, args.get("items"), shelter_ids)
        if label == "plan.set_supporter":
            return check_set_supporter(plan, args)
        if label == "plan.add_premise":
            return check_add_premise(plan, args)
        if label == "plan.add_finding":
            reasons = check_add_finding(plan, args)
            if reasons or session.backend is None:
                return reasons
            try:
                result = scenario_result_for(session, args["scenario_id"], args.get("premise_ids"))
            except ValueError as exc:
                return [f"計算できませんでした: {exc}"]
            return check_add_finding(plan, args, result)
        if label == "plan.propose_fix":
            reasons = check_propose_fix(plan, args, known_shelters=session.known_shelters or None)
            if reasons or session.backend is None:
                return reasons
            finding = find_finding(plan, args["finding_id"])
            try:
                result = session.backend.try_fix(plan, finding["scenario_id"], args["fix_type"], args.get("target_id"))
            except ValueError as exc:  # sim.try_fix は危険な改善案を ValueError で拒否する（S-03）
                return [f"この改善案は使えません: {exc}"]
            return check_propose_fix(plan, args, result, session.known_shelters or None,
                                     baseline=fix_baseline(session, finding))
        if label == "plan.apply_fix":
            return check_apply_fix(plan, args.get("fix_id"))
        if label == "plan.finalize":
            return check_finalize(plan, session.backend)
    return []


def build_hooks(session: PlanSession) -> dict[str, list[HookMatcher]]:
    """エージェントのセッションに登録するフック。session の計画と監査ログを参照する。"""

    async def pre_tool_use(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        tool_name = str(input_data.get("tool_name", ""))
        args = input_data.get("tool_input") or {}
        try:
            reasons = pre_tool_reasons(session, tool_name, args if isinstance(args, dict) else {})
        except Exception:  # noqa: BLE001  確認できないときは、書き込ませない
            reasons = ["フックの確認中にエラーが起きたため、この呼び出しを止めました"]
        if reasons:
            reason = "\n".join(reasons)
            session.audit.record(
                plan_id=session.plan_id, agent=session.role, tool=tool_name, decision="deny", args=args, reason=reason
            )
            return deny_output(reason)
        return {}

    async def post_tool_use(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        session.audit.record(
            plan_id=session.plan_id,
            agent=session.role,
            tool=str(input_data.get("tool_name", "")),
            decision="result",
            args=input_data.get("tool_input"),
            result=input_data.get("tool_response"),
        )
        return {}

    async def post_tool_use_failure(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        session.audit.record(
            plan_id=session.plan_id,
            agent=session.role,
            tool=str(input_data.get("tool_name", "")),
            decision="error",
            args=input_data.get("tool_input"),
            reason=str(input_data.get("error", "")),
        )
        return {}

    return {
        "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_use], on_failure="block")],
        "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool_use])],
        "PostToolUseFailure": [HookMatcher(matcher=None, hooks=[post_tool_use_failure])],
    }
