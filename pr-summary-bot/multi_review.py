"""Multi-Perspective Review Bot - 複数エージェントによる並列PRレビュー"""

import asyncio
import sys

from copilot import CopilotClient
from github import Github

from main import get_env, get_pr_info, post_comment

# --- 各レビュアーのシステムメッセージ ---

REVIEWERS = {
    "security": """\
あなたはセキュリティ専門のコードレビュアーです。
Pull Requestの差分を読み取り、セキュリティ上の問題を指摘してください。

特に以下の観点でレビューしてください:
- SQLインジェクション、XSS、コマンドインジェクション等のインジェクション攻撃
- 認証・認可の欠陥（不適切なアクセス制御、セッション管理の問題）
- シークレットやAPIキーのハードコーディング・漏洩
- 安全でないデシリアライゼーション
- 依存パッケージの既知の脆弱性
- 入力値バリデーションの不備
- 暗号化の不適切な使用

出力は必ず日本語のMarkdown形式で、以下のフォーマットに従ってください:

### 指摘事項
（各指摘を箇条書きで。ファイル名と該当箇所を明記）

### 総評
（セキュリティ観点での全体評価を1〜2文で）

指摘がない場合は「セキュリティ上の問題は検出されませんでした」と明記してください。
""",
    "performance": """\
あなたはパフォーマンス専門のコードレビュアーです。
Pull Requestの差分を読み取り、パフォーマンス上の問題を指摘してください。

特に以下の観点でレビューしてください:
- O(n²) 以上の計算量を持つループ・アルゴリズム
- N+1 クエリ問題（ループ内でのDB/APIアクセス）
- メモリリーク（解放されないリソース、肥大化するデータ構造）
- 不要な再計算（キャッシュすべき値の繰り返し計算）
- 大きなデータのコピー（不要なディープコピー、巨大リストの複製）
- ブロッキング処理（async で書くべき同期処理）
- 不適切なデータ構造の選択

出力は必ず日本語のMarkdown形式で、以下のフォーマットに従ってください:

### 指摘事項
（各指摘を箇条書きで。ファイル名と該当箇所を明記）

### 総評
（パフォーマンス観点での全体評価を1〜2文で）

指摘がない場合は「パフォーマンス上の問題は検出されませんでした」と明記してください。
""",
    "readability": """\
あなたはコード品質・可読性専門のコードレビュアーです。
Pull Requestの差分を読み取り、可読性やメンテナンス性の問題を指摘してください。

特に以下の観点でレビューしてください:
- 不適切な命名（変数名、関数名、クラス名）
- 関数の複雑度（長すぎる関数、ネストの深さ）
- デッドコード（使われていないコード、到達不能コード）
- SOLID 原則への違反
- DRY 原則への違反（コードの重複）
- マジックナンバー・マジックストリング
- 不足しているエラーハンドリング
- コードの意図が不明確な箇所

出力は必ず日本語のMarkdown形式で、以下のフォーマットに従ってください:

### 指摘事項
（各指摘を箇条書きで。ファイル名と該当箇所を明記）

### 総評
（可読性・メンテナンス性の観点での全体評価を1〜2文で）

指摘がない場合は「可読性上の問題は検出されませんでした」と明記してください。
""",
}

ORCHESTRATOR_SYSTEM_MSG = """\
あなたはコードレビュー結果を統合するオーケストレーターです。
3人の専門レビュアー（セキュリティ・パフォーマンス・可読性）の結果を受け取り、
重複を排除し、優先度を付けて最終レポートを生成してください。

以下のルールに従ってください:
- 同じ箇所への指摘が複数レビュアーから出ている場合は1つにまとめる
- 各指摘を Critical / Warning / Suggestion の3段階で分類する
  - Critical: セキュリティ脆弱性、データ損失の可能性、本番障害につながる問題
  - Warning: パフォーマンス劣化、メンテナンス性の低下など、放置すると問題になりうるもの
  - Suggestion: より良い書き方の提案、軽微な改善点
- ファイル名・該当箇所を必ず明記する

出力は必ず日本語のMarkdown形式で、以下のフォーマットに従ってください:

## 🔴 Critical
（該当する指摘を箇条書き。なければ「なし」）

## 🟡 Warning
（該当する指摘を箇条書き。なければ「なし」）

## 🟢 Suggestion
（該当する指摘を箇条書き。なければ「なし」）

## 📊 総合評価
（全体的な品質評価を2〜3文で）
"""


def build_review_prompt(pr_info: dict) -> str:
    """レビュー用プロンプトを構築する。"""
    import json

    files_summary = json.dumps(pr_info["files"], ensure_ascii=False, indent=2)
    commits_text = "\n".join(f"- {msg}" for msg in pr_info["commits"])

    return f"""以下のPull Requestの差分をレビューしてください。

## PR情報
- タイトル: {pr_info["title"]}
- 作成者: {pr_info["author"]}
- ブランチ: {pr_info["head"]} → {pr_info["base"]}

## PR説明文
{pr_info["body"]}

## コミット一覧
{commits_text}

## 変更ファイル
{files_summary}
"""


async def run_reviewer(client: CopilotClient, name: str, system_msg: str, prompt: str) -> str:
    """1つのレビュアーセッションを実行する。"""
    print(f"  [{name}] レビュー開始...")
    session = await client.create_session(
        {
            "model": "gpt-4.1",
            "system_message": {
                "mode": "replace",
                "content": system_msg,
            },
            "available_tools": [],
        }
    )
    response = await session.send_and_wait({"prompt": prompt}, timeout=120.0)
    await session.destroy()
    print(f"  [{name}] レビュー完了")
    return response.data.content


async def run_orchestrator(client: CopilotClient, reviews: dict[str, str]) -> str:
    """3つのレビュー結果を統合する。"""
    print("  [orchestrator] 結果を統合中...")
    session = await client.create_session(
        {
            "model": "gpt-4.1",
            "system_message": {
                "mode": "replace",
                "content": ORCHESTRATOR_SYSTEM_MSG,
            },
            "available_tools": [],
        }
    )

    prompt = "以下の3人の専門レビュアーの結果を統合してください。\n\n"
    for reviewer_name, content in reviews.items():
        prompt += f"---\n### {reviewer_name} レビュアーの結果\n{content}\n\n"

    response = await session.send_and_wait({"prompt": prompt}, timeout=120.0)
    await session.destroy()
    print("  [orchestrator] 統合完了")
    return response.data.content


async def generate_multi_review(pr_info: dict) -> str:
    """メイン処理: fan-out → fan-in でマルチレビューを実行する。"""
    prompt = build_review_prompt(pr_info)

    client = CopilotClient()
    await client.start()
    try:
        # Fan-out: 3つのレビュアーを並列実行
        results = await asyncio.gather(
            run_reviewer(client, "security", REVIEWERS["security"], prompt),
            run_reviewer(client, "performance", REVIEWERS["performance"], prompt),
            run_reviewer(client, "readability", REVIEWERS["readability"], prompt),
        )

        reviews = {
            "Security（セキュリティ）": results[0],
            "Performance（パフォーマンス）": results[1],
            "Readability（可読性）": results[2],
        }

        # Fan-in: オーケストレーターが結果を統合
        return await run_orchestrator(client, reviews)
    finally:
        await client.stop()


def post_review_comment(gh: Github, repo_name: str, pr_number: int, body: str) -> None:
    """PRにマルチレビュー結果をコメントとして投稿する。"""
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    comment_header = "<!-- pr-multi-review-bot -->\n"
    comment_body = (
        f"{comment_header}"
        "# 🤖 Multi-Perspective Review\n\n"
        "_Security / Performance / Readability の3観点で自動レビューしました_\n\n"
        f"{body}"
    )

    for comment in pr.get_issue_comments():
        if comment.body.startswith(comment_header):
            comment.edit(comment_body)
            print(f"Updated existing comment: {comment.html_url}")
            return

    comment = pr.create_issue_comment(comment_body)
    print(f"Posted comment: {comment.html_url}")


async def main() -> None:
    github_token = get_env("GITHUB_TOKEN")
    repo_name = get_env("GITHUB_REPOSITORY")
    pr_number = int(get_env("PR_NUMBER"))

    gh = Github(github_token)

    print(f"Fetching PR #{pr_number} from {repo_name}...")
    pr_info = get_pr_info(gh, repo_name, pr_number)
    print(f"  Title: {pr_info['title']}")
    print(f"  Files changed: {len(pr_info['files'])}")

    print("Running multi-perspective review...")
    review = await generate_multi_review(pr_info)
    print("Review completed.")

    print("Posting review comment to PR...")
    post_review_comment(gh, repo_name, pr_number, review)
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
