#!/usr/bin/env bash

# Git hook 공통 출력 유틸리티

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

info() {
    echo -e "${GREEN}[${HOOK_NAME}]${NC} $*"
}

warn() {
    echo -e "${YELLOW}[${HOOK_NAME}]${NC} $*"
}

fail() {
    echo -e "${RED}[${HOOK_NAME}]${NC} $*"
    exit 1
}

