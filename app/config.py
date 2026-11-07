"""サーバーとエージェントの設定（パス、モデル、上限、API キーの読み込み）。

API キーは環境変数 ESCAPECHECK_ANTHROPIC_API_KEY を優先し、無ければ ANTHROPIC_API_KEY を使う。
キーの値はログ・画面・例外のメッセージに出さない（X-01）。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
WEB_DIR = ROOT / "web"
AGENT_HOME = APP_DIR / "agent_home"
SAMPLES_DIR = ROOT / "data" / "samples"
LOG_DIR = APP_DIR / "logs"  # .gitignore 済み。デモの記録のときだけ使う

# 作品の中のモデル（docs/03-design.md の 6.1 節。変更するときは評価の結果を根拠にする）
MODEL_INTERVIEW = "claude-sonnet-5-5"
MODEL_REDTEAM = "claude-opus-5-5"
MODEL_VERIFY = "claude-sonnet-5-5"

# 上限（X-03）
MAX_REQUEST_BYTES = 256 * 1024
MAX_UTTERANCE_CHARS = 2000

# Claude の呼び出しの上限
TIMEOUT_INTERVIEW_SEC = 90
TIMEOUT_REDTEAM_SEC = 300
TIMEOUT_VERIFY_SEC = 120
MAX_TURNS_INTERVIEW = 12
MAX_TURNS_REDTEAM = 60
MAX_TURNS_VERIFY = 10
MAX_BUDGET_USD_PER_CALL = 1.0

API_KEY_ENV_NAMES = ("ESCAPECHECK_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")


class ApiKeyMissing(RuntimeError):
    """API キーが環境変数に無い。メッセージにキーの値は含めない。"""

    def __init__(self) -> None:
        super().__init__(
            "Claude の API キーが環境変数にありません。ESCAPECHECK_ANTHROPIC_API_KEY か ANTHROPIC_API_KEY を設定して、"
            "サーバーを起動し直してください。"
        )


def get_api_key() -> str:
    """API キーを返す。値はこの関数の呼び出し元から外（ログなど）に出さない。"""
    for name in API_KEY_ENV_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise ApiKeyMissing()


def api_key_available() -> bool:
    return any(os.environ.get(name, "").strip() for name in API_KEY_ENV_NAMES)


def use_stub_tools() -> bool:
    """ESCAPECHECK_STUB_TOOLS=1 のときだけ、計算をテスト用の仮の値（app/stub_backend.py）で行う。"""
    return os.environ.get("ESCAPECHECK_STUB_TOOLS", "").strip() == "1"
