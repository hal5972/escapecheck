# app/（サーバーとエージェント）の動かし方

依頼者の PC の中だけで動かす。インターネットには公開しない（X-05）。

## 起動

```
uv sync
.venv/Scripts/python.exe -m app.server --port 8000
```

- 開く: http://localhost:8000/ （画面は web/ を配信する）
- 合言葉: 環境変数 `ESCAPECHECK_PASSPHRASE`。無ければ起動時に入力するか、自動で作成してコンソールに表示する
- API キー: 環境変数 `ESCAPECHECK_ANTHROPIC_API_KEY`、無ければ `ANTHROPIC_API_KEY`。値はログ・画面に出さない。Windows のユーザーの環境変数に入れた直後は、新しく開いたターミナルからでないと見えない
- `--stub-tools`: 計算をテスト用の仮の値（app/stub_backend.py）で行う。tools/ の実装が入るまでの開発用。数値を実際の判断に使わない
- `--audit-file`: 監査ログを app/logs/ にも書き出す（デモの記録用。仮名だけ。app/logs/ は .gitignore 済み）
- `--lan`: 同じ Wi-Fi のスマートフォンから開けるようにする（音声入力は HTTPS でないため使えないことがある）
- 使用中のポートを指定すると、起動を止めて知らせる

## テスト

```
.venv/Scripts/python.exe -m unittest discover -s tests
```

Claude は呼び出さない（エージェントの実行はテスト用の関数に置き換える）。AC-02・AC-10 は tests/test_app_hooks.py、AC-03 は tests/test_app_finalize.py。

## 評価のための切り替え

呼び出すたびに環境変数を読む（評価のスクリプトから1回ごとに切り替えられる）。

| 環境変数 | 例 | 既定 |
|---|---|---|
| `ESCAPECHECK_MODEL_INTERVIEW`・`_REDTEAM`・`_VERIFY` | `claude-haiku-5-5` | Sonnet 5.5・Opus 5.5・Sonnet 5.5 |
| `ESCAPECHECK_SKILLS_INTERVIEW` など | `kobetsu-plan-form,yasashii-kotoba`（空なら Skill を使わない） | エージェントごとに使う Skill |
| `ESCAPECHECK_EFFORT_INTERVIEW` など | `medium` | low・medium・low |

点検の評価は `orchestrator.redteam_for_eval(plan)`（start_check と同じ処理を同期的に実行し、findings を返す）。

## 構成

| ファイル | 内容 |
|---|---|
| server.py | HTTP、合言葉、トークン、API、静的ファイル、CSP |
| orchestrator.py | エージェントを呼び出す順序（コードで固定）、点検のジョブ、承認、検証、完成 |
| agents.py | Agent SDK の設定（モデル、MCP ツール、Skill、組み込みツールを外す、環境変数） |
| hooks.py | フック（出典・引用・数値・承認・完成のチェック）と監査ログへの記録 |
| mcp_inprocess.py | プロセス内の MCP ツール（plan・hazard・sim） |
| placeholders.py | 説明文のプレースホルダーを計算の結果で置き換える |
| privacy.py | 電話番号などを伏せる、Claude に渡す計画から座標を除く、ログから API キーを伏せる |
| agent_home/.claude/skills/ | 作品の Skill 4つ |
