import discord

# 디스코드 관련 설정값을 모듈 내부에서 전역으로 관리
DISCORD_TOKEN = "여기에_디스코드_봇_토큰을_입력하세요"
TARGET_CHANNEL_ID = 123456789012345678

intents = discord.Intents.default()
bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    print(f"[시스템] 디스코드 봇 연동 완료: {bot.user}")

async def start_discord_bot():
    """외부(이벤트 루프)에서 호출할 수 있는 봇 실행 래퍼(Wrapper) 함수"""
    await bot.start(DISCORD_TOKEN)

async def send_message_to_channel(text):
    """TCP 서버가 수신한 데이터를 디스코드 채널로 전송하는 함수"""
    await bot.wait_until_ready()
    
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        await channel.send(f"📡 수신된 데이터: **{text}**")
        print(f"[디스코드 송신 완료] {text}")
    else:
        print(f"[경고] 채널 ID를 찾을 수 없음. 콘솔 출력 대체: {text}")