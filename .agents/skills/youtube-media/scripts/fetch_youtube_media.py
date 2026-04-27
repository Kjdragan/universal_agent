#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
from typing import List


def strip_proxies(env_dict: dict) -> dict:
    """
    Remove all known proxy-related environment variables to guarantee
    the download occurs on the native VPS IP instead of burning gigabytes
    on the residential proxies.
    """
    keys_to_remove = [
        "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
        "PROXY_USERNAME", "PROXY_PASSWORD", "PROXY_HOST", "PROXY_PORT",
        "WEBSHARE_PROXY_USER", "WEBSHARE_PROXY_PASS", "WEBSHARE_PROXY_HOST", "WEBSHARE_PROXY_PORT"
    ]
    cleaned = dict(env_dict)
    for key in keys_to_remove:
        cleaned.pop(key, None)
    return cleaned

def fetch_media(url: str, format_type: str, out_dir: str):
    print(f"-> Starting Hybrid Extraction Engine for: {url}")
    print(f"-> Media Format: {format_type}")
    print(f"-> Output Dir: {out_dir}")
    
    if not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
        
    cmd: List[str] = [
        "yt-dlp",
        "--output", os.path.join(out_dir, "%(title)s_%(id)s.%(ext)s")
    ]
    
    # Configure payload fidelity
    if format_type.lower() == "audio":
        cmd.extend(["-f", "bestaudio[ext=m4a]"])
    else:
        cmd.extend(["-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"])
        
    cmd.append(url)
    
    # Strip residential proxies from the environment so Native VPS PoT can be used
    safe_env = strip_proxies(os.environ)

    print(f"-> Bypassing Residential Proxies...")
    print(f"-> Executing PoT Provider Natively...")
    
    try:
        process = subprocess.Popen(
            cmd,
            env=safe_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        
        for line in iter(process.stdout.readline, ''):
            sys.stdout.write(line)
            
        process.stdout.close()
        return_code = process.wait()
        
        if return_code != 0:
            print("\n[ ERROR ] Media extraction failed. yt-dlp exited with non-zero status.")
            sys.exit(1)
            
        print(f"\n[ SUCCESS ] Media payload retrieved seamlessly via Native VPS.")
    except Exception as e:
        print(f"\n[ ERROR ] Execution exception: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch YouTube Media Bypassing Proxies via PoT")
    parser.add_argument("url", help="YouTube URL to download")
    parser.add_argument("--format", choices=["audio", "video"], default="audio", help="Download audio-only or combined video file")
    parser.add_argument("--out-dir", default=os.getcwd(), help="Output directory")
    
    args = parser.parse_args()
    fetch_media(args.url, args.format, args.out_dir)
