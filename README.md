# Discord 音楽BOT (Python)

discord.py + yt-dlp で作る音楽BOTです。YouTubeのURL再生はもちろん、キーワード検索でも再生できます。

## 機能

- `!play <URL / キーワード>` — 再生 / キューに追加
- `!pause` / `!resume` — 一時停止 / 再開
- `!skip` — 次の曲へ
- `!stop` — 停止＆キューをクリア
- `!queue` — キュー表示
- `!nowplaying` — 再生中の曲
- `!loop` — ループON/OFF
- `!volume <0-200>` — 音量変更
- `!remove <番号>` — キューから削除
- `!join` / `!leave` — ボイスチャンネル参加 / 退出

サーバー（ギルド）ごとに独立したキューを持ちます。

## 必要なもの

1. Python 3.10以上
2. FFmpeg（必須）
   - Windows: https://ffmpeg.org/download.html からDLしてPATHを通す
   - Mac: `brew install ffmpeg`
   - Ubuntu/Debian: `sudo apt install ffmpeg`
3. Discord BOTトークン

## セットアップ

### 1. Discord BOTを作成

1. [Discord Developer Portal](https://discord.com/developers/applications) → New Application
2. 左メニュー「Bot」→ Reset Token でトークンを取得
3. Bot設定で以下をONにする：
   - `MESSAGE CONTENT INTENT` （必須）
   - Privileged Gateway Intents の Message Content
4. 左メニュー「OAuth2 → URL Generator」
   - Scopes: `bot`
   - Bot Permissions: `Connect`, `Speak`, `Send Messages`, `Embed Links`, `Read Message History`, `Use Voice Activity`
   - 生成されたURLを開いて、自分のサーバーにBOTを招待

### 2. ファイル準備

```bash
cd discord-music-bot
cp .env.example .env
# .env を開いて DISCORD_TOKEN= にトークンを貼る
```

### 3. インストール＆起動

```bash
pip install -r requirements.txt
python bot.py
```

`ログイン完了: ...` と表示されれば成功です。

## 使い方

Discordでボイスチャンネルに入った状態で：

```
!play https://www.youtube.com/watch?v=xxxx
!play 米津玄師 Lemon
!queue
!skip
```

## 注意点

- YouTubeの利用規約を確認の上、個人利用 / 自分のサーバー内での利用にとどめてください。
- 音が出ない場合：FFmpegがPATHに入っているか、`ffmpeg -version` で確認してください。
- `MESSAGE CONTENT INTENT` がOFFだとコマンドに反応しません。

## ファイル構成

- `bot.py` — BOT本体
- `requirements.txt` — 必要なライブラリ
- `.env.example` — 環境変数のサンプル（コピーして `.env` にする）
