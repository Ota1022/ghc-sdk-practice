# GitHub Copilot SDK で PR Summary Bot を作る — セットアップ & 実践ガイド

このガイドでは、GitHub Copilot SDK（Python）を使って **PR が作成されたら自動で要約コメントを投稿する bot** を、ゼロから動かすところまで解説します。

---

## 0. 完成イメージ

PR を作成すると、bot が diff を読み取り、以下のようなコメントを自動投稿します。

```
🤖 PR Summary

## 📝 変更内容の要約
ログイン画面のバリデーションロジックを追加し、...

## 🏷️ 変更カテゴリ
機能追加

## 👀 レビューポイント
- パスワード強度チェックのロジックが...
- エラーメッセージの国際化対応が...

## 📐 影響範囲
ログイン・サインアップ画面に影響。既存APIへの変更なし。
```

### 処理の流れ

```
main.py を実行
    ├── 1. GitHub API で PR 情報を取得（diff, コミット, メタデータ）
    ├── 2. Copilot SDK にその情報を渡して要約を生成
    └── 3. GitHub API で PR にコメントを投稿
```

---

## 1. 前提条件

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) がインストール済み
- GitHub Copilot サブスクリプション（Individual / Business / Enterprise）
- GitHub アカウント

---

## 2. Copilot CLI のインストール

Copilot SDK は単体では動きません。内部で **Copilot CLI をサーバーモードで起動**し、JSON-RPC で通信します。そのため、SDK とは別に CLI のインストールが必要です。

```bash
# 1. GitHub CLI がまだなら先にインストール
brew install gh

# 2. GitHub CLI で認証（ブラウザが開く）
gh auth login

# 3. Copilot CLI 拡張をインストール
gh extension install github/gh-copilot

# 4. 動作確認
copilot --version
```

> **Note**: Copilot CLI は Technical Preview 段階のためインストール方法が変更される可能性があります。
> 最新情報は https://github.com/github/copilot-sdk を参照してください。

---

## 3. プロジェクトのセットアップ

```bash
# プロジェクト作成（pyproject.toml も生成される）
uv init --python 3.12 pr-summary-bot
cd pr-summary-bot

# SDK と依存パッケージを追加
uv add github-copilot-sdk PyGithub
```

インストールされたか確認：

```bash
uv tree
```

既存プロジェクトをセットアップする場合は、次だけでOKです。

```bash
cd pr-summary-bot
uv venv --python 3.12
uv sync
```

---

## 4. まずは SDK の動作確認（hello.py）

いきなり bot を作る前に、SDK が正しく動くか最小コードで確認します。`hello.py` を作成してください。

```python
import asyncio
from copilot import CopilotClient


async def main():
    client = CopilotClient()
    await client.start()
    try:
        # セッションを作成（モデルを指定）
        session = await client.create_session({"model": "gpt-4.1"})

        # メッセージを送って、完了まで待機
        response = await session.send_and_wait(
            {"prompt": "Pythonでfizzbuzzを書いて"},
            timeout=60.0,
        )
        print(response.data.content)

        # セッションを破棄
        await session.destroy()
    finally:
        await client.stop()


asyncio.run(main())
```

```bash
uv run python hello.py
```

FizzBuzz のコードが返ってくれば環境構築は完了です。エラーが出た場合は「トラブルシューティング」を参照してください。

### ここで押さえておく SDK の基本

```
CopilotClient 作成 → start() → create_session() → send_and_wait() → destroy() → stop()
                     ^^^^^^^^                                                       ^^^^^^
                  明示的に呼ぶ必要がある（v0.1.0 は async with 未対応）
```

- **CopilotClient**: Copilot CLI プロセスの起動・管理を行う。`start()` / `stop()` を `try-finally` で明示的に呼ぶ。
- **Session**: 1つの会話スレッド。モデルやシステムメッセージの設定はここで行う。
- **send_and_wait()**: メッセージを送り、LLM の応答が完了するまで待つ。最もシンプルな呼び方。

> **Note: `async with` について**
> SDK の `client.py` docstring には `async with CopilotClient() as client:` の使用例が記載されていますが、v0.1.0 では `__aenter__` / `__aexit__` が未実装のため動作しません（`TypeError` になります）。
> `async with` サポートは [issue #341](https://github.com/github/copilot-sdk/issues/341) で要望が上がっており、[PR #475](https://github.com/github/copilot-sdk/pull/475) で実装済みです。次バージョン以降でリリースされる予定のため、それまでは上記の `start()` / `stop()` パターンを使ってください。

---

## 5. PR Summary Bot の実装を理解する

ここからが本題です。bot は大きく3つのステップで動きます。

### ステップ 1: GitHub API で PR 情報を取得する

PyGithub を使って、対象 PR の diff やコミット情報を取得します。

```python
from github import Github

def get_pr_info(gh: Github, repo_name: str, pr_number: int) -> dict:
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    # 変更ファイル一覧を取得（diff 付き）
    files = []
    for f in pr.get_files():
        file_info = {
            "filename": f.filename,
            "status": f.status,        # "added", "modified", "removed" 等
            "additions": f.additions,
            "deletions": f.deletions,
            "patch": f.patch,           # 実際の diff テキスト
        }
        files.append(file_info)

    # コミットメッセージ一覧
    commits = [c.commit.message for c in pr.get_commits()]

    return {
        "title": pr.title,
        "body": pr.body or "",
        "author": pr.user.login,
        "base": pr.base.ref,
        "head": pr.head.ref,
        "commits": commits,
        "files": files,
    }
```

**ポイント**: 大きな PR だと diff が膨大になり、LLM のトークン制限に引っかかります。実際のコード（`main.py`）ではファイルあたり 3,000 文字、全体で 50,000 文字の上限を設けて切り詰めています。

### ステップ 2: Copilot SDK で要約を生成する

取得した PR 情報をプロンプトに組み立て、Copilot SDK に渡します。

```python
SYSTEM_MESSAGE = """\
あなたはコードレビュー支援エージェントです。
Pull Requestの差分を読み取り、変更内容を正確かつ簡潔に要約します。
技術的に正確で、レビュアーの意思決定に役立つ情報を提供してください。
出力は必ず日本語のMarkdown形式で行ってください。
"""

async def generate_summary(pr_info: dict) -> str:
    prompt = build_prompt(pr_info)  # PR情報をプロンプト文字列に整形

    async with CopilotClient() as client:
        session = await client.create_session({
            "model": "gpt-4.1",
            "system_message": {
                "mode": "replace",       # デフォルトのガードレールを使わず自分で制御
                "content": SYSTEM_MESSAGE,
            },
            "available_tools": [],       # ファイル操作等の組み込みツールは不要なので無効化
        })

        response = await session.send_and_wait(
            {"prompt": prompt},
            timeout=120.0,  # 大きなPRは時間がかかるので長めに
        )
        await session.destroy()

    return response.data.content
```

**ポイント**:
- `system_message` の `mode: "replace"` を使うと、Copilot のデフォルト指示を完全に自分のものに置き換えられます。要約 bot のように用途が明確な場合はこちらが適しています。
- `available_tools: []` で組み込みツール（ファイル操作等）を無効化しています。PR 要約には不要で、余計なツール呼び出しを防ぎます。

### ステップ 3: PR にコメントを投稿する

生成された要約を PR コメントとして投稿します。

```python
def post_comment(gh: Github, repo_name: str, pr_number: int, body: str) -> None:
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    # HTMLコメントをマーカーとして使い、既存の bot コメントを識別
    comment_header = "<!-- pr-summary-bot -->\n"
    comment_body = f"{comment_header}# 🤖 PR Summary\n\n{body}"

    # 既存の bot コメントがあれば更新、なければ新規作成
    for comment in pr.get_issue_comments():
        if comment.body.startswith(comment_header):
            comment.edit(comment_body)
            print(f"Updated existing comment: {comment.html_url}")
            return

    comment = pr.create_issue_comment(comment_body)
    print(f"Posted comment: {comment.html_url}")
```

**ポイント**: `<!-- pr-summary-bot -->` という HTML コメントをマーカーにしています。PR が更新されて再実行された場合、コメントが増殖せずに既存のものを上書きします。

---

## 6. ローカルで実行してみる

### テスト用 PR を作成する

bot を試すには、コメントを投稿できる PR が必要です。diff が複雑なほど要約の品質を確認しやすいため、複数ファイル・複数コミットで構成します。

```bash
# テスト用リポジトリを作成
gh repo create pr-bot-test --public --clone
cd pr-bot-test

# ベースとなるファイル群を main に追加
cat > auth.py << 'EOF'
def login(username, password):
    if username == "admin" and password == "password":
        return True
    return False
EOF

cat > db.py << 'EOF'
import sqlite3

def get_user(user_id):
    conn = sqlite3.connect("app.db")
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
    return cursor.fetchone()
EOF

git add . && git commit -m "Initial commit: add auth and db modules"
git push -u origin main

# 変更用ブランチを作成
git checkout -b feat/improve-auth-and-db

# コミット1: 認証ロジックをハッシュ化 + バリデーション追加
cat > auth.py << 'EOF'
import hashlib
import re

MIN_PASSWORD_LENGTH = 8

def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def validate_password(password: str) -> bool:
    """パスワードポリシーのバリデーション"""
    if len(password) < MIN_PASSWORD_LENGTH:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[0-9]", password):
        return False
    return True

def login(username: str, password: str, users_db: dict) -> bool:
    if not username or not validate_password(password):
        return False
    hashed = _hash_password(password)
    return users_db.get(username) == hashed
EOF

git add auth.py && git commit -m "feat: replace plaintext password check with SHA-256 hashing"

# コミット2: SQLインジェクション修正 + コネクション管理改善
cat > db.py << 'EOF'
import sqlite3
from contextlib import contextmanager

DATABASE = "app.db"

@contextmanager
def get_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def get_user(user_id: int) -> dict | None:
    """SQLインジェクション対策済みのユーザー取得"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_users_by_role(role: str) -> list[dict]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, role FROM users WHERE role = ?", (role,))
        return [dict(row) for row in cursor.fetchall()]
EOF

git add db.py && git commit -m "fix: prevent SQL injection and improve connection management"

# PR を作成
git push -u origin feat/improve-auth-and-db
gh pr create \
  --title "fix: improve auth security and fix SQL injection vulnerability" \
  --body "## 概要
認証モジュールのセキュリティ改善と、SQLインジェクション脆弱性の修正。

## 変更内容
- パスワードを平文比較から SHA-256 ハッシュ比較に変更
- パスワードポリシーのバリデーション追加（8文字以上、大文字・数字を含む）
- SQL文の動的生成をプレースホルダーに置換
- DB接続をコンテキストマネージャで管理するよう整理" \
  --base main
```

PR が作成されると末尾に URL が表示されます。番号（例: `1`）を次のステップで使います。

---

実際に動かしてみましょう。対象にしたい PR が存在するリポジトリと PR 番号を用意してください。

```bash
# 環境変数を設定
export GITHUB_TOKEN="ghp_xxxxxxxxxxxx"        # GitHub Personal Access Token
export GITHUB_REPOSITORY="owner/repo-name"    # 対象リポジトリ（例: octocat/hello-world）
export PR_NUMBER="123"                        # 対象PR番号

# 実行
uv run python main.py
```

成功すると以下のような出力が表示されます：

```
Fetching PR #123 from owner/repo-name...
  Title: Add login validation
  Files changed: 5
Generating summary with Copilot SDK...
Summary generated successfully.
Posting comment to PR...
Posted comment: https://github.com/owner/repo-name/pull/123#issuecomment-xxxxx
Done!
```

GitHub で該当 PR を開くと、bot のコメントが投稿されているはずです。

### 認証について

Copilot SDK は以下の優先順位で認証トークンを探します：

1. `CopilotClient(github_token="...")` で直接指定
2. 環境変数 `COPILOT_GITHUB_TOKEN`
3. 環境変数 `GH_TOKEN`
4. 環境変数 `GITHUB_TOKEN`
5. Copilot CLI に保存済みの資格情報（`gh auth login` 済みなら自動）

ローカルでは `gh auth login` 済みなら何も設定しなくても動きます。`GITHUB_TOKEN` は PyGithub（GitHub API）の認証に必要です。

---

## 7. トラブルシューティング

### `copilot: command not found`

Copilot CLI がインストールされていません。セクション2を参照してください。

### 認証エラー

```bash
# GitHub CLI の認証状態を確認
gh auth status

# 再認証
gh auth login
```

### タイムアウト

`send_and_wait()` の `timeout` を増やしてください。大きな PR の場合は 120 秒以上を推奨します。

### SDK のバージョン確認

```bash
pip show github-copilot-sdk
```

---

## 8. Multi-Perspective Review Bot（マルチエージェント拡張）

`main.py` の単体要約に加えて、**3つの専門エージェントが並列でレビューし、結果を統合する** `multi_review.py` も用意しています。

### アーキテクチャ: Fan-out / Fan-in パターン

```
PR diff ──┬──> [Security Agent]      ──┐
          ├──> [Performance Agent]   ──┼──> [Orchestrator Agent] ──> PR Comment
          └──> [Readability Agent]   ──┘
              (asyncio.gather で並列)         (結果を統合・優先度付け)
```

- SDK の `create_session()` を 3 つ並列で実行し、それぞれ異なる `system_message` を持つ
- `asyncio.gather()` で並列待機するため、直列実行の約 1/3 の時間で完了
- Orchestrator セッションが 3 つの結果を受け取り、重複排除・優先度付けして最終レポートを生成

### 各レビュアーの観点

| レビュアー | 主な観点 |
|---|---|
| Security | SQLインジェクション、XSS、認証の欠陥、シークレット漏洩、依存パッケージの脆弱性 |
| Performance | O(n²) ループ、N+1 クエリ、メモリリーク、不要な再計算 |
| Readability | 命名規則、関数の複雑度、デッドコード、SOLID 原則 |

### Orchestrator の出力フォーマット

3 つのレビュー結果を **Critical / Warning / Suggestion** の 3 段階に分類し、ファイル名・該当箇所付きで出力します。

### 実行方法

```bash
# 環境変数を設定（main.py と同じ）
export GITHUB_TOKEN="ghp_xxxxxxxxxxxx"
export GITHUB_REPOSITORY="owner/repo-name"
export PR_NUMBER="123"

# 実行
uv run python multi_review.py
```

成功すると対象 PR に 3 観点の統合レビューコメントが投稿されます。

### Copilot SDK のマルチセッション活用ポイント

```python
async with CopilotClient() as client:
    # 1つの CopilotClient から複数セッションを並列作成できる
    results = await asyncio.gather(
        run_reviewer(client, "security", REVIEWERS["security"], prompt),
        run_reviewer(client, "performance", REVIEWERS["performance"], prompt),
        run_reviewer(client, "readability", REVIEWERS["readability"], prompt),
    )
```

- **1 つの `CopilotClient` インスタンスを共有**して複数セッションを作成可能
- 各セッションは独立した `system_message` を持てるため、専門エージェントの振る舞いを個別に制御できる
- `asyncio.gather()` で並列実行することで、単純な直列実行より大幅に高速化
- これは Copilot の組み込み機能では実現できない、**SDK ならではのマルチエージェント構成**

---

## 参考リンク

- [github/copilot-sdk](https://github.com/github/copilot-sdk) - 公式リポジトリ
- [Python SDK README](https://github.com/github/copilot-sdk/blob/main/python/README.md)
- [Getting Started Guide](https://github.com/github/copilot-sdk/blob/main/docs/getting-started.md)
- [PyPI: github-copilot-sdk](https://pypi.org/project/github-copilot-sdk/)
- [awesome-copilot Python instructions](https://github.com/github/awesome-copilot/blob/main/instructions/copilot-sdk-python.instructions.md)
- [Python クックブック](https://github.com/github/awesome-copilot/blob/main/cookbook/copilot-sdk/python/README.md)
