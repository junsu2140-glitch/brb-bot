import discord
from discord import app_commands, TextChannel, ui, ButtonStyle, Member
from discord.ui import Button, View
from discord.ext import commands, tasks  
from datetime import datetime, date
import random                         
import aiosqlite   
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from fastapi import FastAPI
import uvicorn  
import threading    
import asyncio    
from typing import Optional
import os
from dotenv import load_dotenv
import json

load_dotenv()

current_path = os.path.dirname(os.path.abspath(__file__))
creds_path = os.path.join(current_path, "creds.json")

with open('/root/brb-bot/creds.json', 'r') as f:
    creds_info = json.load(f)
    
creds_info['private_key'] = creds_info['private_key'].replace('\\n', '\n')

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
    ]



gc = gspread.service_account_from_dict(creds_info, scopes=scope)

doc = gc.open("[관리자용] BRB 티어 리스트")

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix='!', intents=intents)
    async def setup_hook(self):
        give_voice_exp.start()  # 음성 경험치 루프 시작
        monthly_reset_loop.start()  # 월별 초기화 루프 시작
        # 작성한 슬래시 명령어를 디스코드 서버에 등록(동기화)합니다.
        await self.tree.sync()
        print("슬래시 명령어 동기화 완료")

bot = MyBot()
DB_NAME = "attendance.db"
voice_user = set()
multi_role = {
    1470331131315355760: 1, #2배 3시간
    1470717115135819912: 2, #2배 6시간
    1470713107029561456: 3, #3배 24시간
}
attendance_channel = 1383387649917718610 # 출석체크 채널
lvl_channel = 12345678901234567890 # 레벨업 채널
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
            discord.SelectOption(label="2배 부스트 3시간 ON", description="경험치를 3시간동안 2배 받습니다. [ 800🥕]", emoji="💊"),
            discord.SelectOption(label="2배 부스트 6시간 ON", description="경험치를 6시간동안 2배 받습니다. [ 1500🥕]", emoji="🧪"),
            discord.SelectOption(label="3배 부스트 24시간 ON", description="경험치를 24시간동안 3배 받습니다. [ 5000🥕]", emoji="⚗️"),
            discord.SelectOption(label="내전 작은 경고 차감권", description="내전 작은 경고 차감권 [ 60레벨 이상 구매 가능 40000🥕]", emoji="💦"),
            discord.SelectOption(label="내전 큰 경고 차감권", description="내전 큰 경고 차감권 [ 60레벨 이상 구매 가능 100000🥕]", emoji="💢"),
            discord.SelectOption(label="내전 참전 금지 해제권", description="내전 2주 불참 해지권 [ 60레벨 이상 구매 가능 20000🥕]", emoji="💥"),
        ]
        my_holder = "MR. CARROT의 상점에 오신것을 환영합니다🐰"
        super().__init__(placeholder= my_holder, options=options)
    async def callback(self, interaction: discord.Interaction):
        limit_lvl = 0
        if self.values[0] == "2배 부스트 3시간 ON":
            price = 800
            limit_lvl = 0
            role_id = 1470331131315355760  # 2배 역할 넣기
        elif self.values[0] == "2배 부스트 6시간 ON":
            price = 1500
            limit_lvl = 0
            role_id = 1470717115135819912  # 2배 역할 넣기
        elif self.values[0] == "3배 부스트 24시간 ON":
            price = 5000
            limit_lvl = 0
            role_id = 1470713107029561456  # 3배 역할 넣기
        elif self.values[0] == "내전 작은 경고 차감권":
            price = 40000
            limit_lvl = 60
        elif self.values[0] == "내전 큰 경고 차감권":
            price = 100000
            limit_lvl = 60
        elif self.values[0] == "내전 참전 금지 해제권":
            price = 20000
            limit_lvl = 60
        user_id = interaction.user.id
        
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
            # 유저의 레벨이 충분한지 확인
            if row:
                level, _, _ = calculate_level(row[0])
            else:
                level = 0
            if level < limit_lvl :
                await interaction.response.send_message("❌ 레벨이 부족합니다!", ephemeral=True)
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
                await interaction.response.send_message("구매가 완료되었습니다!", ephemeral=True)
                await db.execute("UPDATE attendance SET experience = experience - ? WHERE user_id = ?", (price, user_id))
                await db.commit()

                if isinstance(interaction.user, discord.Member) and role and price == 800:
                    await interaction.user.add_roles(role)
                    await asyncio.sleep(10800)
                    await interaction.user.remove_roles(role)
                elif isinstance(interaction.user, discord.Member) and role and price == 1500 :
                    await interaction.user.add_roles(role)
                    await asyncio.sleep(21600)
                    await interaction.user.remove_roles(role)
                elif isinstance(interaction.user, discord.Member) and role and price == 5000 :
                    await interaction.user.add_roles(role)
                    await asyncio.sleep(86400)
                    await interaction.user.remove_roles(role)
                else:
                    return
        async def no_callback(interaction: discord.Interaction):
            await interaction.response.send_message("구매가 취소되었습니다.", ephemeral=True)


        yes.callback = yes_callback
        no.callback = no_callback

class DropdownView(discord.ui.View):
    def __init__(self):
        super().__init__()
        self.add_item(Dropdown())

# 명령어 부분
@bot.tree.command(name="상점", description="경험치 상점을 엽니다.")
async def open_shop(interaction: discord.Interaction):
    embed = discord.Embed(title="🏪 당근 포인트 상점", color=discord.Color.gold())
    embed.description = "경험치를 소모하여 특별한 아이템을 구매하세요."
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
            LC = bot.get_channel(lvl_channel)
            if isinstance(LC, TextChannel):
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
    AC = interaction.channel
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

# 내전 기능에 관한 코드
scrim_players = []
scrim_data = {}
scrim_limit = {}
scrim_name = ""

class ScrimView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @ui.button(label="참가하기", style=ButtonStyle.blurple, custom_id="join_scrim")
    async def join_scrim(self, interaction: discord.Interaction, button: ui.Button):
        msg_id = interaction.message.id if interaction.message else None

        if msg_id not in scrim_data:
            await interaction.response.send_message("❌ 종료된 내전이거나 데이터를 찾을 수 없습니다.", ephemeral=True)
            return
        
        players = scrim_data[msg_id]
        limit = scrim_limit[msg_id]

        if interaction.user.id in players:
            await interaction.response.send_message("❌ 이미 참가하셨습니다!", ephemeral=True)
            return

        players.append(interaction.user.id)
        
        if not interaction.message or not interaction.message.embeds:
            await interaction.response.send_message("❌ 오류가 발생했습니다.", ephemeral=True)
            return
        embed = interaction.message.embeds[0]
    
        if embed.description:
            new_desc = embed.description.split("현재 참가자 수: ")[0] + f"현재 참가자 수: {len(players)}/{limit}"
            embed.description = new_desc

        if len(players) >= limit:
            for item in self.children[:]:
                if isinstance(item, ui.Button) and item.custom_id in ["join_scrim", "leave_scrim"]:
                    self.remove_item(item)
            close_button = ui.Button(label="모집 마감", style=ButtonStyle.gray, disabled=True)
            self.add_item(close_button)

            embed.title = "🚫 모집이 마감되었습니다."

        await interaction.response.edit_message(embed=embed, view=self)
    
        await interaction.followup.send(f"✅ {interaction.user.mention}님, 내전 참가하셨습니다!", ephemeral=True)

    @ui.button(label="취소하기", style=ButtonStyle.red, custom_id="leave_scrim")
    async def cancel_scrim(self, interaction: discord.Interaction, button: ui.Button):

        msg_id = interaction.message.id if interaction.message else None
        if msg_id not in scrim_data: return

        players = scrim_data[msg_id]
        limit = scrim_limit[msg_id]
        if interaction.user.id not in players:
            await interaction.response.send_message("❌ 참가하지 않으셨습니다!", ephemeral=True)
            return
        
        players.remove(interaction.user.id)
        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            if embed.description:
                new_desc = embed.description.split("현재 참가자 수: ")[0] + f" 현재 참가자 수: {len(players)}/{limit}"
                embed.description = new_desc
                await interaction.message.edit(embed=embed, view=self)

        await interaction.followup.send(f"✅ {interaction.user.mention}님, 내전 참가 취소하셨습니다!", ephemeral=True)

    @ui.button(label="명단 확인", style=ButtonStyle.green, custom_id="view_players")
    async def view_players(self, interaction: discord.Interaction, button: ui.Button):
        msg_id = interaction.message.id if interaction.message else None
        if msg_id not in scrim_data:
            return await interaction.response.send_message("❌ 종료된 내전이거나 데이터를 찾을 수 없습니다.", ephemeral=True)
        
        players = scrim_data[msg_id]
        if not players:
            return await interaction.response.send_message("현재 참가자가 없습니다.", ephemeral=True)
        
        mentions = "\n".join([f"- <@{p_id}>" for p_id in players])

        embed = discord.Embed(title="📋 참가자 명단", description=mentions, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="내전생성", description="내전을 생성합니다. 참가자들은 /내전참가 명령어로 등록할 수 있습니다.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(prize=[
    app_commands.Choice(name="황금 당근 티켓", value="황금 당근 티켓"),
    app_commands.Choice(name="은 당근 티켓", value="은 당근 티켓"),
    app_commands.Choice(name="기프티콘", value="기프티콘"),
])
@app_commands.choices(games=[
    app_commands.Choice(name="협곡", value="협곡"),
    app_commands.Choice(name="발로란트", value="발로란트"),
    app_commands.Choice(name="아수라장", value="아수라장"),
    app_commands.Choice(name="롤토체스", value="롤토체스"),
])
async def create_scrim(interaction: discord.Interaction, games: app_commands.Choice[str], prize: app_commands.Choice[str], day: str, time: str):

    global scrim_name
    scrim_name = games.value
    if games.value == "협곡":
        limit = 20
        color = discord.Color.green()
        image_url = "https://e7.pngegg.com/pngimages/752/220/png-clipart-league-of-legends-computer-icons-riot-games-league-of-legends-game-logo-thumbnail.png"
    elif games.value == "발로란트":
        limit = 10
        color = discord.Color.red()
        image_url = "https://i.namu.wiki/i/Rf1dgn0IGRUraSmcIP3QLUSUtD8acVY63qFFPg2sRzC13lDFkdvLxQ4FZ_4TAfHNq-DELZGAIPDLiDyNx3Ojpw.svg"
    elif games.value == "아수라장":
        limit = 10
        color = discord.Color.blue()
        image_url = "https://i.namu.wiki/i/vc84KQM17eS9aUoG5igiGk5cTDVfMJa3n0gs_a8R-qeif0tVD3hKkDgh-iLHH4zBt-o_TT4DEExDA12LU39veA.webp"
    elif games.value == "롤토체스":
        limit = 8
        color = discord.Color.purple()
        image_url = "https://i.namu.wiki/i/mOmhZXL-wsMUG919w1Dwd34dOHqdZZgBXMCsGQOLRP-GyZ3l-vOi1xElWgVZqR1wn12qN60SulfMVMuPZ64GVA.webp"
    embed = discord.Embed(title=f"{games.name} 내전 모집중!", description=f"지금부터 {games.name} 내전을 모집합니다. \n 현재 참가자 수: {len(scrim_players)}/{limit}", color=color)
    embed.add_field(name="참가 방법", value=f"참가 버튼으로 참가할 수 있습니다.", inline=False)
    embed.add_field(name="내전 시간", value=f"{day}일 {time}시에 시작합니다!", inline=False)
    embed.add_field(name="내전 보상", value=f"이번 내전의 보상은 {prize.name}입니다.", inline=False)
    embed.add_field(name="주의 사항", value="내전 참가자들은 내전 시작 10분 전까지 대기실에 모여야합니다.", inline=False)
    embed.set_thumbnail(url=image_url)
    embed.set_footer(text="참가자 수가 최대 인원에 도달하면 취소가 불가능합니다.")
    
    view = ScrimView()
    await interaction.response.send_message(embed=embed, view=view)

    msg = await interaction.original_response()
    scrim_data[msg.id] = []
    scrim_limit[msg.id] = limit

@bot.tree.context_menu(name="내전시작")
async def start_scrim_context(interaction: discord.Interaction, message: discord.Message):
    
    msg_id = message.id

    players = scrim_data[msg_id]
    if not players:
        await interaction.response.send_message("❌ 참가자가 없는 내전은 시작할 수 없습니다.", ephemeral=True)
        return
    if scrim_name == "협곡" :
        s_name ="롤 데이터"
    elif scrim_name == "발로란트" :
        s_name ="발로 데이터"
    else :
        return await interaction.response.send_message("내전이 시작되었습니다.", ephemeral=True)
    s_sheet = doc.worksheet(s_name)
    t_sheet = doc.worksheet("경매장")
    name_list = []

    for p_id in players :
        cell = s_sheet.find(str(p_id))
        if cell :
            row_data = s_sheet.row_values(cell.row)
            name_list.append([row_data[1]])

    if name_list:
        # 데이터가 들어갈 범위 계산 (K열 2행부터 데이터 개수만큼)
        # 예: 10명이면 K2:K11 범위가 됩니다.
        end_row = 1 + len(name_list)
        target_range = f"K2:K{end_row}"
        
        # 4. update 함수를 사용하여 한 번에 전송
        t_sheet.update(range_name=target_range, values=name_list)
    await interaction.response.send_message(f"✅ {scrim_name} 경매 준비 완료! 명단이 업데이트되었습니다.", ephemeral=True)

#내전 승패 기록 기능 추가


# 사람들 ID 얻어내기

@bot.tree.command(name="정보얻기", description="서버원의 사용자 id를 가져옵니다.")
@app_commands.checks.has_permissions(administrator=True)
async def info_take(interaction: discord.Interaction) :
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    if not guild :
        await interaction.response.send_message("서버에서 사용가능")
        return
    try :
        member_data = [[ member.display_name, str(member.id)] for member in guild.members if not member.bot]
        target_sheet = doc.worksheet("서버 전체 데이터")
        end_row = 1 + len(member_data)
        target_range = f"E2:F{end_row}"
        target_sheet.update(range_name=target_range, values=member_data)
    except Exception as e:
        await interaction.followup.send(f"오류 {e}")



# 봇 실행

TOKEN = os.getenv('DISCORD_TOKEN')
if TOKEN:
    bot.run(TOKEN)
else:    print("❌ DISCORD_TOKEN이 설정되지 않았습니다. .env 파일을 확인해주세요.")