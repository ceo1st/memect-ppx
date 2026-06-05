#!/usr/bin/env sh
set -e

# PyPI 镜像
PYPI_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
# uv 安装源（官方 + 国内备用）
UV_INSTALL_URL="https://astral.sh/uv/install.sh"
UV_INSTALL_URL_CN="https://gitee.com/astral-sh/uv/raw/main/scripts/install.sh"

info() { printf '\033[32m[ppx]\033[0m %s\n' "$*"; }
err()  { printf '\033[31m[ppx] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ── 1. 检测系统 ───────────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Linux*)  SYS=linux ;;
    Darwin*) SYS=mac ;;
    MINGW*|MSYS*|CYGWIN*) SYS=windows ;;
    *) err "Unsupported OS: $OS" ;;
esac
info "System: $SYS"

# ── 2. 安装 uv ────────────────────────────────────────────────────────────────
ensure_uv() {
    export PATH="$HOME/.local/bin:$PATH"
    if command -v uv >/dev/null 2>&1; then
        info "uv found: $(command -v uv)"; return
    fi
    info "Installing uv..."
    if curl -LsSf "$UV_INSTALL_URL" | sh; then
        :
    else
        info "Official source failed, trying mirror..."
        curl -LsSf "$UV_INSTALL_URL_CN" | sh || err "uv install failed. See https://docs.astral.sh/uv/"
    fi
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1 || err "uv not found after install, restart shell and re-run."
}

export UV_LOCK_TIMEOUT=60

# ── 3. GPU 检测 ───────────────────────────────────────────────────────────────
detect_gpu() {
    command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1
}

# ── 4. 解析参数 ───────────────────────────────────────────────────────────────
GPU=""
for arg in "$@"; do
    case "$arg" in
        --gpu) GPU=1 ;;
        --cpu) GPU=0 ;;
    esac
done

ensure_uv

if [ -z "$GPU" ]; then
    if detect_gpu; then
        info "NVIDIA GPU detected"
        printf '[ppx] Install GPU (CUDA) support? [y/N] '
        read -r ans
        case "$ans" in y|Y|yes|YES) GPU=1 ;; *) GPU=0 ;; esac
    else
        info "No GPU detected, using CPU"
        GPU=0
    fi
fi

# ── 5. 安装包 ─────────────────────────────────────────────────────────────────
PKG="memect-ppx"
ONNX="onnxruntime"
[ "$GPU" = "1" ] && PKG="memect-ppx[cuda]" && ONNX="onnxruntime-gpu"

# 清除可能干扰的环境变量
unset UV_INDEX_URL UV_EXTRA_INDEX_URL UV_DEFAULT_INDEX UV_INDEX

info "Installing $PKG (this may take a few minutes on first run)..."
uv tool install "$PKG" \
    --no-config \
    --default-index "$PYPI_MIRROR" \
    --extra-index-url "https://pypi.org/simple" \
    --force

info "Installing $ONNX + opencv ..."
uv tool install "$PKG" \
    --with "$ONNX" \
    --with opencv-contrib-python \
    --no-config \
    --default-index "$PYPI_MIRROR" \
    --extra-index-url "https://pypi.org/simple" \
    --force

# ── 6. 验证 ───────────────────────────────────────────────────────────────────
export PATH="$HOME/.local/bin:$PATH"
if command -v ppx >/dev/null 2>&1; then
    info "Done! Run: ppx --help"
else
    info "Installed. Restart shell or run: source ~/.local/bin/env"
fi
