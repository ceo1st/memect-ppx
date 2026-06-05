#!/usr/bin/env python3
"""memect-ppx installer — works on Windows / macOS / Linux"""
import os
import platform
import subprocess
import sys
from pathlib import Path

MIN_PYTHON = (3, 12)
UV_INSTALL_URL = "https://astral.sh/uv/install.sh"
UV_INSTALL_URL_WIN = "https://astral.sh/uv/install.ps1"


def info(msg: str) -> None:
    print(f"[ppx] {msg}")


def error(msg: str) -> None:
    print(f"[ppx] ERROR: {msg}", file=sys.stderr)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    info(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


# ── 1. Python version check ───────────────────────────────────────────────────

def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        error(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, got {sys.version}")
        sys.exit(1)
    info(f"Python {sys.version.split()[0]} OK")


# ── 2. uv availability ────────────────────────────────────────────────────────

def ensure_uv() -> str:
    """Return path to uv, installing it if missing."""
    from shutil import which
    uv = which("uv")
    if uv:
        info(f"uv found: {uv}")
        return uv

    info("uv not found — installing...")
    system = platform.system()
    if system == "Windows":
        run([
            "powershell", "-ExecutionPolicy", "Bypass",
            "-Command",
            f"irm {UV_INSTALL_URL_WIN} | iex",
        ])
        # After install, uv lands in %USERPROFILE%\.local\bin
        candidate = Path.home() / ".local" / "bin" / "uv.exe"
    else:
        run(["sh", "-c", f"curl -LsSf {UV_INSTALL_URL} | sh"])
        candidate = Path.home() / ".local" / "bin" / "uv"

    if candidate.exists():
        return str(candidate)

    # Fallback: re-check PATH (installer may have modified it)
    uv = which("uv")
    if uv:
        return uv

    error("uv installation failed. Install manually: https://docs.astral.sh/uv/")
    sys.exit(1)


# ── 3. GPU detection ──────────────────────────────────────────────────────────

def detect_gpu() -> bool:
    """Best-effort NVIDIA GPU detection."""
    from shutil import which
    if which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi"], capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            pass
    return False


def ask_gpu(auto: bool) -> bool:
    if auto:
        has_gpu = detect_gpu()
        info(f"GPU detected: {has_gpu}")
        return has_gpu
    ans = input("[ppx] Install GPU (CUDA) support? [y/N] ").strip().lower()
    return ans in ("y", "yes")


# ── 4. Install ────────────────────────────────────────────────────────────────

def install(uv: str, gpu: bool) -> None:
    pkg = "memect-ppx[cuda]" if gpu else "memect-ppx"
    info(f"Installing {pkg} ...")
    run([uv, "pip", "install", pkg])

    onnx = "onnxruntime-gpu" if gpu else "onnxruntime"
    info(f"Installing {onnx} ...")
    run([uv, "pip", "install", onnx, "--no-config"])

    info("Installing opencv ...")
    run([uv, "pip", "install", "opencv-contrib-python", "--no-config"])


# ── 5. Verify ─────────────────────────────────────────────────────────────────

def verify(uv: str) -> None:
    try:
        run([uv, "run", "ppx", "--help"], capture_output=True)
        info("Installation verified — run `ppx --help` to get started.")
    except subprocess.CalledProcessError:
        error("Verification failed. Try: ppx --help")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Install memect-ppx")
    parser.add_argument("--gpu", action="store_true", help="Force GPU install")
    parser.add_argument("--cpu", action="store_true", help="Force CPU install")
    parser.add_argument("--auto", action="store_true", default=True,
                        help="Auto-detect GPU (default)")
    args = parser.parse_args()

    check_python()
    uv = ensure_uv()

    if args.gpu:
        gpu = True
    elif args.cpu:
        gpu = False
    else:
        gpu = ask_gpu(auto=args.auto)

    install(uv, gpu)
    verify(uv)


if __name__ == "__main__":
    main()
