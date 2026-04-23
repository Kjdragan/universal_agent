#!/usr/bin/env python
"""Narrate a text file into an MP3 audiobook using edge-tts."""

import argparse
import asyncio
import os
import re
import sys
import tempfile

import edge_tts


def chunk_text(text: str, max_chars: int = 4000) -> list[str]:
    """Split text into chunks at sentence boundaries."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    sentences = re.split(r'(?<=[.!?])\s+', text)
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) + 1 > max_chars:
            if current:
                chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()

    if current:
        chunks.append(current.strip())

    return chunks


def concatenate_mp3_files(file_paths: list[str], output_path: str) -> None:
    """Concatenate MP3 files by merging raw frames (no ffmpeg needed)."""
    with open(output_path, 'wb') as out:
        for path in file_paths:
            with open(path, 'rb') as f:
                out.write(f.read())


async def narrate(text: str, output_path: str, voice: str = "en-US-AvaNeural") -> str:
    """Generate narration MP3 from text."""
    chunks = chunk_text(text)
    print(f"Narrating {len(text)} chars in {len(chunks)} chunk(s)...")

    if len(chunks) == 1:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
    else:
        temp_files = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, chunk in enumerate(chunks):
                tmp_path = os.path.join(tmpdir, f"chunk_{i:04d}.mp3")
                communicate = edge_tts.Communicate(chunk, voice)
                await communicate.save(tmp_path)
                temp_files.append(tmp_path)
                print(f"  Chunk {i+1}/{len(chunks)} done ({len(chunk)} chars)")

            # Copy temp files to a persistent temp location before tmpdir cleanup
            persist_dir = tempfile.mkdtemp()
            persist_files = []
            for tf in temp_files:
                name = os.path.basename(tf)
                dest = os.path.join(persist_dir, name)
                with open(tf, 'rb') as src, open(dest, 'wb') as dst:
                    dst.write(src.read())
                persist_files.append(dest)

        concatenate_mp3_files(persist_files, output_path)

        # Cleanup
        for pf in persist_files:
            os.unlink(pf)
        os.rmdir(persist_dir)

    size = os.path.getsize(output_path)
    print(f"Output: {output_path} ({size:,} bytes)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Narrate text file to MP3")
    parser.add_argument("input", help="Input text file path")
    parser.add_argument("-o", "--output", help="Output MP3 path")
    parser.add_argument("-v", "--voice", default="en-US-AvaNeural", help="Voice ID")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    with open(args.input, 'r') as f:
        text = f.read()

    if not text.strip():
        print("ERROR: Input file is empty", file=sys.stderr)
        sys.exit(1)

    output = args.output or args.input.rsplit('.', 1)[0] + "_narrated.mp3"
    asyncio.run(narrate(text, output, args.voice))


if __name__ == "__main__":
    main()
