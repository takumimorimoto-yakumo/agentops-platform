# agentops-platform

AgentOps platform — evaluation, canary release, and auto-rollback CI/CD for AI agents built with Google ADK.

## コミュニケーション
- 日本語で回答すること。コードのコメント・docstring・README は英語

## グローバル原則の継承

開発系エージェント（system-developer / web-developer）の共通原則は `~/.claude/docs/dev-principles.md` に集約されている。このプロジェクトでも同じ原則に従う。

## スコープ

| 含める | 含めない |
|---|---|
| 評価データセット管理（ADK Eval ベース） | 汎用 MLOps（モデル学習・DWH） |
| 回帰テスト（プロンプト/ルール変更時の挙動差分検知） | 全エージェントタイプ対応（ADK 特化） |
| カナリアリリース（トラフィック比率調整） | マルチクラウド対応 |
| 自動ロールバック（評価スコア低下時） | エージェント開発フレームワークの再発明 |
| Cloud Trace / Cloud Logging 連携 | 既存 APM の置き換え |
| CLI + 読み取り専用 Web Dashboard（3画面） | フル機能の運用 GUI |

評価軸は3つ: ①出力ドリフト検知（Gemini judge スコア差分）②行動評価（ADK Eval trajectory）③コスト/レイテンシ（Cloud Trace/Logging）。

## 技術スタック

ADK + Gemini API / Cloud Run / Cloud Build / BigQuery / Cloud Trace / Cloud Logging。**外部 LLM（Claude / OpenAI 等）への依存は一切追加しない**。

## SSOT マップ（ハードコード禁止カテゴリ）

| カテゴリ | 格納場所 |
|---|---|
| 評価しきい値・カナリア比率等のデフォルト | `config/defaults.*`（実装時に作成） |
| 環境別 URL・プロジェクト ID | `.env.example` + config 層 |
| Gemini モデル ID | config 層（コード直書き禁止） |

実装前に該当ファイルを Grep し、見つからなければ SSOT に追加してから参照する。

## Git
- コミットメッセージは英語
- 作業ブランチ: `develop`（`main` への直接コミット禁止）
- ライセンス: Apache-2.0、著作権者は個人名（`Copyright (c) 2026 Takumi Morimoto`）
- コミット時のユーザー情報:
```bash
git -c user.name="takumimorimoto-yakumo" -c user.email="takumi.morimoto@yakumo.world" commit -m "..."
```
