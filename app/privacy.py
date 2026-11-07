"""仮名化の確認と、個人情報・秘密情報を外に出さないための処理（R-02・R-03・X-01）。

- 本名から仮名（Aさん）への置き換えは、端末（ブラウザ）が送信する前に行う（docs/03-design.md の 7.5 節）。
  サーバーは、その後に残った電話番号・メールアドレス・郵便番号・座標らしい数値を伏せる（mask_pii）
- Claude に渡すデータからは、座標と、氏名・電話番号・住所のキーを取り除く（view_for_claude）
- ログと例外のメッセージから API キーを取り除く（redact_secrets）

登録していない名前が話に出ると、置き換えられない。ハッカソン版はデモ用の架空の人物で利用する前提で、この限界を README に記載する。
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from typing import Any

from tools import plan_model as pm

#: Claude に渡さないキー（計画のどの階層にあっても取り除く）
FORBIDDEN_KEYS = frozenset(
    {"home", "location", "point", "lat", "lon", "lng", "latitude", "longitude",
     "name_real", "real_name", "full_name", "phone", "tel", "telephone", "address", "email"}
)

_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "［メールアドレス］"),
    # 電話番号（市外局番つき・携帯・フリーダイヤル。区切りはハイフン・空白・括弧）
    ("phone", re.compile(r"(?<!\d)(?:\+81[\s\-]?|0)\d{1,4}[\s\-()]{0,2}\d{1,4}[\s\-()]{0,2}\d{3,4}(?!\d)"), "［電話番号］"),
    ("postal", re.compile(r"〒?\s?(?<!\d)\d{3}-\d{4}(?!\d)"), "［郵便番号］"),
    # 緯度・経度らしい数値（小数点以下4桁以上）
    ("coordinate", re.compile(r"(?<![\d.])\d{1,3}\.\d{4,}(?![\d])"), "［位置］"),
]

_SECRET_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")


def mask_pii(text: str) -> tuple[str, list[str]]:
    """電話番号などを伏せた文と、伏せた種類の一覧を返す。全角の数字も半角にそろえてから調べる。"""
    if not text:
        return text, []
    normalized = unicodedata.normalize("NFKC", text)
    found: list[str] = []
    for kind, pattern, token in _PATTERNS:
        if pattern.search(normalized):
            found.append(kind)
            normalized = pattern.sub(token, normalized)
    if not found:
        return text, []  # 何も伏せないときは、原文（正規化する前）のまま返す
    return normalized, found


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in FORBIDDEN_KEYS}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return mask_pii(value)[0]
    return value


def view_for_claude(plan: pm.Plan) -> dict[str, Any]:
    """Claude に渡す計画。plan_model.for_claude に加えて、禁止のキーをどの階層からも取り除き、電話番号などを伏せる。"""
    return _scrub(pm.for_claude(plan))


def find_forbidden_keys(value: Any, path: str = "") -> list[str]:
    """禁止のキーが残っている場所の一覧（テストと、送信前の最終確認に使う）。"""
    hits: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            here = f"{path}.{k}" if path else k
            if k in FORBIDDEN_KEYS:
                hits.append(here)
            hits.extend(find_forbidden_keys(v, here))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            hits.extend(find_forbidden_keys(v, f"{path}[{i}]"))
    return hits


def sanitize_incoming_plan(plan: pm.Plan) -> list[str]:
    """端末から届いた計画の、発言の原文と欄の値の電話番号などを伏せる。伏せた種類の一覧を返す。

    ここで伏せておくと、Claude が見る文と、フックが引用を照合する原文が一致する。
    """
    found: list[str] = []
    for source in plan.get("sources", {}).values():
        if isinstance(source.get("text"), str):
            source["text"], kinds = mask_pii(source["text"])
            found += kinds
    for field in plan.get("fields", {}).values():
        if isinstance(field.get("value"), str):
            field["value"], kinds = mask_pii(field["value"])
            found += kinds
        if isinstance(field.get("quote"), str):
            field["quote"], kinds = mask_pii(field["quote"])
            found += kinds
    return sorted(set(found))


def redacted_traceback(limit: int = 4000) -> str:
    """直前の例外の traceback を、API キーを伏せて返す（ログ用）。"""
    import traceback

    return redact_secrets(traceback.format_exc())[-limit:]


class RedactingFormatter(logging.Formatter):
    """ログの1行と traceback の全体から、API キーを伏せる。"""

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


def redact_secrets(text: str) -> str:
    """API キーと思われる文字列と、環境変数にある実際のキーの値を伏せる。"""
    if not text:
        return text
    text = _SECRET_PATTERN.sub("［伏せました］", text)
    for name in ("ESCAPECHECK_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"):
        value = os.environ.get(name, "")
        if len(value) >= 8 and value in text:
            text = text.replace(value, "［伏せました］")
    return text
