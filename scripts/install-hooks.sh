#!/usr/bin/env bash
# Git hook 설치 스크립트
# - scripts/pre-commit
# - scripts/pre-push

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

GIT_DIR="$(git rev-parse --git-dir)"
HOOK_DIR="${GIT_DIR}/hooks"

install_hook() {
    local name="$1"
    local source_path="$2"
    local target_path="${HOOK_DIR}/${name}"

    if [ ! -f "$source_path" ]; then
        echo "오류: ${source_path} 파일이 없습니다."
        exit 1
    fi

    mkdir -p "${HOOK_DIR}"

    # 기존 훅 백업
    if [ -e "$target_path" ] && [ ! -L "$target_path" ]; then
        mv "$target_path" "${target_path}.bak"
        echo "기존 훅을 ${target_path}.bak 으로 백업했습니다."
    elif [ -L "$target_path" ]; then
        rm "$target_path"
    fi

    ln -s "$source_path" "$target_path"
    echo "${name} 설치 완료: ${target_path} -> ${source_path}"
}

install_hook "pre-commit" "$(pwd)/scripts/pre-commit"
install_hook "pre-push" "$(pwd)/scripts/pre-push"

echo "Git hook 설치 완료"
