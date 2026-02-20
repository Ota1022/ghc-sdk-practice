# PR Summary Bot

GitHub Copilot SDK を使った PR 要約 bot です。
Pull Request が作成・更新されると、自動で変更内容を要約してコメントを投稿します。

## 機能

- PR の diff を分析して日本語で要約
- 変更カテゴリの自動分類（機能追加 / バグ修正 / リファクタ等）
- レビュー時に注目すべきポイントの提示
- 影響範囲の推定
- 既存の bot コメントがあれば上書き更新

## 前提条件

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) がインストール済み
- [GitHub Copilot CLI](https://github.com/github/copilot-sdk) がインストール済み
- GitHub Copilot サブスクリプション（Individual / Business / Enterprise）

## セットアップ

### ローカル実行

```bash
# 0) uv が未インストールなら導入（macOS）
# brew install uv

# 1) （初回のみ）仮想環境を作成
uv venv --python 3.12

# 2) 依存パッケージを同期（pyproject.toml から）
uv sync

# 3) 環境変数の設定
export GITHUB_TOKEN="your-github-token"
export GITHUB_REPOSITORY="owner/repo"
export PR_NUMBER="123"

# 4) 実行
uv run python main.py
```

依存を追加する場合は `uv add パッケージ名` を使い、その後 `uv sync` を実行します。

### GitHub Actions

1. リポジトリに `.github/workflows/pr-summary.yml` を配置
2. Copilot 認証用のシークレットを設定（必要に応じて）
3. PR を作成すると自動で要約コメントが投稿されます

## アーキテクチャ

```
PR作成/更新
    ↓
GitHub Actions トリガー
    ↓
main.py 実行
    ├── GitHub API で PR 情報取得（diff, コミット, メタデータ）
    ├── Copilot SDK でエージェントに要約を依頼
    └── GitHub API で PR にコメント投稿
```

## 注意事項

- GitHub Copilot SDK は Technical Preview 段階です。API が変更される可能性があります。
- 大きな PR の場合、diff が切り詰められることがあります（トークン制限対策）。
