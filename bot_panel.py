"""
Discord 音楽BOT - パネル操作版（Spotifyプレイリスト対応 / FadeHost・クッキー版）
- 曲の追加も含め、すべてパネル（ボタン + モーダル）で操作可能
- !panel または /panel でパネルを召喚したら、あとはコマンド不要
- Spotify単曲 / プレイリスト / アルバムのリンクを貼ると、YouTubeで検索して代替再生
  （プレイリストは最大50曲まで一括追加、非公開プレイリストは取得不可）
- FadeHost用クッキー対応: 環境変数 YT_COOKIES_B64（推奨）/ YTDLP_COOKIEFILE=/data/cookies.txt
  手順は FADEHOST_COOKIES.md を参照。cookies.txt は絶対にGitHubへ上げないこと。

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
import base64
import json
import re
import random
import urllib.request
import urllib.parse
from collections import deque

import discord
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("COMMAND_PREFIX", "!")


# ---------------- YouTube bot判定対策: cookies（FadeHost クッキー版） ----------------
# FadeHostのようなデータセンターIPはYouTubeにbot判定されやすい。
# ログイン済みブラウザから書き出した cookies.txt (Netscape形式) を使う。
# 優先順位:
#   1. 環境変数 YTDLP_COOKIEFILE = cookies.txt のパス（例: /data/cookies.txt）
#   2. 環境変数 YT_COOKIES_B64 = cookies.txt をBase64化した1行文字列【FadeHostではこれが推奨】
#   3. /data/cookies.txt（FadeHostの永続フォルダ）
#   4. bot_panel.py と同じフォルダ / 実行フォルダの cookies.txt（ローカル実行用）
# ※ cookies はログイン情報そのもの。GitHubに上げない・他人に送らないこと。.gitignore済み前提。
# ※ メインのGoogleアカウントではなく、BOT専用のサブアカウント推奨（凍結・セッション無効化リスクあり）
# ※ 書き出し直後はYouTubeタブを閉じない等、セッションを変えると無効化されやすいので注意。
def _validate_cookie_bytes(raw: bytes) -> str | None:
    """cookies.txtの中身チェック。問題があれば理由を返す（値は表示しない）"""
    if not raw:
        return "中身が空です"
    text = raw.decode("utf-8", errors="ignore")
    if text.lstrip().startswith("{") or text.lstrip().startswith("["):
        return "JSON形式になっています。Netscape形式の cookies.txt で書き出してください（拡張機能のExport形式を確認）"
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    youtube_lines = [ln for ln in lines if "youtube.com" in ln or "google.com" in ln]
    if not youtube_lines:
        return "youtube.com / google.com のクッキーが含まれていません。YouTubeにログインした状態で youtube.com を開いて書き出してください"
    # Netscape形式はタブ区切り7列
    if not any(len(ln.split("\t")) >= 7 for ln in youtube_lines):
        return "タブ区切りのNetscape形式ではないようです"
    return None


def _describe_cookie_file(path: str) -> str:
    """ログ用: クッキーの値を見せずに、件数だけを返す"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8", errors="ignore")
        lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        yt = sum(1 for ln in lines if "youtube.com" in ln)
        gg = sum(1 for ln in lines if "google.com" in ln)
        problem = _validate_cookie_bytes(raw)
        if problem:
            return f"{len(raw)} bytes / YouTube {yt}件 / Google {gg}件 / ⚠️ {problem}"
        return f"{len(raw)} bytes / YouTube {yt}件 / Google {gg}件 / 形式OK"
    except Exception as e:
        return f"読み取り失敗: {e}"


def _resolve_cookie_file():
    path_env = os.getenv("YTDLP_COOKIEFILE")
    if path_env:
        if os.path.exists(path_env):
            return path_env
        print(f"⚠️ YTDLP_COOKIEFILE={path_env} が見つかりません")

    b64 = os.getenv("YT_COOKIES_B64")
    if b64:
        # 改行・空白が混じっても動くように除去してからデコードする
        cleaned = "".join(b64.split())
        raw = None
        try:
            raw = base64.b64decode(cleaned, validate=False)
        except Exception as e:
            print(f"⚠️ YT_COOKIES_B64 のデコードに失敗: {e}")
        if raw:
            problem = _validate_cookie_bytes(raw)
            if problem:
                print(f"⚠️ YT_COOKIES_B64 の中身に問題があります: {problem}")
            # FadeHost等はコードフォルダが読み取り専用なので /tmp -> /data の順で書ける場所を探す
            for dest in ("/tmp/youtube_cookies.txt", "/data/youtube_cookies.txt"):
                try:
                    with open(dest, "wb") as f:
                        f.write(raw)
                    return dest
                except Exception:
                    continue
            print("⚠️ YT_COOKIES_B64 を書き込める場所がありませんでした")

    for candidate in (
        "/data/cookies.txt",  # FadeHostの永続フォルダ（ファイルマネージャでアップ可・有料プラン）
        "/data/youtube_cookies.txt",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt"),
        os.path.join(os.getcwd(), "cookies.txt"),
    ):
        if os.path.exists(candidate):
            return candidate
    return None


COOKIE_FILE = _resolve_cookie_file()
if COOKIE_FILE:
    print(f"YouTube cookies: 使用する ({COOKIE_FILE}) / {_describe_cookie_file(COOKIE_FILE)}")
else:
    print("⚠️ YouTube cookies: なし。FadeHostではYouTubeにbot判定される可能性が高いです。")
    print("   FadeHostのパネルで環境変数 YT_COOKIES_B64 を設定してください（手順: FADEHOST_COOKIES.md）")
try:
    print(f"yt-dlp version: {yt_dlp.version.__version__}")
except Exception:
    pass


class YouTubeBotCheckError(Exception):
    """YouTubeの 'Sign in to confirm you're not a bot' でブロックされた"""
    pass


def _is_bot_check_error(e) -> bool:
    s = str(e)
    return "not a bot" in s or "Sign in to confirm" in s


YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
}
if COOKIE_FILE:
    YTDL_OPTIONS["cookiefile"] = COOKIE_FILE
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


# ---------------- DRMサイト対応 ----------------
# Spotify / Apple Music / Amazon Music / Tidal などはDRM保護のため
# yt-dlpで直接再生できない。URLから曲名+アーティスト名を取り出し、
# YouTubeで検索して代替再生する。
DRM_DOMAINS = (
    "open.spotify.com",
    "music.apple.com",
    "music.amazon.",
    "amazon.co.jp/music",
    "tidal.com",
    "music.youtube.com",  # これは再生可なので含めないケースが多いが有料限定曲用に注意
)
# music.youtube.com は通常再生できるので対象から外す
DRM_DOMAINS = tuple(d for d in DRM_DOMAINS if d != "music.youtube.com")


def is_drm_url(url: str) -> bool:
    return any(d in url for d in DRM_DOMAINS) or "spotify.com" in url or "apple.com/music" in url


def _fetch_spotify_oembed(url: str):
    """SpotifyのoEmbed（APIキー不要）から曲名を取得 ※アーティスト名は含まれない"""
    api = "https://open.spotify.com/oembed?url=" + urllib.parse.quote(url, safe="")
    req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as res:
        data = json.loads(res.read().decode("utf-8"))
    title = (data.get("title") or "").strip()
    author = (data.get("author_name") or "").strip()
    if title and author:
        return f"{author} {title}"
    return title or None


def _fetch_spotify_embed(url: str):
    """Spotify埋め込みページのJSONから 曲名+アーティスト名 を取得（APIキー不要・精度が高い）
    対応: /track/, /album/, /episode/ の単体リンク。intl-ja等の言語prefix付きURLも可。
    """
    m = re.search(r"spotify\.com/(?:intl-[a-z\-]+/)?(track|album|episode)/([A-Za-z0-9]+)", url)
    if not m:
        return None
    kind, spotify_id = m.group(1), m.group(2)
    embed_url = f"https://open.spotify.com/embed/{kind}/{spotify_id}"
    req = urllib.request.Request(embed_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as res:
        html = res.read().decode("utf-8", errors="ignore")
    # __NEXT_DATA__ 内の entity から name と artists を取る
    name_match = re.search(r'"entity":\{"type":"(?:track|album|episode)"[^}]*?"name":"([^"]+)"', html)
    if not name_match:
        name_match = re.search(r'"name":"([^"]+)","uri":"spotify:(?:track|album|episode):', html)
    if not name_match:
        return None
    title = name_match.group(1)
    artists = re.findall(r'"artists":\[\{"name":"([^"]+)"', html)
    if not artists:
        artists = re.findall(r'"artistName":"([^"]+)"', html)
    if artists:
        return f"{artists[0]} {title}"
    return title


MAX_PLAYLIST_TRACKS = 50  # Spotifyプレイリスト/アルバムを一度に追加する上限


def is_spotify_collection_url(url: str) -> bool:
    """Spotifyのプレイリスト / アルバムURLかどうか"""
    return bool(re.search(r"spotify\.com/(?:intl-[a-z\-]+/)?(playlist|album)/[A-Za-z0-9]+", url or ""))


def _fetch_spotify_collection(url: str):
    """Spotifyプレイリスト/アルバムの埋め込みページから全曲リストを取得（APIキー不要）
    戻り値: (コレクション名, [検索クエリ, ...])  例: ("Today's Top Hits", ["Tate McRae Housewife", ...])
    埋め込みページの __NEXT_DATA__ -> entity.trackList に title=曲名, subtitle=アーティスト名 が入っている。
    """
    m = re.search(r"spotify\.com/(?:intl-[a-z\-]+/)?(playlist|album)/([A-Za-z0-9]+)", url)
    if not m:
        return None, []
    kind, spotify_id = m.group(1), m.group(2)
    embed_url = f"https://open.spotify.com/embed/{kind}/{spotify_id}"
    req = urllib.request.Request(embed_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as res:
        html = res.read().decode("utf-8", errors="ignore")

    name = None
    queries = []
    m_data = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if m_data:
        try:
            data = json.loads(m_data.group(1))
            entity = data["props"]["pageProps"]["state"]["data"]["entity"]
            name = entity.get("name") or entity.get("title")
            for t in entity.get("trackList", [])[:MAX_PLAYLIST_TRACKS]:
                title = (t.get("title") or "").replace("\xa0", " ").strip()
                artist = (t.get("subtitle") or "").replace("\xa0", " ").strip()
                if title:
                    queries.append(f"{artist} {title}".strip())
        except Exception as e:
            print(f"SpotifyコレクションJSON解析失敗: {e}")

    if not queries:
        # フォールバック: 正規表現で title/subtitle のペアを拾う（アルバム等で構造が違う場合）
        pairs = re.findall(r'"title":"([^"]+)","subtitle":"([^"]+)"', html)
        for title, artist in pairs[:MAX_PLAYLIST_TRACKS]:
            queries.append(f"{artist} {title}".strip())
        if not name:
            nm = re.search(r'"entity":\{"type":"(?:playlist|album)","name":"([^"]+)"', html)
            if nm:
                name = nm.group(1)
    return name, queries


async def fetch_spotify_collection(url: str):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _fetch_spotify_collection(url))


async def load_spotify_collection(player, url: str, user, notify=None):
    """プレイリスト/アルバムをYouTube検索しながら1曲ずつキューへ追加する（バックグラウンド用）
    notify: async callable(str) 進捗通知用（省略可）
    戻り値: (コレクション名, 追加できた曲数, 全曲数)
    """
    name, queries = await fetch_spotify_collection(url)
    if not queries:
        if notify:
            await notify("❌ プレイリストの曲一覧が取得できませんでした。公開プレイリストか確認してください（非公開は取得できません）。")
        return name, 0, 0

    if notify:
        await notify(
            f"📋 Spotifyプレイリスト **{name or '（無題）'}** から {len(queries)}曲を読み込みます。\n"
            f"1曲ずつYouTubeで検索して追加するので、全曲入るまで少し時間がかかります（目安: 1曲あたり1〜3秒）。最初の曲はすぐ再生が始まります。"
        )

    added = 0
    for q in queries:
        try:
            results = await extract_info(q, search=False)
            if not results:
                continue
            # add_and_play は再生中でなければ自動で再生開始する
            await player.add_and_play(results[0], user)
            added += 1
        except Exception as e:
            print(f"プレイリスト曲の追加失敗 ({q}): {e}")
            continue

    if notify:
        await notify(f"✅ プレイリスト **{name or ''}** の読み込み完了: {added}/{len(queries)}曲をキューに追加しました。")
    await player.refresh_panel()
    return name, added, len(queries)


def _query_from_url_slug(url: str):
    """Apple Music等のURL末尾スラッグから検索語を推測するフォールバック"""
    path = urllib.parse.urlparse(url).path
    parts = [p for p in path.split("/") if p and not p.isdigit() and p not in ("jp", "us", "song", "album", "playlist", "track")]
    if not parts:
        return None
    slug = parts[-1]
    slug = re.sub(r"[-_]+", " ", slug)
    slug = re.sub(r"\d+$", "", slug).strip()
    return slug or None


async def resolve_drm_query(url: str):
    """DRMサイトのURL -> YouTube検索用クエリ。取れなければNone"""
    loop = asyncio.get_event_loop()

    def _resolve():
        if "spotify.com" in url:
            # 1) 埋め込みページ: 曲名+アーティスト名まで取れる（最優先）
            try:
                q = _fetch_spotify_embed(url)
                if q:
                    return q
            except Exception as e:
                print(f"Spotify embed取得失敗: {e}")
            # 2) oEmbed: 曲名のみのフォールバック
            try:
                q = _fetch_spotify_oembed(url)
                if q:
                    return q
            except Exception as e:
                print(f"Spotify oEmbed失敗: {e}")
        return _query_from_url_slug(url)

    return await loop.run_in_executor(None, _resolve)


async def extract_from_drm_url(url: str):
    """DRM URLをYouTube検索に変換して曲情報を返す。(info, 検索クエリ)"""
    search_query = await resolve_drm_query(url)
    if not search_query:
        return None, None
    results = await extract_info(search_query, search=False)
    if not results:
        return None, search_query
    return results[0], search_query


# ---------------- yt-dlp ----------------
def _is_format_error(e) -> bool:
    return "Requested format is not available" in str(e)


def _is_playable_result(results) -> bool:
    """storyboard(mhtml)だけ掴んでいないか、URLがあるかを確認"""
    if not results:
        return False
    url = results[0].get("url") or ""
    fmt = str(results[0].get("format_id") or "")
    if not url or url.startswith("https://i.ytimg.com/"):
        return False
    if fmt.startswith("sb"):  # storyboard
        return False
    return True


def _pick_best_format(formats):
    """rawなformats一覧から、FFmpegで再生できるものを自分で選ぶ（フォーマット選択エラー時の最終手段用）
    優先度: 音声のみ(https) > 音声のみ(その他) > 音声+映像(360p付近) > 音声を含むもの
    storyboard(mhtml)やURL無し(SABR形式など)は除外する。
    """
    candidates = []
    for f in formats or []:
        if not f.get("url"):
            continue  # URLが無い形式は直接再生できない
        if f.get("ext") == "mhtml" or f.get("protocol") == "mhtml":
            continue  # storyboard
        if str(f.get("format_id") or "").startswith("sb"):
            continue
        candidates.append(f)
    if not candidates:
        return None

    def is_audio_only(f):
        return f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")

    def is_combined(f):
        return f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")

    audio_only = [f for f in candidates if is_audio_only(f)]
    if audio_only:
        audio_only.sort(key=lambda f: (0 if str(f.get("protocol", "")).startswith("http") and f.get("protocol") != "m3u8" else 1,
                                       -(f.get("abr") or f.get("tbr") or 0)))
        return audio_only[0]

    combined = [f for f in candidates if is_combined(f)]
    if combined:
        combined.sort(key=lambda f: (abs((f.get("height") or 360) - 360), -(f.get("abr") or 0)))
        return combined[0]

    with_audio = [f for f in candidates if f.get("acodec") not in (None, "none")]
    if with_audio:
        with_audio.sort(key=lambda f: -(f.get("abr") or f.get("tbr") or 0))
        return with_audio[0]
    return None


def _summarize_formats(formats, limit=30):
    parts = []
    for f in (formats or [])[:limit]:
        parts.append(f"{f.get('format_id')}({f.get('ext')}/{f.get('protocol')}/a={f.get('acodec')}/v={f.get('vcodec')}/url={'Y' if f.get('url') else 'N'})")
    return ", ".join(parts) if parts else "(formats自体が空)"


async def extract_info(query: str, search: bool = False):
    """曲情報を取得する。
    YouTubeはクライアントごとに返るフォーマットが違い、
    - web単体だとbot判定
    - ios / web_embedded単体だと音声フォーマットが無く 'Requested format is not available'
    - tv_embedded / visionos だと完全な音声フォーマット(251等)が返る
    といったことが起きる（実測済み）。そのため失敗時は
    フォーマット条件を緩める + クライアントを変える、を順に試す。
    """
    loop = asyncio.get_event_loop()
    base_opts = YTDL_SEARCH_OPTIONS if search else YTDL_OPTIONS
    ALT_FORMAT = "ba*/ba/b*/b/worst"

    def _extract_with(opts):
        ydl = yt_dlp.YoutubeDL(opts)
        data = ydl.extract_info(query, download=False)
        entries = data["entries"] if "entries" in data else [data]
        results = []
        for d in entries:
            if d is None:
                continue
            results.append({
                "url": d.get("url"),
                "title": d.get("title", "Unknown"),
                "webpage_url": d.get("webpage_url", query),
                "duration": d.get("duration"),
                "thumbnail": d.get("thumbnail"),
                "format_id": d.get("format_id"),
            })
        return results

    def _make_opts(fmt=None, clients=None):
        opts = dict(base_opts)
        if fmt:
            opts["format"] = fmt
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": list(clients)}}
        return opts

    # 試行パターン: (説明, オプション)
    attempts = [
        ("通常", _make_opts()),
        ("フォーマット緩和", _make_opts(fmt=ALT_FORMAT)),
        ("tv_embedded", _make_opts(fmt=ALT_FORMAT, clients=["tv_embedded"])),
        ("visionos+tv_embedded", _make_opts(fmt=ALT_FORMAT, clients=["visionos", "tv_embedded"])),
        ("web+cookies", _make_opts(fmt=ALT_FORMAT, clients=["web"])) if COOKIE_FILE else ("android_vr(最終手段・360p音声付き)", _make_opts(fmt="ba/b/worst", clients=["android_vr"])),
        ("android_vr(最終手段・360p音声付き)", _make_opts(fmt="ba/b/worst", clients=["android_vr"])),
    ]

    def _extract_raw(clients=None):
        """format選択をyt-dlpに任せず、process=Falseで全formatsを取得して自分で選ぶ最終手段"""
        opts = dict(base_opts)
        opts.pop("format", None)
        opts.pop("extract_flat", None)
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": list(clients)}}
        ydl = yt_dlp.YoutubeDL(opts)

        is_url = isinstance(query, str) and query.startswith("http")
        if is_url:
            data = ydl.extract_info(query, download=False, process=False)
            if data and "entries" in data and data["entries"]:
                data = data["entries"][0]
        else:
            # 検索語の場合は、まずflat検索で動画URLを特定してからraw取得する
            flat_opts = dict(opts)
            flat_opts["extract_flat"] = True
            flat_opts["default_search"] = "ytsearch1"
            with yt_dlp.YoutubeDL(flat_opts) as flat_ydl:
                search_data = flat_ydl.extract_info(query, download=False)
            entries = (search_data or {}).get("entries") or []
            if not entries:
                raise Exception("検索結果がありませんでした（raw取得）")
            first = entries[0]
            target = first.get("webpage_url") or first.get("url")
            if target and not str(target).startswith("http"):
                target = f"https://www.youtube.com/watch?v={first.get('id') or target}"
            data = ydl.extract_info(target, download=False, process=False)

        if not data:
            raise Exception("raw取得でデータが空でした")
        formats = data.get("formats") or []
        summary = _summarize_formats(formats)
        print(f"raw取得 formats[{len(formats)}]: {summary}")
        chosen = _pick_best_format(formats)
        if not chosen:
            raise Exception(f"再生可能なURL付きフォーマットがありませんでした。formats: {summary}")
        return [{
            "url": chosen["url"],
            "title": data.get("title", "Unknown"),
            "webpage_url": data.get("webpage_url", query),
            "duration": data.get("duration"),
            "thumbnail": data.get("thumbnail"),
            "format_id": chosen.get("format_id"),
        }]

    def _extract():
        last_error = None
        saw_bot_check = False
        for label, opts in attempts:
            try:
                results = _extract_with(opts)
                if _is_playable_result(results):
                    if label != "通常":
                        print(f"抽出成功（{label}）: {query} -> format {results[0].get('format_id')}")
                    return results
                # 再生できない形式しかなかった場合は次の試行へ
                last_error = Exception(f"再生可能なフォーマットがありませんでした（{label}）")
                print(f"抽出リトライ: {label} では再生可能な形式が無し -> 次を試します")
            except Exception as e:
                last_error = e
                if _is_bot_check_error(e):
                    saw_bot_check = True
                if _is_bot_check_error(e) or _is_format_error(e):
                    print(f"抽出リトライ: {label} 失敗 ({type(e).__name__}: {str(e)[:120]}) -> 次を試します")
                    continue
                raise
        # 最終手段: format選択を外してrawで全formatsから自分で選ぶ
        raw_attempts = [(None, "raw(通常)"), (["tv_embedded"], "raw(tv_embedded)"),
                        (["android_vr"], "raw(android_vr)"),
                        (["web"], "raw(web)") if COOKIE_FILE else (["visionos", "tv_embedded"], "raw(visionos+tv_embedded)")]
        for clients, label in raw_attempts:
            try:
                results = _extract_raw(clients)
                if _is_playable_result(results):
                    print(f"抽出成功（{label}）: {query} -> format {results[0].get('format_id')}")
                    return results
            except Exception as e:
                last_error = e
                if _is_bot_check_error(e):
                    saw_bot_check = True
                print(f"抽出リトライ: {label} 失敗 ({type(e).__name__}: {str(e)[:200]}) -> 次を試します")
                continue
        # 全パターン失敗
        if saw_bot_check:
            raise YouTubeBotCheckError(
                "YouTubeにbot判定されて再生できませんでした。"
                + (
                    "cookiesは読み込まれていますが判定を回避できませんでした。cookiesの再書き出し（サブ垢でYouTubeを開いた状態で再Export）か、FadeHostのリージョン変更を試してください。"
                    if COOKIE_FILE else
                    "FadeHostでは cookies が必要です。環境変数 YT_COOKIES_B64 を設定してください（手順: FADEHOST_COOKIES.md）。"
                )
                + f" 元のエラー: {last_error}"
            )
        raise last_error if last_error else Exception("曲情報が取得できませんでした")

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
            if is_url and is_spotify_collection_url(query):
                # Spotify プレイリスト / アルバム: バックグラウンドで全曲追加（最初の曲はすぐ再生される）
                await interaction.followup.send("📋 Spotifyプレイリストとして読み込みを開始します…", ephemeral=True)

                async def _notify(msg):
                    try:
                        await interaction.followup.send(msg, ephemeral=True)
                    except Exception:
                        pass

                asyncio.create_task(load_spotify_collection(self.player, query, interaction.user, notify=_notify))
            elif is_url and is_drm_url(query):
                # DRMサイトは直接再生不可 -> YouTubeで同じ曲を検索して代替再生
                info, search_query = await extract_from_drm_url(query)
                if not info:
                    await interaction.followup.send(
                        "❌ そのリンクはDRM保護されたサービス（Spotify / Apple Music等）のため直接再生できません。\n"
                        "曲名がURLから取得できなかったので、パネルから **曲名＋アーティスト名で検索** して追加してください。",
                        ephemeral=True,
                    )
                    return
                await self.player.add_and_play(info, interaction.user)
                await interaction.followup.send(
                    f"⚠️ そのサービスはDRMで直接再生できないため、YouTubeで検索して代替再生します。\n"
                    f"検索: `{search_query}`\n✅ 追加しました: **{info['title']}**",
                    ephemeral=True,
                )
            elif is_url:
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
            if isinstance(e, YouTubeBotCheckError) or _is_bot_check_error(e):
                await interaction.followup.send(
                    "❌ YouTubeにbot判定されて再生できませんでした。\n"
                    "サーバー側に **cookies.txt** を設置すると解決することが多いです。管理者に確認してください。",
                    ephemeral=True,
                )
            elif "DRM" in str(e):
                await interaction.followup.send(
                    "❌ そのリンクはDRM保護のため直接再生できません。YouTubeのURLか、曲名での検索で追加してください。",
                    ephemeral=True,
                )
            else:
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
    if query.startswith("http") and is_spotify_collection_url(query):
        await ctx.send("📋 Spotifyプレイリストとして読み込みを開始します…")

        async def _notify(msg):
            await ctx.send(msg)

        asyncio.create_task(load_spotify_collection(player, query, ctx.author, notify=_notify))
    elif query.startswith("http") and is_drm_url(query):
        info, search_query = await extract_from_drm_url(query)
        if not info:
            await ctx.send("❌ DRM保護されたサービスのリンクは直接再生できません。曲名で検索してください。")
            return
        await ctx.send(f"⚠️ DRMサイトのためYouTubeで代替再生します（検索: `{search_query}`）")
        await player.add_and_play(info, ctx.author)
    else:
        try:
            results = await extract_info(query, search=False)
        except Exception as e:
            if "DRM" in str(e):
                await ctx.send("❌ DRM保護のため直接再生できません。YouTubeのURLか曲名検索で追加してください。")
                return
            raise
        await player.add_and_play(results[0], ctx.author)
    if not player.panel_message:
        await send_panel(ctx.channel, player)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ .env に DISCORD_TOKEN が設定されていません。")
    bot.run(TOKEN)
