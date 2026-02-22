#!/usr/bin/env bash
# Source this file to force sandbox-safe runtime/cache defaults for CSI work.

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export TMPDIR="${TMPDIR:-/tmp}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/tmp/.cache}"
export UV_PYTHON="${UV_PYTHON:-/usr/bin/python3}"

mkdir -p "$UV_CACHE_DIR" "$TMPDIR" "$XDG_CACHE_HOME"

echo "CSI sandbox env loaded:"
echo "  UV_CACHE_DIR=$UV_CACHE_DIR"
echo "  TMPDIR=$TMPDIR"
echo "  XDG_CACHE_HOME=$XDG_CACHE_HOME"
echo "  UV_PYTHON=$UV_PYTHON"
