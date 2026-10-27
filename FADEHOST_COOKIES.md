# FadeHost クッキー版 セットアップ手順

対象: FadeHostで動かしているパネル版BOT（`bot_panel.py`）で
`Sign in to confirm you're not a bot` が出る場合の対策です。

FadeHostはGitHubデプロイ＆コードフォルダ読み取り専用なので、
cookies.txt をリポジトリに置く方法は使いません。
**環境変数 `YT_COOKIES_B64` で渡す**のがこの版の方式です。

---

## GitHubに上げるファイル（これだけ）

1. `bot_panel.py` ← クッキー版
2. `requirements.txt`
3. `.gitignore` ← cookies.txt / cookies_b64.txt / .env を誤って上げないため

※ `convert_cookies.py` は手元のPCだけで使う変換ツールです。上げても害はありませんが不要です。
※ `cookies.txt` と `cookies_b64.txt` は **絶対に上げない**（ログイン情報そのものです）。

## 手順

### 1. BOT専用のGoogleアカウントを用意する

メインアカウントは使わないでください。cookies運用には凍結・セッション無効化のリスクがあります。
サブアカウントで十分です。

### 2. cookies.txt を書き出す

1. Chromeに拡張機能 **「Get cookies.txt LOCALLY」** を入れる
2. サブアカウントで **youtube.com にログイン** する
3. youtube.com を開いた状態で拡張機能を開き、Export する → `cookies.txt` がダウンロードされる
4. 形式が **Netscape形式**（タブ区切り）であること。JSONになっていたら拡張機能/形式を間違えています

注意: 書き出した直後にそのブラウザでログアウトしたり、パスワードを変えたりすると cookies が無効になります。

### 3. Base64に変換する

方法A（変換ツール・おすすめ）:
- `convert_cookies.py` と `cookies.txt` を同じフォルダに置いて `python convert_cookies.py`
- `cookies_b64.txt` ができたら、中身を全部コピー

方法B（コマンド）:
- Mac: `base64 -i cookies.txt | tr -d '\n'`
- Windows PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("cookies.txt"))`

### 4. FadeHostに設定する

FadeHostのパネルでBOT/アプリを開く → Environment variables に追加:

| 名前 | 値 |
|---|---|
| `DISCORD_TOKEN` | BOTトークン（設定済みならそのまま） |
| `YT_COOKIES_B64` | 手順3の文字列を貼る |

保存するとアプリが再起動します。

### 5. ログで確認する

FadeHostの Live console で起動直後にこう出れば成功です:

```
YouTube cookies: 使用する (/tmp/youtube_cookies.txt) / xxxx bytes / YouTube xx件 / Google xx件 / 形式OK
yt-dlp version: ...
ログイン完了: ...
```

- `⚠️ YouTube cookies: なし` → 変数名の打ち間違い（`YT_COOKIES_B64`）か、保存後に再起動していない
- `⚠️ ...の中身に問題があります` → ログに理由が出ます（JSON形式、YouTubeのクッキーが無い、など）。手順2からやり直し

その後、DiscordでYouTubeの曲を1曲試してください。

## それでもbot判定される場合

1. cookiesを再書き出し直す（サブ垢でYouTubeを開いた状態で再Export → 手順3〜4を繰り返す）
   cookiesの寿命は短いことがあります。これが一番多い原因です。
2. yt-dlpが古い場合は再デプロイする（FadeHostのデプロイで requirements.txt から再インストールさせる）
3. FadeHostのリージョンを変更する（IPが変わるため通ることがあります）
4. 最終手段: BOTを自宅PCなど家庭用回線で動かす（IP評価の問題のため、一番安定します）

## 有料プランの別方式

ファイルマネージャで `/data/cookies.txt` にアップロードできる場合は、
環境変数 `YTDLP_COOKIEFILE=/data/cookies.txt` でも同じように動きます（B64変換が不要）。
