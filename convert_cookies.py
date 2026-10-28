"""
cookies.txt -> FadeHost用 Base64文字列 変換ツール（手元のPCで実行するもの。BOTではありません）

使い方:
    1. このファイルと cookies.txt を同じフォルダに置く
    2. python convert_cookies.py
    3. cookies_b64.txt ができる。その中身を全てコピーして
       FadeHostの環境変数 YT_COOKIES_B64 に貼る

※ cookies_b64.txt もログイン情報そのものです。GitHubに上げない・他人に送らないこと。
※ 中身は表示しません（件数などの確認情報だけ表示します）。
"""
import base64
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies_b64.txt")


def main():
    if not os.path.exists(SRC):
        print("❌ cookies.txt が見つかりません。このファイルと同じフォルダに置いてください。")
        sys.exit(1)

    with open(SRC, "rb") as f:
        raw = f.read()

    text = raw.decode("utf-8", errors="ignore")
    if text.lstrip().startswith("{") or text.lstrip().startswith("["):
        print("❌ JSON形式です。Netscape形式で書き出してください。")
        print("   Chrome拡張『Get cookies.txt LOCALLY』で youtube.com を開いた状態でExportしてください。")
        sys.exit(1)

    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    yt = sum(1 for ln in lines if "youtube.com" in ln)
    gg = sum(1 for ln in lines if "google.com" in ln)
    print(f"cookies.txt: {len(raw)} bytes / YouTube {yt}件 / Google {gg}件")
    if yt == 0:
        print("❌ YouTubeのクッキーがありません。YouTubeにログインした状態で書き出してください。")
        sys.exit(1)
    if not any(len(ln.split("\t")) >= 7 for ln in lines if "youtube.com" in ln):
        print("❌ タブ区切りのNetscape形式ではないようです。")
        sys.exit(1)

    b64 = base64.b64encode(raw).decode("ascii")  # 1行、改行なし
    with open(DEST, "w", encoding="ascii") as f:
        f.write(b64)

    print(f"✅ 変換完了: cookies_b64.txt を作成しました（{len(b64)}文字）")
    print("   中身を全てコピーして、FadeHostの環境変数 YT_COOKIES_B64 に貼ってください。")
    print("⚠️ cookies_b64.txt はGitHubに上げないでください。.gitignore に入れてあります。")


if __name__ == "__main__":
    main()
