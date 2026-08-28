import asyncio
import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
import psycopg2
from twikit import Client

# .envファイルから環境変数を読み込む
load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# ボットの初期化
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
x_client = Client("ja")


# データベースの初期テーブル作成
def init_db():
  conn = psycopg2.connect(DATABASE_URL)
  cur = conn.cursor()
  cur.execute("""
        CREATE TABLE IF NOT EXISTS follows (
            id SERIAL PRIMARY KEY,
            channel_id BIGINT,
            target_username TEXT,
            last_tweet_id TEXT
        )
    """)
  conn.commit()
  cur.close()
  conn.close()


@bot.event
async def on_ready():
  init_db()
  print(f"Logged in as {bot.user}")
  try:
    synced = await bot.tree.sync()
    print(f"Synced {len(synced)} command(s)")
  except Exception as e:
    print(e)

  check_tweets_loop.start()


# /follow コマンドの定義
@bot.tree.command(
    name="follow", description="指定したXアカウントの監視を登録します"
)
@app_commands.describe(
    username="監視したいXのユーザー名（@なし）",
    channel="通知を送るチャンネル",
)
async def follow(
    interaction: discord.Interaction, username: str, channel: discord.TextChannel
):
  conn = psycopg2.connect(DATABASE_URL)
  cur = conn.cursor()

  cur.execute(
      "INSERT INTO follows (channel_id, target_username, last_tweet_id) VALUES"
      " (%s, %s, %s)",
      (channel.id, username, ""),
  )
  conn.commit()
  cur.close()
  conn.close()

  await interaction.response.send_message(
      f"@{username} の監視を {channel.mention} に設定しました！", ephemeral=True
  )


# 3分おきに全データをチェックするバックグラウンドタスク
@tasks.loop(minutes=3)
async def check_tweets_loop():
  conn = psycopg2.connect(DATABASE_URL)
  cur = conn.cursor()
  cur.execute("SELECT id, channel_id, target_username, last_tweet_id FROM follows")
  rows = cur.fetchall()

  for row in rows:
    db_id, channel_id, username, last_tweet_id = row
    try:
      user = await x_client.get_user_by_screen_name(username)
      tweets = await user.get_tweets("tweets", count=1)

      if not tweets:
        continue

      latest_tweet = tweets[0]
      latest_id = latest_tweet.id
      tweet_url = f"https://twitter.com/{username}/status/{latest_id}"

      if str(latest_id) != str(last_tweet_id):
        if last_tweet_id != "":
          channel = bot.get_channel(channel_id)
          if channel:
            # URLだけを送信
            await channel.send(tweet_url)

        cur.execute(
            "UPDATE follows SET last_tweet_id = %s WHERE id = %s",
            (str(latest_id), db_id),
        )
        conn.commit()

    except Exception as e:
      print(f"Error checking @{username}: {e}")

  cur.close()
  conn.close()


bot.run(TOKEN)