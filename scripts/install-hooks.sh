#!/usr/bin/env bash
# Pre-commit hook 설치 스크립트
# scripts/pre-commit → .git/hooks/pre-commit symlink 생성

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

HOOK_SRC="$(pwd)/scripts/pre-commit"
HOOK_DST="$(pwd)/.git/hooks/pre-commit"

if [ ! -f "$HOOK_SRC" ]; then
    echo "오류: $HOOK_SRC 파일이 없습니다."
    exit 1
fi

# 기존 hook 백업
if [ -e "$HOOK_DST" ] && [ ! -L "$HOOK_DST" ]; then
    mv "$HOOK_DST" "${HOOK_DST}.bak"
    echo "기존 hook을 ${HOOK_DST}.bak 으로 백업했습니다."
elif [ -L "$HOOK_DST" ]; then
    rm "$HOOK_DST"
fi

ln -s "$HOOK_SRC" "$HOOK_DST"
echo "pre-commit hook 설치 완료: $HOOK_DST → $HOOK_SRC"
