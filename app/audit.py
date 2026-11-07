"""監査ログ（docs/03-design.md の 6.4 節）。

MCP ツールの呼び出しごとに1行（時刻、エージェント、MCP ツール、引数の要約、結果の要約）を記録する。
フックが拒否した呼び出しも、理由とともに記録する（AC-02）。
サーバーのメモリにだけ保持し、デモの記録のときだけ app/logs/ にファイルでも残す（仮名だけ。R-03）。
"""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Literal

from app import privacy

JST = timezone(timedelta(hours=9))
Decision = Literal["deny", "result", "error", "info"]

#: 引数・結果の要約の上限（文字）
SUMMARY_CHARS = 300


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def tool_label(tool_name: str) -> str:
    """mcp__plan__set_fields を plan.set_fields にする（設計書の表記に合わせる）。"""
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        if len(parts) >= 3:
            return f"{parts[1]}.{'__'.join(parts[2:])}"
    return tool_name


def summarize(value: Any, limit: int = SUMMARY_CHARS) -> str:
    """JSON にして短く切る。電話番号などは伏せる（実在の個人情報を記録しない）。"""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = privacy.mask_pii(text)[0]
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


class AuditLog:
    """スレッドから安全に使える、メモリの中の監査ログ。"""

    def __init__(self, max_entries: int = 5000, file_path: Path | None = None) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._file_path = file_path

    def record(
        self,
        *,
        plan_id: str,
        agent: str,
        tool: str,
        decision: Decision,
        args: Any = None,
        result: Any = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        entry = {
            "at": now_iso(),
            "plan_id": plan_id,
            "agent": agent,
            "tool": tool_label(tool),
            "decision": decision,
            "args": summarize(args) if args is not None else "",
            "result": summarize(result) if result is not None else "",
            "reason": summarize(reason, 600) if reason else "",
        }
        with self._lock:
            self._entries.append(entry)
            if self._file_path is not None:
                try:
                    self._file_path.parent.mkdir(parents=True, exist_ok=True)
                    with self._file_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except OSError:
                    pass  # ファイルに書けなくても、メモリの記録は残す
        return entry

    def entries(self, plan_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            items = [e for e in self._entries if plan_id is None or e["plan_id"] == plan_id]
        return items[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


#: サーバー全体で使う監査ログ
AUDIT = AuditLog()
