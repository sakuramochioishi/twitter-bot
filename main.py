import os
import discord
from discord.ext import tasks, commands
from discord import app_commands
import asyncpg
import feedparser
import aiohttp
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

class RSSBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.pool = None

    async def setup_hook(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL)
        
        async with self.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS rss_feeds (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT,
                    channel_id BIGINT,
                    rss_url TEXT,
                    last_entry_link TEXT
                )
            ''')
            
        self.check_rss.start()
        await self.tree.sync()

    async def process_rss_check(self):
        """RSSをチェックして更新があれば通知する共通処理"""
        async with self.pool.acquire() as conn:
            feeds = await conn.fetch('SELECT id, channel_id, rss_url, last_entry_link FROM rss_feeds')
            
            async with aiohttp.ClientSession() as session:
                for feed in feeds:
                    feed_id, channel_id, rss_url, last_link = feed
                    
                    try:
                        async with session.get(rss_url) as resp:
                            xml_data = await resp.text()
                            parsed = feedparser.parse(xml_data)

                        if not parsed.entries:
                            continue

                        latest_entry = parsed.entries[0]
                        
                        # 初回登録時などで last_entry_link が NULL の場合の対策
                        if last_link is None:
                            await conn.execute('UPDATE rss_feeds SET last_entry_link = $1 WHERE id = $2', latest_entry.link, feed_id)
                            continue

                        # 前回保存したリンクと異なる場合（新しい投稿）に通知
                        if latest_entry.link != last_link:
                            channel = self.get_channel(channel_id)
                            if channel:
                                await channel.send(f"新しい投稿がありました！\n{latest_entry.link}")
                            
                            await conn.execute('UPDATE rss_feeds SET last_entry_link = $1 WHERE id = $2', latest_entry.link, feed_id)
                    except Exception as e:
                        print(f"RSS fetch error ({rss_url}): {e}")

    @tasks.loop(minutes=10)
    async def check_rss(self):
        await self.process_rss_check()

    @check_rss.before_loop
    async def before_check_rss(self):
        await self.wait_until_ready()
        # 起動時に一度即座にチェックを実行
        await self.process_rss_check()

bot = RSSBot()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')

@bot.tree.command(name="follow", description="RSSフィードをチャンネルに登録します")
@app_commands.describe(rss_url="RSS.appで生成したURL", channel="通知を送るチャンネル")
async def follow(interaction: discord.Interaction, rss_url: str, channel: discord.TextChannel):
    async with bot.pool.acquire() as conn:
        async with aiohttp.ClientSession() as session:
            async with session.get(rss_url) as resp:
                xml_data = await resp.text()
                parsed = feedparser.parse(xml_data)
                last_link = parsed.entries[0].link if parsed.entries else None

        await conn.execute('''
            INSERT INTO rss_feeds (guild_id, channel_id, rss_url, last_entry_link)
            VALUES ($1, $2, $3, $4)
        ''', interaction.guild_id, channel.id, rss_url, last_link)

    await interaction.response.send_message(f"{channel.mention} に {rss_url} の通知を登録しました！", ephemeral=True)

bot.run(TOKEN)