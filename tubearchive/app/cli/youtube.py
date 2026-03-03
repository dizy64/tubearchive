"""YouTube CLI facade module."""

from tubearchive.app.cli.main import (
    cmd_list_playlists,
    cmd_setup_youtube,
    cmd_upload_only,
    cmd_youtube_auth,
    upload_to_youtube,
)

__all__ = [
    "cmd_list_playlists",
    "cmd_setup_youtube",
    "cmd_upload_only",
    "cmd_youtube_auth",
    "upload_to_youtube",
]
