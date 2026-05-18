# TubeArchive TUI Guide

[Back to README](../README.en.md) | [한국어](tui.md) | [日本語](tui.ja.md)

The TubeArchive TUI is a terminal dashboard for selecting footage, tuning options, tracking progress, and reviewing projects, stats, history, and YouTube settings.

## Launch

```bash
# Start from the current directory
tubearchive tui

# Open a specific shoot folder
tubearchive tui ~/Videos/Trip2026/

# Run from the development environment
uv run tubearchive tui ~/Videos/Trip2026/
```

## Tabs

| Tab | Purpose |
|-----|---------|
| Pipeline | Select files/folders, tune encoding options, and run the pipeline |
| Projects | Review project lists and date-based job status |
| Stats | Inspect processing totals, device distribution, and archive stats |
| History | Browse transcode, merge, and upload history |
| YouTube | Check authentication, playlists, and upload options |

## Shortcuts

| Key | Action |
|-----|--------|
| `1` | Pipeline tab |
| `2` | Projects tab |
| `3` | Stats tab |
| `4` | History tab |
| `5` | YouTube tab |
| `r` | Refresh current tab |
| `t` | Toggle theme |
| `q` | Quit |

## External Audio Picker

In the Pipeline tab, the external audio panel can apply an audio file or candidate folder directly to the current option state.

| Button | Applied option | Use case |
|--------|----------------|----------|
| Single file | `--external-audio ... --external-audio-scope single` | Apply one external audio file to one video |
| Long recording | `--external-audio ... --external-audio-scope long` | Match segments from one long recording to multiple clips |
| Candidate folder | `--external-audio-dir ...` | Auto-select the best candidate by duration and file time |
