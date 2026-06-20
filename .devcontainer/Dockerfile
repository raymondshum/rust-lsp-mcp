# Base: Python 3.12 (devcontainer default base image)
# The rust:1 feature installs the Rust toolchain + rust-analyzer (native, linux-arm64).
# uv is layered from the first-party Astral image (download-once, digest-pinnable).
FROM mcr.microsoft.com/devcontainers/python:3.12

# Layer the first-party uv binary from Astral's published image.
# Pin the tag for reproducibility; bump to pull a newer uv.
COPY --from=ghcr.io/astral-sh/uv:0.7.13 /uv /uvx /usr/local/bin/

# Keep uv's managed Python consistent with the container interpreter.
ENV UV_PYTHON=python3.12

# Pre-create the user cache dir, owned by vscode. The model-cache bind mount targets
# ~/.cache/chroma; if .cache doesn't already exist, the Docker daemon auto-creates that
# parent as root, which blocks vscode from writing siblings (e.g. uv's ~/.cache/uv).
# Creating it here means Docker only adds the chroma mountpoint and leaves .cache writable.
RUN mkdir -p /home/vscode/.cache && chown vscode:vscode /home/vscode/.cache
