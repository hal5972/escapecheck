# 第三者表示（Third-party notices）

この作品が使う第三者のソフトウェアと、そのライセンス。2026-11-07 時点。

- 配り方: 依存のパッケージはリポジトリに同梱しない。動かす人が `uv sync` で PyPI から入れる（`uv.lock` に版とハッシュを固定）
- 版を変えたら、この表を作り直す（依存の点検で、ライセンス・活性度・既知の脆弱性を確かめる）
- データと地図の出典は [data/README.md](data/README.md) にある

## 直接の依存

| パッケージ | 版 | ライセンス | 用途 |
|---|---|---|---|
| claude-agent-sdk | 0.2.179 | MIT | 作品の中の Claude のエージェント（聞き取り役・赤チーム役・検証役）を動かす。配布物には Claude Code（Anthropic）が含まれ、その利用は Anthropic の規約に従う |
| mcp | 2.3.0 | MIT | 自作の道具（地理の事実・計算）を MCP のサーバーとして出す |

## 推移的な依存（上の2つが使う）

| パッケージ | 版 | ライセンス |
|---|---|---|
| annotated-types | 0.8.0 | MIT |
| anyio | 4.16.0 | MIT |
| attrs | 26.1.0 | MIT |
| cffi | 2.1.1 | MIT-0（承認の記録: [docs/oss-approvals.json](docs/oss-approvals.json)） |
| click | 8.5.1 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause（Apache-2.0 を選ぶ） |
| h11 | 0.16.0 | MIT |
| httpcore2 | 2.16.0 | BSD-3-Clause |
| httpx2 | 2.16.0 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| mcp-types | 2.3.0 | MIT |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic_core | 2.50.4 | MIT |
| PyJWT | 2.15.1 | MIT |
| python-multipart | 0.0.33 | Apache-2.0 |
| pywin32 | 312 | PSF |
| referencing | 0.37.0 | MIT |
| rpds-py | 2026.9.1 | MIT |
| sniffio | 1.3.1 | MIT OR Apache-2.0（MIT を選ぶ）。最後の公開が 2024-02 で、活性度に注意。claude-agent-sdk が使う小さな部品で、改変しない |
| sse-starlette | 3.6.0 | BSD-3-Clause |
| starlette | 1.10.0 | BSD-3-Clause |
| truststore | 0.10.5 | MIT |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |

既知の脆弱性: 上のすべて（推移的な依存を含む）を OSV（<https://osv.dev>）に問い合わせ、0件（2026-11-07）。

## 動かすのに使う物（同梱しない）

| 物 | 提供元 | ライセンス |
|---|---|---|
| Python 3.14 | Python Software Foundation | PSF License |
| uv | Astral（OpenAI） | MIT OR Apache-2.0 |
