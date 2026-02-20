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