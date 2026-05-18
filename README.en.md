<p align="center">
  <img src="assets/readme/tubearchive-logo.svg" alt="TubeArchive logo" width="760">
</p>

<h1 align="center">TubeArchive</h1>

<p align="center">
  <a href="README.md">한국어</a> | English | <a href="README.ja.md">日本語</a>
</p>

[![CI](https://github.com/dizy64/tubearchive/actions/workflows/ci.yml/badge.svg)](https://github.com/dizy64/tubearchive/actions/workflows/ci.yml)

**TubeArchive** is a macOS video archiving tool for creators who work with footage from Nikon, GoPro, DJI, iPhone, and other cameras. It standardizes mixed 4K clips to HEVC 10-bit, merges them, applies optional audio/video processing, and can prepare the result for YouTube.

Use the CLI for repeatable automation, or launch the TUI dashboard to choose files, adjust options, and review project status from one terminal screen.

```bash
# Interactive dashboard
tubearchive tui ~/Videos/Trip2026/

# Automated CLI merge
tubearchive ~/Videos/Trip2026/ --normalize-audio --thumbnail
```

## TUI Preview

<p align="center">
  <img src="assets/readme/tui-pipeline.svg" alt="TubeArchive TUI Pipeline tab screenshot">
</p>

<p align="center">
  <img src="assets/readme/tui-stats.svg" alt="TubeArchive TUI Stats tab screenshot">
</p>

The TUI includes a file browser, external audio picker, encoding options, presets, projects, statistics, history, and YouTube tabs. It runs directly in your terminal with `tubearchive tui`; no local web server is required.

## Features

- **Interactive TUI**: select files, tune options, save presets, monitor progress, and inspect projects/statistics/history.
- **Smart video scanning**: current directory, explicit files, or full directories.
- **Portrait video layout**: blurred background plus centered foreground for vertical footage.
- **Resume support**: SQLite-backed job tracking and automatic continuation after interruption.
- **VideoToolbox acceleration**: fast HEVC encoding on Apple Silicon Macs.
- **Device detection**: Nikon N-Log, iPhone, GoPro, DJI, and generic sources.
- **Sequence grouping**: GoPro/DJI split files are detected and joined as one shooting sequence.
- **Audio processing**: EBU R128 loudness normalization, denoise, silence trimming, BGM mixing, and external microphone sync.
- **Video processing**: stabilization, LUT color grading, timelapse generation, splitting, and thumbnail extraction.
- **YouTube workflow**: OAuth upload, chapter timestamps, playlist integration, and thumbnail selection.
- **Project history**: track merge jobs, uploads, archive actions, and device statistics.

## Output Profile

All inputs are normalized to **HEVC 50 Mbps 10-bit (p010le), 29.97 fps** for merge compatibility.

| Device | Detection signal | Output profile |
|--------|------------------|----------------|
| Nikon (N-Log) | `color_transfer: arib-std-b67` / `smpte2084` | SDR BT.709 with HDR-to-SDR conversion |
| iPhone | SDR source metadata | SDR BT.709 |
| GoPro | SDR source metadata | SDR BT.709 |
| DJI | SDR source metadata | SDR BT.709 |
| Other | Automatic detection | SDR BT.709 |

## Requirements

- macOS 12+ with VideoToolbox support
- Python 3.14+
- FFmpeg 6.0+ with `hevc_videotoolbox`
- `uv` for Python package management
- `exiftool` is recommended for DSLR/mirrorless camera model detection

## Install

```bash
# System dependencies
brew install ffmpeg exiftool

# uv and Python
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.14

# Project setup
git clone https://github.com/dizy64/tubearchive.git
cd tubearchive
uv sync
```

Install as a global CLI tool:

```bash
uv tool install .
uv tool update-shell
```

## Usage

```bash
# Launch the TUI dashboard
tubearchive tui ~/Videos/Trip2026/

# Merge every supported video in a folder
tubearchive ~/Videos/Trip2026/

# Merge specific files in creation-time order
tubearchive video1.mp4 video2.mov video3.mts

# Preview the plan without transcoding
tubearchive --dry-run ~/Videos/Trip2026/

# Normalize audio and generate thumbnails
tubearchive --normalize-audio --thumbnail ~/Videos/Trip2026/

# Use an external microphone recording and clap-sync it
tubearchive --external-audio ~/Audio/mic.wav --sync-audio-clap video.mp4

# Upload the merged result to YouTube
tubearchive --upload ~/Videos/Trip2026/
```

Run from the repository without global installation:

```bash
uv run tubearchive tui ~/Videos/Trip2026/
uv run tubearchive ~/Videos/Trip2026/
```

## TUI Guide

For detailed launch modes, tabs, and keyboard shortcuts, see the [TUI guide](docs/tui.en.md).

## Configuration

Create the default config file:

```bash
tubearchive --init-config
```

TubeArchive reads `~/.tubearchive/config.toml`. Precedence is:

```text
CLI options > environment variables > config.toml > defaults
```

Common environment variables:

| Variable | Purpose |
|----------|---------|
| `TUBEARCHIVE_OUTPUT_DIR` | Default output directory |
| `TUBEARCHIVE_DB_PATH` | SQLite database path |
| `TUBEARCHIVE_PARALLEL` | Parallel transcode count |
| `TUBEARCHIVE_NORMALIZE_AUDIO` | Enable loudness normalization |
| `TUBEARCHIVE_AUTO_LUT` | Enable device-based LUT matching |
| `TUBEARCHIVE_YOUTUBE_PLAYLIST` | Default YouTube playlist IDs |

## YouTube Setup

```bash
# Show current auth status and setup guidance
tubearchive --setup-youtube

# Upload an existing video without merging
tubearchive --upload-only merged_output.mp4
```

You need a Google Cloud OAuth desktop client JSON at `~/.tubearchive/client_secrets.json`. The first upload opens a browser authorization flow and stores the token at `~/.tubearchive/youtube_token.json`.

## Development

```bash
uv run pytest tests/unit/ -v
uv run mypy tubearchive/
uv run ruff check tubearchive/ tests/
uv run ruff format --check tubearchive/ tests/
```

For the complete command reference, see the Korean [README.md](README.md).
