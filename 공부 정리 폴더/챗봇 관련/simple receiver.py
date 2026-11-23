import asyncio
import simple_discord

async def handle_client(reader, writer):
    data = await reader.read(1024)
    if data:
        message = data.decode("utf-8").strip()
        print(f"\n[TCP 수신] {message}")
        
        # 수신된 메시지 처리를 외부 모듈로 위임
        await simple_discord.send_message_to_channel(message)
        
    writer.close()
    await writer.wait_closed()

async def main():
    # TCP 서버 구동
    server = await asyncio.start_server(handle_client, "127.0.0.1", 9000)
    print("[시스템] TCP 서버 대기 중... (127.0.0.1:9000)")
    
    # 서버 루프와 외부(디스코드) 봇 루프를 병렬로 실행
    await asyncio.gather(
        server.serve_forever(),
        simple_discord.start_discord_bot() # 토큰 없이 함수만 호출
    )

if __name__ == "__main__":
    asyncio.run(main())
