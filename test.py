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

load_dotenv()

# --- [설정 및 상수] ---
DB_NAME = "attendance.db"
ATTENDANCE_CHANNEL_ID = 1383387649917718610
GUILD_ID = 1383387649464729600
EX_channel = ["<CHANNEL_ID>"]
MULTI_ROLE = {
    1479067128366764157: 2,  # 2배 역할 ID
    1479067238324768939: 3,  # 3배 역할 ID
}

# --- [DB 관리 클래스] ---
class Database:
    @staticmethod
    async def execute(query, params=()):
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute(query, params) as cursor:
                result = await cursor.fetchone()
                await db.commit()
                return result

    @staticmethod
    async def update_exp(user_id, amount, nickname=None):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("""
                INSERT INTO attendance (user_id, nickname, experience) 
                VALUES (?, ?, ?) 
                ON CONFLICT(user_id) DO UPDATE SET 
                experience = experience + ?, nickname = COALESCE(?, nickname)
            """, (user_id, nickname, amount, amount, nickname))
            await db.commit()

# --- [공통 유틸리티 함수] ---
def get_user_multiplier(member: discord.Member):
    multiplier = 1.0
    for role in member.roles:
        if role.id in MULTI_ROLE:
            multiplier = max(multiplier, MULTI_ROLE[role.id])
    return multiplier

def calculate_level(exp):
    level = 1
    remaining_exp = exp
    while True:
        next_level_exp = int(((10 * level) ** 2 - 5) / 2 + 1)
        if remaining_exp >= next_level_exp:
            level += 1
            remaining_exp -= next_level_exp
        else: break
    return level, remaining_exp, next_level_exp

# --- [상점 UI 관련] ---
class ConfirmPurchase(ui.View):
    def __init__(self, item_name, price, role_id, user_id):
        super().__init__(timeout=60)
        self.item_name = item_name
        self.price = price
        self.role_id = role_id
        self.user_id = user_id

    @ui.button(label="구매", style=ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT experience FROM attendance WHERE user_id = ?", (self.user_id,)) as cursor:
                row = await cursor.fetchone()
                if not row or row[0] < self.price:
                    return await interaction.response.edit_message(content="❌ 경험치가 부족합니다.", view=None)

            await db.execute("UPDATE attendance SET experience = experience - ? WHERE user_id = ?", (self.price, self.user_id))
            await db.commit()

        await interaction.response.edit_message(content=f"✅ {self.item_name} 구매 완료!", view=None)
        
        if self.role_id and isinstance(interaction.user, discord.Member) and interaction.guild:
            role = interaction.guild.get_role(self.role_id)
            if role:
                await interaction.user.add_roles(role)
                await asyncio.sleep(7200) # 2시간 뒤 제거
                await interaction.user.remove_roles(role)

    @ui.button(label="취소", style=ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="구매가 취소되었습니다.", view=None)

class ShopDropdown(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="2배", description="5000 EXP / 2시간", emoji="🎃", value="5000:1479067128366764157:2배"),
            discord.SelectOption(label="3배", description="10000 EXP / 2시간", emoji="💊", value="10000:1479067238324768939:3배"),
            discord.SelectOption(label="차감권", description="15000 EXP", emoji="😡", value="15000:1479067275792224439:차감권"),
        ]
        super().__init__(placeholder="아이템을 선택하세요", options=options)

    async def callback(self, interaction: discord.Interaction):
        price, role_id, name = self.values[0].split(":")
        view = ConfirmPurchase(name, int(price), int(role_id), interaction.user.id)
        await interaction.response.send_message(f"정말로 **{name}**을(를) {price} EXP에 구매하시겠습니까?", view=view, ephemeral=True)

# --- [봇 메인 클래스] ---
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix='!', intents=discord.Intents.all())
        self.voice_users = set()
        self.chat_cooldown = {}

    async def setup_hook(self):
        await self.tree.sync()
        self.give_voice_exp.start()
        self.monthly_reset_loop.start()

    async def on_ready(self):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.executescript('''
                CREATE TABLE IF NOT EXISTS attendance (
                    user_id INTEGER PRIMARY KEY, nickname TEXT, last_attendance TEXT,
                    streak INTEGER DEFAULT 0, total_count INTEGER DEFAULT 0, experience INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS monthly_stats (
                    user_id INTEGER, month TEXT, count INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, month)
                );
            ''')
        print(f'Logged in as {self.user}')

    # [이벤트: 채팅 경험치]
    async def on_message(self, message):
        if message.author.bot: return
        
        user_id = message.author.id
        now = datetime.now()
        
        # 쿨다운 체크 (1분)
        if user_id in self.chat_cooldown and (now - self.chat_cooldown[user_id]).total_seconds() < 60:
            return await self.process_commands(message)

        self.chat_cooldown[user_id] = now
        mult = get_user_multiplier(message.author)
        await Database.update_exp(user_id, int(20 * mult), message.author.display_name)
        await self.process_commands(message)

    # [이벤트: 음성 경험치 트래킹]
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        if after.channel is None or after.channel.id in EX_channel: self.voice_users.discard(member.id)
        elif after.channel is not None and after.channel.id not in EX_channel: self.voice_users.add(member.id)

    # [태스크: 음성 경험치 지급]
    @tasks.loop(seconds=300)
    async def give_voice_exp(self):
        guild = self.get_guild(GUILD_ID)
        if not guild or not self.voice_users: return
        for uid in list(self.voice_users):
            m = guild.get_member(uid)
            if m and m.voice and m.voice.channel and m.voice.channel.id not in EX_channel:
                await Database.update_exp(uid, int(30 * get_user_multiplier(m)))
            else:   self.voice_users.discard(uid)

    # [태스크: 매월 초기화]
    @tasks.loop(hours=24)
    async def monthly_reset_loop(self):
        if datetime.now().day == 1:
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("UPDATE attendance SET streak = 0, total_count = 0, last_attendance = NULL")
                await db.commit()

bot = MyBot()

# --- [슬래시 명령어] ---
@bot.tree.command(name="상점", description="경험치 상점 오픈")
async def open_shop(interaction: discord.Interaction):
    view = ui.View(); view.add_item(ShopDropdown())
    embed = discord.Embed(title="🏪 경험치 상점", description="아이템을 선택하세요.", color=0xFFD700)
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="출석", description="오늘의 출석체크")
async def attendance(interaction: discord.Interaction):
    await interaction.response.defer()
    today = date.today().isoformat()
    uid = interaction.user.id
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT last_attendance, streak, total_count, experience FROM attendance WHERE user_id = ?", (uid,)) as cursor:
            row = await cursor.fetchone()
        
        if row and row[0] == today:
            return await interaction.followup.send("⚠️ 이미 오늘 출석하셨습니다.")

        # 연속 출석 계산
        new_streak = (row[1] + 1) if row and row[0] and (date.today() - date.fromisoformat(row[0])).days == 1 else 1
        new_total = (row[2] + 1) if row else 1
        if isinstance(interaction.user, discord.Member):
            gain = int(300 * get_user_multiplier(interaction.user))
        else:
            gain = 300

        await db.execute("""
            INSERT INTO attendance (user_id, nickname, last_attendance, streak, total_count, experience)
            VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET
            last_attendance=?, streak=?, total_count=?, experience=experience+?
        """, (uid, interaction.user.display_name, today, new_streak, new_total, gain, today, new_streak, new_total, gain))
        
        # 월별 통계 업데이트
        await db.execute("""
            INSERT INTO monthly_stats (user_id, month, count) VALUES (?, ?, 1)
            ON CONFLICT(user_id, month) DO UPDATE SET count = count + 1
        """, (uid, date.today().strftime("%Y-%m")))
        await db.commit()

    embed = discord.Embed(title="✅ 출석 완료", description=f"보상: {gain} EXP", color=discord.Color.blue())
    embed.add_field(name="연속/누적", value=f"🔥 {new_streak}일 / 📊 {new_total}회")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="내정보", description="정보 확인")
async def my_info(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    target = user or interaction.user
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT experience, streak, total_count FROM attendance WHERE user_id = ?", (target.id,)) as cursor:
            row = await cursor.fetchone()
    
    if not row: return await interaction.response.send_message("기록이 없습니다.")
    
    lvl, curr, mx = calculate_level(row[0])
    embed = discord.Embed(title=f"📊 {target.display_name}님의 정보", color=0x3498db)
    embed.add_field(name="레벨", value=f"Lv.{lvl} ({curr}/{mx})")
    embed.add_field(name="출석", value=f"🔥 {row[1]}일 / 📊 {row[2]}회")
    await interaction.response.send_message(embed=embed)

# 봇 실행
TOKEN = os.getenv('DISCORD_TOKEN')
if TOKEN is not None:
    bot.run(TOKEN)
else:
    print("Error: DISCORD_BOT_TOKEN is not set in the environment variables.")