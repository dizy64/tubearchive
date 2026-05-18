# TubeArchive TUI ガイド

[README に戻る](../README.ja.md) | [한국어](tui.md) | [English](tui.en.md)

TubeArchive TUI は、映像選択、オプション調整、進捗確認、プロジェクト/統計/履歴/YouTube 管理をターミナル内で行うダッシュボードです。

## 起動

```bash
# 現在のディレクトリから起動
tubearchive tui

# 特定の撮影フォルダを開いて起動
tubearchive tui ~/Videos/Trip2026/

# 開発環境から起動
uv run tubearchive tui ~/Videos/Trip2026/
```

## タブ

| タブ | 用途 |
|------|------|
| Pipeline | ファイル/フォルダ選択、エンコード設定、パイプライン実行 |
| Projects | プロジェクト一覧と日付別ジョブ状態の確認 |
| Stats | 処理統計、デバイス分布、アーカイブ統計の確認 |
| History | トランスコード、結合、アップロード履歴の確認 |
| YouTube | 認証状態、プレイリスト、アップロード設定の確認 |

## ショートカット

| キー | 操作 |
|------|------|
| `1` | Pipeline タブ |
| `2` | Projects タブ |
| `3` | Stats タブ |
| `4` | History タブ |
| `5` | YouTube タブ |
| `r` | 現在のタブを更新 |
| `t` | テーマ切り替え |
| `q` | 終了 |

## 外部音声選択

Pipeline タブの外部音声パネルから、音声ファイルや候補フォルダを現在のオプションに直接反映できます。

| ボタン | 適用オプション | 用途 |
|--------|----------------|------|
| 単一ファイル | `--external-audio ... --external-audio-scope single` | 1つの動画に1つの外部音声を適用 |
| 長時間録音 | `--external-audio ... --external-audio-scope long` | 1つの長時間録音から複数クリップの区間を自動照合 |
| 候補フォルダ | `--external-audio-dir ...` | 長さとファイル時刻をもとに候補を自動選択 |
