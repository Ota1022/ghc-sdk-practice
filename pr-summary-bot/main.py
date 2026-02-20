"""PR Summary Bot - GitHub Copilot SDKを使ったPR要約エージェント"""

import asyncio
import json
import os
import sys

from copilot import CopilotClient
from github import Github

MAX_PATCH_CHARS_PER_FILE = 3000  # ファイルあたりのdiff上限
MAX_TOTAL_PATCH_CHARS = 50000  # 全ファイル合計のdiff上限


def get_env(name: str) -> str:
    """環境変数を取得する。未設定の場合はエラー終了。"""
    value = os.environ.get(name)
    if not value:
        print(f"Error: {name} is not set", file=sys.stderr)
        sys.exit(1)
    return value


def get_pr_info(gh: Github, repo_name: str, pr_number: int) -> dict:
    """GitHub APIからPR情報を取得する。"""
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    # PRのファイル変更一覧を取得
    files = []
    total_patch_chars = 0
    for f in pr.get_files():
        file_info = {
            "filename": f.filename,
            "status": f.status,
            "additions": f.additions,
            "deletions": f.deletions,
            "changes": f.changes,
        }
        # patchが大きすぎる場合は切り詰める（トークン制限対策）
        if f.patch:
            patch = f.patch
            if len(patch) > MAX_PATCH_CHARS_PER_FILE:
                patch = patch[:MAX_PATCH_CHARS_PER_FILE] + "\n... (truncated)"
            file_info["patch"] = patch
        # patchの有無にかかわらずfile_info全体のシリアライズサイズでキャップを計算する。
        # patchのみカウントするとバイナリ・リネームファイルが大量にある場合に
        # キャップが効かずプロンプトが肥大化するため。
        total_patch_chars += len(json.dumps(file_info, ensure_ascii=False))
        files.append(file_info)
        # 全体合計が大きすぎる場合は打ち切り
        if total_patch_chars > MAX_TOTAL_PATCH_CHARS:
            files.append({"filename": "... (remaining files omitted)", "status": "", "additions": 0, "deletions": 0, "changes": 0})
            break

    # コミットメッセージ一覧を取得
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


def build_prompt(pr_info: dict) -> str:
    """PR情報からプロンプトを構築する。"""
    files_summary = json.dumps(pr_info["files"], ensure_ascii=False, indent=2)
    commits_text = "\n".join(f"- {msg}" for msg in pr_info["commits"])

    return f"""以下のPull Requestの内容を分析し、日本語で要約してください。

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

---

以下のフォーマットで出力してください（Markdown形式）:

## 📝 変更内容の要約
（変更全体を2〜3文で簡潔に説明）

## 🏷️ 変更カテゴリ
（以下から該当するものを選択: 機能追加 / バグ修正 / リファクタリング / ドキュメント / テスト / 設定変更 / 依存関係更新）

## 👀 レビューポイント
（レビュアーが特に注目すべき箇所を箇条書きで）

## 📐 影響範囲
（この変更が影響する範囲を簡潔に）
"""


SYSTEM_MESSAGE = """\
あなたはコードレビュー支援エージェントです。
Pull Requestの差分を読み取り、変更内容を正確かつ簡潔に要約します。
技術的に正確で、レビュアーの意思決定に役立つ情報を提供してください。
出力は必ず日本語のMarkdown形式で行ってください。
"""


async def generate_summary(pr_info: dict) -> str:
    """Copilot SDKを使ってPRの要約を生成する。"""
    prompt = build_prompt(pr_info)

    async with CopilotClient() as client:
        session = await client.create_session(
            {
                "model": "gpt-4.1",
                "system_message": {
                    "mode": "replace",
                    "content": SYSTEM_MESSAGE,
                },
                # PR要約にはファイル操作等の組み込みツールは不要
                "available_tools": [],
            }
        )

        response = await session.send_and_wait({"prompt": prompt}, timeout=120.0)
        await session.destroy()

    return response.data.content


def post_comment(gh: Github, repo_name: str, pr_number: int, body: str) -> None:
    """PRにコメントを投稿する。"""
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)

    comment_header = "<!-- pr-summary-bot -->\n"
    comment_body = f"{comment_header}# 🤖 PR Summary\n\n{body}"

    # 既存のbotコメントがあれば更新、なければ新規作成
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

    print("Generating summary with Copilot SDK...")
    summary = await generate_summary(pr_info)
    print("Summary generated successfully.")

    print("Posting comment to PR...")
    post_comment(gh, repo_name, pr_number, summary)
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
