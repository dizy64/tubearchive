#!/bin/bash
# TubeArchive Finder Integration Script
# Quick Action 및 Automator 앱에서 사용

# 입력 경로 (Finder에서 선택한 폴더/파일)
INPUT_PATH="$1"

if [ -z "$INPUT_PATH" ]; then
    osascript -e 'display dialog "폴더를 선택해주세요." buttons {"확인"} default button "확인" with icon stop with title "TubeArchive"'
    exit 1
fi

# tubearchive 경로 확인
TUBEARCHIVE_PATH=$(which tubearchive 2>/dev/null)

if [ -z "$TUBEARCHIVE_PATH" ]; then
    # uv tool 경로 확인
    TUBEARCHIVE_PATH="$HOME/.local/bin/tubearchive"
    if [ ! -f "$TUBEARCHIVE_PATH" ]; then
        osascript -e 'display dialog "tubearchive가 설치되지 않았습니다.\n\nuv tool install tubearchive 명령으로 설치해주세요." buttons {"확인"} default button "확인" with icon stop with title "TubeArchive"'
        exit 1
    fi
fi

# 확인 다이얼로그
FOLDER_NAME=$(basename "$INPUT_PATH")
RESPONSE=$(osascript -e "display dialog \"'$FOLDER_NAME' 폴더의 영상을 병합하시겠습니까?\" buttons {\"취소\", \"병합 시작\"} default button \"병합 시작\" with title \"TubeArchive\"" 2>/dev/null)

if [[ "$RESPONSE" != *"병합 시작"* ]]; then
    exit 0
fi

# Terminal에서 실행 (진행률 확인 가능)
osascript <<EOF
tell application "Terminal"
    activate
    do script "cd \"$INPUT_PATH\" && \"$TUBEARCHIVE_PATH\"; echo ''; echo '완료! 아무 키나 누르면 창이 닫힙니다.'; read -n 1"
end tell
EOF
