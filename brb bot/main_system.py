import discord
from discord import app_commands, TextChannel, ui, ButtonStyle, Member
from discord.ui import Button, View
from discord.ext import commands, tasks  # 봇 명령어 및 반복 작업(초기화 등)용
from datetime import datetime, date
import random                            # 경험치 랜덤 지급용
# [DB 관리] - 데이터 저장용 (경험치, 출석 기록 등)
import aiosqlite   # 비동기 SQLite (데이터베이스)
# [구글 스프레드시트 연동] - 내전 기록 및 데이터 쓰기용
import gspread
from oauth2client.service_account import ServiceAccountCredentials
# [웹 대시보드 구축] - FastAPI 활용
from fastapi import FastAPI
import uvicorn  # 웹 서버 실행용
import threading    # 봇과 웹 서버를 동시에 돌리기 위함
import asyncio      # 비동기 작업 처리용
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix='!', intents=intents)
    async def setup_hook(self):
        # 작성한 슬래시 명령어를 디스코드 서버에 등록(동기화)합니다.
        await self.tree.sync()
        print("슬래시 명령어 동기화 완료")

bot = MyBot()
DB_NAME = "attendance.db"
voice_user = set() 
multi_role = {
    "역할1": 1,
    "역할2": 2,
    "역할3": 3,
}
attendance_channel = 1383387649917718610
chat_cooldown = {}


def get_user_multiplier(member):
    multiplier = 1.0
    for role in member.roles:
        if role.id in multi_role:
            multiplier = max(multiplier, multi_role[role.id])
    return multiplier

class Dropdown(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="2배", description="경험치를 2배로 얻습니다. 가격: 5000 EXP", emoji="🎃"),
            discord.SelectOption(label="3배", description="경험치를 3배로 얻습니다. 가격: 10000 EXP", emoji="💊"),
            discord.SelectOption(label="차감권", description="경고를 차감합니다. 가격: 15000 EXP", emoji="😡"),
        ]
        super().__init__(placeholder="토끼의 상점에 오신 것을 환영합니다!", options=options)
    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "2배":
            price = 5000
        elif self.values[0] == "3배":
            price = 10000
        elif self.values[0] == "차감권":
            price = 15000
        user_id = interaction.user.id
        if price == 5000:
            role_id = 1479067128366764157  # 2배 역할 ID로 변경
        elif price == 10000:
            role_id = 1479067238324768939  # 3배 역할 ID로 변경
        elif price == 15000:
            role_id = 1479067275792224439  # 차감권 역할 ID로 변경
        async with aiosqlite.connect(DB_NAME) as db:
            # 1. 먼저 유저의 현재 경험치가 충분한지 확인 (SELECT)
            async with db.execute("SELECT experience FROM attendance WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
            
            if not row or row[0] < price:
                await interaction.response.send_message("❌ 경험치가 부족합니다!", ephemeral=True)
                if interaction.message:
                    await interaction.message.delete()
                else:
                    return
        yes = Button(label="응", style=ButtonStyle.blurple)
        no = Button(label="아니", style=ButtonStyle.red)
        view=View()
        view.add_item(yes)
        view.add_item(no)
        await interaction.response.send_message(f"정말로 {self.values[0]} 아이템을 구매하시겠습니까?", view=view)
        async def yes_callback(interaction: discord.Interaction):
            async with aiosqlite.connect(DB_NAME) as db:
                async with db.execute("SELECT experience FROM attendance WHERE user_id = ?", (user_id,)) as cursor:
                    row = await cursor.fetchone()
                if not row: await interaction.response.send_message("❌ 데이터베이스에 사용자 정보가 없습니다.", ephemeral=True)
                if interaction.guild:
                    role = interaction.guild.get_role(role_id)
                await interaction.response.send_message("구매가 완료되었습니다!")
                await db.execute("UPDATE attendance SET experience = experience - ? WHERE user_id = ?", (price, user_id))
                await db.commit()
                if interaction.message:
                    if isinstance(interaction.channel, discord.TextChannel):
                        await interaction.channel.purge(limit=3)
                if isinstance(interaction.user, discord.Member) and role:
                    await interaction.user.add_roles(role)
                    await asyncio.sleep(7200)
                    await interaction.user.remove_roles(role)

                else:
                    return
        async def no_callback(interaction: discord.Interaction):
            await interaction.response.send_message("구매가 취소되었습니다.")
            if interaction.message:
                if isinstance(interaction.channel, discord.TextChannel):
                    await interaction.channel.purge(limit=3)
            else:
                return

        yes.callback = yes_callback
        no.callback = no_callback

class DropdownView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(Dropdown())

# 명령어 부분
@bot.tree.command(name="상점", description="경험치 상점을 엽니다.")
async def open_shop(interaction: discord.Interaction):
    embed = discord.Embed(title="🏪 경험치 포인트 상점", color=discord.Color.gold())
    embed.description = "경험치를 소모하여 특별한 아이템을 구매하세요!"
    view = DropdownView()
    
    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()
    await msg.delete(delay=180)

@bot.tree.command(name="경험치추가", description="사용자에게 경험치를 추가합니다.")
async def add_experience(interaction: discord.Interaction, user: discord.User, amount: int):
    user_id = user.id
    if user_id == None:
        user_id = interaction.user.id
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE attendance SET experience = experience + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
    await interaction.response.send_message(f"✅ {user.mention}님에게 {amount} EXP를 추가했습니다!", ephemeral=True)

@bot.tree.command(name="경험치차감", description="사용자에게서 경험치를 차감합니다.")
async def remove_experience(interaction: discord.Interaction, user: discord.User, amount: int):
    user_id = user.id
    if user_id == None:
        user_id = interaction.user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT experience FROM attendance WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] < amount:
            await interaction.response.send_message(f"❌ {user.mention}님의 경험치가 부족합니다!", ephemeral=True)
            return
        await db.execute("UPDATE attendance SET experience = experience - ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
    await interaction.response.send_message(f"✅ {user.mention}님에게서 {amount} EXP를 차감했습니다!", ephemeral=True)

@bot.tree.command(name="경험치선물", description="다른 사용자에게 경험치를 선물합니다.")
async def gift_experience(interaction: discord.Interaction, recipient: discord.User, amount: int):
    sender_id = interaction.user.id
    recipient_id = recipient.id
    if sender_id == recipient_id:
        await interaction.response.send_message("❌ 자신에게 경험치를 선물할 수 없습니다!", ephemeral=True)
        return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT experience FROM attendance WHERE user_id = ?", (sender_id,)) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] < amount:
            await interaction.response.send_message("❌ 경험치가 부족합니다!", ephemeral=True)
            return
        await db.execute("UPDATE attendance SET experience = experience - ? WHERE user_id = ?", (amount, sender_id))
        await db.execute("UPDATE attendance SET experience = experience + ? WHERE user_id = ?", (amount, recipient_id))
        await db.commit()
    await interaction.response.send_message(f"✅ {recipient.mention}님에게 {amount} EXP를 선물했습니다!")

#[기능] DB 초기화: 봇이 시작될 때 테이블이 없으면 생성
@bot.event
async def on_ready():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS attendance (
                user_id INTEGER PRIMARY KEY,
                nickname TEXT,
                last_attendance TEXT,
                streak INTEGER DEFAULT 0,
                total_count INTEGER DEFAULT 0,
                experience INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS monthly_stats (
                user_id INTEGER,
                month TEXT, 
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, month)
            )
        ''')
        await db.commit()
    if bot.user:
        print(f'준비 완료: {bot.user.name}')
    else:        print('준비 완료: 봇 사용자 정보 없음')

def calculate_level(exp):
    level = 1
    remaining_exp = exp
    while True:
        next_level_exp = int(((10 * level) ** 2 - 5) / 2 + 1)
        if remaining_exp >= next_level_exp:
            level += 1
            remaining_exp -= next_level_exp
        else:
            break
    return level, remaining_exp, next_level_exp

#[명령어] !출석
@bot.tree.command(name="출석", description="오늘의 출석을 기록합니다.")
async def attendance(interaction: discord.Interaction):
    await interaction.response.defer()
    user_id = interaction.user.id
    user_name = interaction.user.display_name
    today = date.today().isoformat()
    
    async with aiosqlite.connect(DB_NAME) as db:
        # 1. 기존 데이터 불러오기
        async with db.execute(
            "SELECT last_attendance, streak, total_count, experience FROM attendance WHERE user_id = ?", 
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            last_date_str, streak, total_count, current_exp = row
            if last_date_str == today:
                await interaction.followup.send(f"⚠️ {interaction.user.mention}님, 오늘은 이미 출석하셨습니다!")
                return

            last_date = date.fromisoformat(last_date_str)
            if (date.today() - last_date).days == 1:
                new_streak = streak + 1
            else:
                new_streak = 1
            
            new_total = total_count + 1
            base_exp = current_exp
        else:
            new_streak = 1
            new_total = 1
            base_exp = 0

        # 2. 레벨 및 경험치 계산
        multiplier = get_user_multiplier(interaction.user)
        gain_exp = int(300 * multiplier)
        new_exp = base_exp + gain_exp

        # 레벨업 체크: 이전 레벨과 이후 레벨 비교
        old_level, _, _ = calculate_level(base_exp)
        new_level, _, _ = calculate_level(new_exp)
        if new_level > old_level:
            await interaction.followup.send(f"🎉 축하합니다! {interaction.user.mention}님이 레벨업 하셨습니다! (Lv. {old_level} → Lv. {new_level})", ephemeral = True)

        # 3. 데이터 업데이트 (물음표 6개 매칭 완료)
        await db.execute("""
            INSERT OR REPLACE INTO attendance (user_id, nickname, last_attendance, streak, total_count, experience)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, user_name, today, new_streak, new_total, new_exp))

        current_month = date.today().strftime("%Y-%m") # 예: "2024-03"
        await db.execute("""
    INSERT INTO monthly_stats (user_id, month, count)
    VALUES (?, ?, 1)
    ON CONFLICT(user_id, month) DO UPDATE SET count = count + 1
""", (user_id, current_month))

        

        await db.commit()
        embed2 = discord.Embed(title=f"✅ 출석체크 하셨습니다!", color=discord.Color.blue(), description= f"출석체크 보상으로 {gain_exp} EXP를 얻었습니다!")
        embed2.add_field(name="연속 출석", value=f"🔥 {new_streak}일차", inline=False)
        embed2.add_field(name="누적 출석", value=f"📊 {new_total}회", inline=True)
    AC = bot.get_channel(attendance_channel)
    if isinstance(AC, TextChannel):
        await AC.send(embed=embed2)
    else: await interaction.followup.send("오류", ephemeral=True)  
    # await interaction.followup.send(f"✅ {interaction.user.mention}님 출석 완료!\n🔥 연속 {new_streak}일차 | 📊 총 {new_total}회 출석 | 💰 +{gain_exp} EXP")

@bot.tree.command(name="내정보", description="당신의 출석 정보를 확인합니다.")
async def my_info(interaction: discord.Interaction, user: Optional [discord.User] = None):
    await interaction.response.defer()
    target_user = user if user else interaction.user
    user_id = target_user.id
    user_name = target_user.display_name
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT experience, streak, total_count FROM attendance WHERE user_id = ?", 
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
    
    if not row:
        await interaction.followup.send("아직 출석 기록이 없습니다. `!출석`을 먼저 해주세요!")
        return

    exp, streak, total = row
    lvl, curr_exp, max_exp = calculate_level(exp)

    # 디스코드 임베드(Embed)로 예쁘게 출력
    embed = discord.Embed(title=f"📊 {user_name}님의 정보", color=discord.Color.blue())
    embed.add_field(name="레벨", value=f"Lv. {lvl}", inline=True)
    embed.add_field(name="경험치", value=f"{int(curr_exp)} / {int(max_exp)} (Total: {int(exp)})", inline=True)
    embed.add_field(name="연속 출석", value=f"🔥 {streak}일차", inline=False)
    embed.add_field(name="누적 출석", value=f"📊 {total}회", inline=True)
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="초기화", description="[관리자 전용] 모든 출석 및 경험치 데이터를 삭제합니다.")
@app_commands.checks.has_permissions(administrator=True)
async def reset_database(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("DROP TABLE IF EXISTS attendance")
            await db.execute('''
                CREATE TABLE IF NOT EXISTS attendance (
                    user_id INTEGER PRIMARY KEY,
                    nickname TEXT,
                    last_attendance TEXT,
                    streak INTEGER DEFAULT 0,
                    total_count INTEGER DEFAULT 0,
                    experience INTEGER DEFAULT 0
                )
            ''')
            await db.commit()
        await interaction.followup.send("✅ 데이터베이스가 초기화되었습니다.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ 초기화 중 오류가 발생했습니다: {e}", ephemeral=True)

EX_channel = ["<CHANNEL_ID>"]  # 임시 채널 ID

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    if after.channel and after.channel.id in EX_channel:
        # 만약 제외 채널로 들어왔다면, 목록에서 제거하고 종료
        if member.id in voice_user:
            voice_user.remove(member.id)
            print(f"🔇 {member.display_name}님이 제외된 채널({after.channel.name})에 입장하여 경험치 지급 중단")
        return

    # 채널에 입장했을 때 (제외 채널이 아님을 위에서 확인 완료)
    if before.channel is None and after.channel is not None:
        voice_user.add(member.id)
        print(f"🎙️ {member.display_name} 음성 채널 입장")

    # 채널에서 퇴장했을 때
    elif before.channel is not None and after.channel is None:
        if member.id in voice_user:
            voice_user.remove(member.id)
            print(f"🔇 {member.display_name} 음성 채널 퇴장")
            
    # 채널 이동 시 (일반 채널 -> 제외 채널로 이동하는 경우 처리)
    elif before.channel is not None and after.channel is not None:
        if after.channel.id in EX_channel:
            if member.id in voice_user:
                voice_user.remove(member.id)
        else:
            voice_user.add(member.id)
    

@tasks.loop(seconds=300)
async def give_voice_exp():
    guild = bot.get_guild(1383387649464729600)  # 서버 ID로 변경
    if not guild: return
    if not voice_user:
        return
    async with aiosqlite.connect(DB_NAME) as db:
        for user_id in voice_user:
            member = guild.get_member(user_id)
            if not member: continue
            async with db.execute(
                "SELECT experience FROM attendance WHERE user_id = ?", 
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    multiplier = get_user_multiplier(member)
                    current_exp = row[0]
                    gain_exp = int(30 * multiplier)
                    new_exp = current_exp + gain_exp
                    await db.execute(
                        "UPDATE attendance SET experience = ? WHERE user_id = ?", 
                        (new_exp, user_id)
                    )

    await db.commit()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.content.startswith(bot.command_prefix):
        await bot.process_commands(message)
        return
    user_id = message.author.id
    now = datetime.now()
    if user_id in chat_cooldown:
        elapsed = (now - chat_cooldown[user_id]).total_seconds()
        if elapsed < 60:  # 1분 쿨다운
            await bot.process_commands(message)
            return
    chat_cooldown[user_id] = now
    async with aiosqlite.connect(DB_NAME) as db:
        multiplier = get_user_multiplier(message.author)
        gain_exp = int(20 * multiplier)
        async with db.execute(
            "SELECT experience FROM attendance WHERE user_id = ?", 
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                current_exp = row[0]
                new_exp = current_exp + gain_exp
                await db.execute(
                    "UPDATE attendance SET experience = ? WHERE user_id = ?", 
                    (new_exp, user_id)
                )
            else:
                await db.execute(
                    "INSERT OR REPLACE INTO attendance (user_id, nickname, last_attendance, streak, total_count, experience) VALUES (?, ?, ?, ?, ?, ?)", 
                    (user_id, message.author.display_name, None, 0, 0, 10)
                )

        await db.commit()
    await bot.process_commands(message)


@tasks.loop(hours=24)
async def monthly_reset_loop():
    now = datetime.now()
    
    if now.day == 1:
        async with aiosqlite.connect(DB_NAME) as db:
            # 방식 1: 출석 관련 기록만 초기화 (경험치는 보존 - 추천)
            await db.execute("""
                UPDATE attendance 
                SET streak = 0, 
                    total_count = 0, 
                    last_attendance = NULL
            """)
            
            await db.commit()
        AC = bot.get_channel(attendance_channel)
        if isinstance(AC, TextChannel):
            await AC.send(f"📅 {now.month}월 1일: 출석 데이터가 초기화되었습니다.")

@bot.tree.command(name="월별기록", description="달별 출석 횟수를 확인합니다.")
async def monthly_history(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    await interaction.response.defer()
    target = user or interaction.user
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT month, count FROM monthly_stats WHERE user_id = ? ORDER BY month DESC", 
            (target.id,)
        ) as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        await interaction.followup.send(f"📅 {target.display_name}님의 과거 출석 기록이 없습니다.")
        return

    embed = discord.Embed(title=f"📅 {target.display_name}님의 월별 출석 기록", color=discord.Color.green())
    for month, count in rows:
        embed.add_field(name=f"{month}", value=f"✅ {count}회 출석", inline=True)
        
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="월별수정", description="[관리자] 유저의 특정 달 출석 횟수를 수정합니다.")
@app_commands.describe(
    user="수정할 유저를 선택하세요.",
    month="수정할 달을 입력하세요 (예: 2026-01)",
    count="설정할 출석 횟수를 입력하세요."
)
@app_commands.checks.has_permissions(administrator=True)
async def edit_monthly_stats(interaction: discord.Interaction, user: discord.Member, month: str, count: int):
    await interaction.response.defer(ephemeral=True)

    # 입력 형식 검증 (YYYY-MM 형식인지 간단히 체크)
    if len(month) != 7 or month[4] != '-':
        await interaction.followup.send("❌ 날짜 형식이 올바르지 않습니다. `YYYY-MM` 형식으로 입력해주세요. (예: 2026-03)", ephemeral=True)
        return

    async with aiosqlite.connect(DB_NAME) as db:
        try:
            # 기록이 있으면 업데이트, 없으면 새로 삽입
            await db.execute("""
                INSERT INTO monthly_stats (user_id, month, count)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, month) DO UPDATE SET count = ?
            """, (user.id, month, count, count))
            
            await db.commit()
            await interaction.followup.send(f"✅ {user.display_name}님의 {month} 출석 횟수가 **{count}회**로 수정되었습니다.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 수정 중 오류 발생: {e}", ephemeral=True)


TOKEN = os.getenv('DISCORD_TOKEN')
if TOKEN:
    bot.run(TOKEN)
else:    print("❌ DISCORD_TOKEN이 설정되지 않았습니다. .env 파일을 확인해주세요.")