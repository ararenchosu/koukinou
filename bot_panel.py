"""
Discord 音楽BOT - パネル操作版
- 曲の追加も含め、すべてパネル（ボタン + モーダル）で操作可能
- !panel または /panel でパネルを召喚したら、あとはコマンド不要

操作の流れ:
    1. VCに入る
    2. !panel を送る
    3. [➕ 曲を追加] ボタン -> モーダル（入力フォーム）が開く
       -> URL / キーワードを入力して送信 -> そのままキューに追加＆再生
    4. 一時停止 / スキップ / ループ / シャッフル / 音量なども全てボタン

必要なものは bot.py と同じ: .env の DISCORD_TOKEN, FFmpeg, requirements.txt
起動: python bot_panel.py
"""

import os
import asyncio
import random
from collections import deque

import discord
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("COMMAND_PREFIX", "!")

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
}
YTDL_SEARCH_OPTIONS = {**YTDL_OPTIONS, "default_search": "ytsearch5", "noplaylist": True}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

LOOP_LABELS = {0: "OFF", 1: "1曲リピート", 2: "キュー全体"}


# ---------------- プレイヤー ----------------
class MusicPlayer:
    def __init__(self, bot, guild):
        self.bot = bot
        self.guild = guild
        self.queue = deque()
        self.current = None  # (info, user)
        self.voice_client = None
        self.loop_mode = 0  # 0=OFF, 1=1曲, 2=キュー全体
        self.volume = 1.0
        self.panel_message = None  # discord.Message
        self.panel_channel = None

    async def connect(self, channel):
        if self.voice_client and self.voice_client.is_connected():
            if self.voice_client.channel != channel:
                await self.voice_client.move_to(channel)
        else:
            self.voice_client = await channel.connect()
        return self.voice_client

    def _after_play(self, error=None):
        if error:
            print(f"再生エラー: {error}")
        if self.current:
            if self.loop_mode == 1:
                self.queue.appendleft(self.current)
            elif self.loop_mode == 2:
                self.queue.append(self.current)
        if self.queue:
            asyncio.run_coroutine_threadsafe(self.play_next(), self.bot.loop)
        else:
            self.current = None
            asyncio.run_coroutine_threadsafe(self.refresh_panel(), self.bot.loop)

    async def play_next(self):
        if not self.queue:
            self.current = None
            await self.refresh_panel()
            return
        info, user = self.queue.popleft()
        self.current = (info, user)
        try:
            source = await discord.FFmpegOpusAudio.from_probe(info["url"], **FFMPEG_OPTIONS)
        except Exception:
            source = discord.FFmpegPCMAudio(info["url"], **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=self.volume)
        if self.voice_client and self.voice_client.is_connected():
            self.voice_client.play(source, after=self._after_play)
        await self.refresh_panel()

    async def add_and_play(self, info, user):
        self.queue.append((info, user))
        if self.voice_client and not self.voice_client.is_playing() and not self.voice_client.is_paused():
            await self.play_next()
        else:
            await self.refresh_panel()

    async def refresh_panel(self):
        """パネルのEmbedを現在の状態に更新する"""
        if not self.panel_message:
            return
        try:
            await self.panel_message.edit(embed=build_panel_embed(self), view=MusicPanelView(self.bot, self))
        except (discord.NotFound, discord.HTTPException):
            self.panel_message = None


def build_panel_embed(player: MusicPlayer) -> discord.Embed:
    if player.current:
        info, user = player.current
        embed = discord.Embed(
            title="🎶 再生中",
            description=f"[{info['title']}]({info['webpage_url']})",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="長さ", value=format_duration(info.get("duration")))
        embed.add_field(name="リクエスト", value=user.display_name if user else "-")
        if info.get("thumbnail"):
            embed.set_thumbnail(url=info["thumbnail"])
    else:
        embed = discord.Embed(
            title="🎵 音楽パネル",
            description="再生中の曲はありません。\n下の **➕ 曲を追加** ボタンから追加してください。",
            color=discord.Color.dark_grey(),
        )
    embed.add_field(name="🔁 ループ", value=LOOP_LABELS[player.loop_mode], inline=True)
    embed.add_field(name="🔊 音量", value=f"{int(player.volume * 100)}%", inline=True)
    embed.add_field(name="状態", value="接続中" if player.voice_client and player.voice_client.is_connected() else "未接続", inline=True)

    if player.queue:
        lines = []
        for i, (info, user) in enumerate(list(player.queue)[:8], start=1):
            lines.append(f"{i}. [{info['title']}]({info['webpage_url']}) `[{format_duration(info.get('duration'))}]`")
        if len(player.queue) > 8:
            lines.append(f"...他 {len(player.queue) - 8} 曲")
        embed.add_field(name=f"📋 キュー（{len(player.queue)}曲）", value="\n".join(lines), inline=False)
    embed.set_footer(text="曲の追加も含め、下のボタンだけで操作できます")
    return embed


def format_duration(seconds):
    if not seconds:
        return "不明"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------- yt-dlp ----------------
async def extract_info(query: str, search: bool = False):
    loop = asyncio.get_event_loop()
    opts = YTDL_SEARCH_OPTIONS if search else YTDL_OPTIONS
    ydl = yt_dlp.YoutubeDL(opts)

    def _extract():
        data = ydl.extract_info(query, download=False)
        entries = data["entries"] if "entries" in data else [data]
        results = []
        for d in entries:
            results.append({
                "url": d["url"],
                "title": d.get("title", "Unknown"),
                "webpage_url": d.get("webpage_url", query),
                "duration": d.get("duration"),
                "thumbnail": d.get("thumbnail"),
            })
        return results

    return await loop.run_in_executor(None, _extract)


# ---------------- モーダル: 曲追加フォーム ----------------
class AddSongModal(discord.ui.Modal, title="🎵 曲を追加"):
    query = discord.ui.TextInput(
        label="URL または キーワード",
        placeholder="例: https://youtube.com/...  /  米津玄師 Lemon",
        required=True,
        max_length=300,
    )

    def __init__(self, bot, player: MusicPlayer):
        super().__init__()
        self.bot = bot
        self.player = player

    async def on_submit(self, interaction: discord.Interaction):
        # ユーザーがVCにいなければ接続できない
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ 先にボイスチャンネルに入ってください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.player.connect(interaction.user.voice.channel)

        query = self.query.value.strip()
        is_url = query.startswith("http://") or query.startswith("https://")

        try:
            if is_url:
                results = await extract_info(query, search=False)
                info = results[0]
                await self.player.add_and_play(info, interaction.user)
                await interaction.followup.send(f"✅ 追加しました: **{info['title']}**", ephemeral=True)
            else:
                # キーワードの場合は候補を出して選ばせる
                results = await extract_info(query, search=True)
                if not results:
                    await interaction.followup.send("❌ 見つかりませんでした。", ephemeral=True)
                    return
                view = SearchResultView(self.player, results, interaction.user)
                desc = "\n".join(
                    f"{i}. **{r['title']}** `[{format_duration(r.get('duration'))}]`"
                    for i, r in enumerate(results, start=1)
                )
                await interaction.followup.send(f"🔍 検索結果（プルダウンで選択）\n{desc}", view=view, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 取得に失敗しました: {e}", ephemeral=True)


class SearchResultView(discord.ui.View):
    """検索候補のプルダウン（押した本人だけに見える ephemeral メッセージ用）"""
    def __init__(self, player, results, user):
        super().__init__(timeout=120)
        self.player = player
        self.results = results
        options = [
            discord.SelectOption(label=r["title"][:100], description=format_duration(r.get("duration")), value=str(i))
            for i, r in enumerate(results)
        ]
        select = discord.ui.Select(placeholder="再生する曲を選択", options=options)
        select.callback = self._on_select
        self.add_item(select)
        self.user = user

    async def _on_select(self, interaction: discord.Interaction):
        idx = int(interaction.data["values"][0])
        info = self.results[idx]
        await interaction.response.defer(ephemeral=True)
        await self.player.add_and_play(info, self.user)
        await interaction.followup.send(f"✅ 追加しました: **{info['title']}**", ephemeral=True)
        self.stop()


# ---------------- パネル本体 ----------------
class MusicPanelView(discord.ui.View):
    def __init__(self, bot, player: MusicPlayer):
        super().__init__(timeout=None)  # 永続化: タイムアウトでボタンが死なない
        self.bot = bot
        self.player = player

    async def _check_voice(self, interaction) -> bool:
        if not self.player.voice_client or not self.player.voice_client.is_connected():
            # 接続してなければ、押した人のVCへ自動接続を試みる
            if interaction.user.voice and interaction.user.voice.channel:
                await self.player.connect(interaction.user.voice.channel)
                return True
            await interaction.response.send_message("❌ BOTがVCに接続していません。VCに入ってから曲を追加してください。", ephemeral=True)
            return False
        return True

    # 行1: 曲追加（最重要なので大きく目立たせる）
    @discord.ui.button(label="曲を追加", emoji="➕", style=discord.ButtonStyle.success, row=0, custom_id="panel:add")
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddSongModal(self.bot, self.player))

    @discord.ui.button(label="一時停止 / 再開", emoji="⏯️", style=discord.ButtonStyle.primary, row=0, custom_id="panel:pause")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_voice(interaction):
            return
        vc = self.player.voice_client
        if vc.is_playing():
            vc.pause()
            msg = "⏸️ 一時停止しました"
        elif vc.is_paused():
            vc.resume()
            msg = "▶️ 再開しました"
        else:
            msg = "再生中ではありません"
        await interaction.response.send_message(msg, ephemeral=True)
        await self.player.refresh_panel()

    @discord.ui.button(label="スキップ", emoji="⏭️", style=discord.ButtonStyle.primary, row=0, custom_id="panel:skip")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_voice(interaction):
            return
        vc = self.player.voice_client
        if vc.is_playing() or vc.is_paused():
            # 1曲ループ中でもスキップできるよう一時的に無効化
            was = self.player.loop_mode
            self.player.loop_mode = 0
            vc.stop()
            if was:
                async def restore():
                    await asyncio.sleep(1.2)
                    self.player.loop_mode = was
                asyncio.create_task(restore())
            await interaction.response.send_message("⏭️ スキップしました", ephemeral=True)
        else:
            await interaction.response.send_message("再生中ではありません", ephemeral=True)

    @discord.ui.button(label="停止", emoji="⏹️", style=discord.ButtonStyle.danger, row=0, custom_id="panel:stop")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.queue.clear()
        if self.player.voice_client and (self.player.voice_client.is_playing() or self.player.voice_client.is_paused()):
            # _after_playで次へ進まないよう current を先に消す工夫
            current_backup = self.player.current
            self.player.current = None
            self.player.voice_client.stop()
            # stop()のafterでcurrentが参照されるケースを防ぐため少し待って確定
            await asyncio.sleep(0.2)
            self.player.current = None
        else:
            self.player.current = None
        await interaction.response.send_message("⏹️ 停止してキューをクリアしました", ephemeral=True)
        await self.player.refresh_panel()

    # 行2
    @discord.ui.button(label="ループ切替", emoji="🔁", style=discord.ButtonStyle.secondary, row=1, custom_id="panel:loop")
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.loop_mode = (self.player.loop_mode + 1) % 3
        await interaction.response.send_message(f"🔁 ループ: **{LOOP_LABELS[self.player.loop_mode]}**", ephemeral=True)
        await self.player.refresh_panel()

    @discord.ui.button(label="シャッフル", emoji="🔀", style=discord.ButtonStyle.secondary, row=1, custom_id="panel:shuffle")
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = list(self.player.queue)
        random.shuffle(items)
        self.player.queue = deque(items)
        await interaction.response.send_message("🔀 キューをシャッフルしました", ephemeral=True)
        await self.player.refresh_panel()

    @discord.ui.button(label="音量-", emoji="🔉", style=discord.ButtonStyle.secondary, row=1, custom_id="panel:voldown")
    async def voldown_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._change_volume(interaction, -0.1)

    @discord.ui.button(label="音量+", emoji="🔊", style=discord.ButtonStyle.secondary, row=1, custom_id="panel:volup")
    async def volup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._change_volume(interaction, +0.1)

    async def _change_volume(self, interaction, delta):
        self.player.volume = max(0.0, min(2.0, self.player.volume + delta))
        vc = self.player.voice_client
        if vc and vc.source:
            vc.source.volume = self.player.volume
        await interaction.response.send_message(f"🔊 音量: {int(self.player.volume * 100)}%", ephemeral=True)
        await self.player.refresh_panel()

    @discord.ui.button(label="キュー更新", emoji="🔄", style=discord.ButtonStyle.secondary, row=2, custom_id="panel:refresh")
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.player.refresh_panel()
        await interaction.followup.send("🔄 パネルを更新しました", ephemeral=True)

    @discord.ui.button(label="退出", emoji="👋", style=discord.ButtonStyle.danger, row=2, custom_id="panel:leave")
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.queue.clear()
        self.player.current = None
        if self.player.voice_client and self.player.voice_client.is_connected():
            await self.player.voice_client.disconnect()
        await interaction.response.send_message("👋 切断しました", ephemeral=True)
        await self.player.refresh_panel()


# ---------------- BOT本体 ----------------
class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix=PREFIX, intents=intents, help_command=None)
        self.players = {}

    def get_player(self, guild):
        if guild.id not in self.players:
            self.players[guild.id] = MusicPlayer(self, guild)
        return self.players[guild.id]

    async def on_ready(self):
        print(f"ログイン完了: {self.user} (ID: {self.user.id})")
        try:
            synced = await self.tree.sync()
            print(f"スラッシュコマンド同期: {len(synced)}件")
        except Exception as e:
            print(f"スラッシュ同期エラー: {e}")
        print("------ パネル版BOT起動完了 ------")


bot = MusicBot()


async def send_panel(channel, player):
    """パネルを送信（既にあれば古い方を消して出し直す）"""
    if player.panel_message:
        try:
            await player.panel_message.delete()
        except Exception:
            pass
    msg = await channel.send(embed=build_panel_embed(player), view=MusicPanelView(bot, player))
    player.panel_message = msg
    player.panel_channel = channel
    return msg


@bot.command(name="panel")
async def panel_cmd(ctx):
    player = bot.get_player(ctx.guild)
    await send_panel(ctx.channel, player)


@bot.tree.command(name="panel", description="音楽パネルを表示します")
async def panel_slash(interaction: discord.Interaction):
    player = bot.get_player(interaction.guild)
    await interaction.response.send_message("🎵 パネルを表示しました", ephemeral=True)
    await send_panel(interaction.channel, player)


# 互換用: !play でも追加できるように残しておく（任意）
@bot.command(name="play", aliases=["p"])
async def play_cmd(ctx, *, query: str = None):
    if not query:
        await ctx.send("❌ 使い方: `!play <URL/キーワード>` または パネルの [➕ 曲を追加]")
        return
    if not ctx.author.voice:
        await ctx.send("❌ 先にボイスチャンネルに入ってください。")
        return
    player = bot.get_player(ctx.guild)
    await player.connect(ctx.author.voice.channel)
    results = await extract_info(query, search=False)
    await player.add_and_play(results[0], ctx.author)
    if not player.panel_message:
        await send_panel(ctx.channel, player)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ .env に DISCORD_TOKEN が設定されていません。")
    bot.run(TOKEN)
