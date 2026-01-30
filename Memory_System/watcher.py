"""
File Watcher for Memory System.
Monitors the memory/ directory and syncs changes to the archival memory index.
"""

import os
import time
import hashlib
from typing import Callable, Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class MemoryFileHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[str, str], None], patterns: list = ["*.md"]):
        self.callback = callback
        self.patterns = patterns
        self.last_hashes = {}  # proper dedupe
        
    def _process(self, filepath: str):
        if not any(filepath.endswith(p.replace("*", "")) for p in self.patterns):
            return
            
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
                
            # Dedupe based on content hash
            current_hash = hashlib.md5(content.encode()).hexdigest()
            if self.last_hashes.get(filepath) == current_hash:
                return
                
            self.last_hashes[filepath] = current_hash
            self.callback(filepath, content)
            
        except Exception as e:
            # Log error but don't crash watcher
            print(f"[MemoryWatcher] Error reading {filepath}: {e}")

    def on_modified(self, event):
        if not event.is_directory:
            self._process(event.src_path)
            
    def on_created(self, event):
        if not event.is_directory:
            self._process(event.src_path)

class MemoryWatcher:
    def __init__(self, watch_dir: str, on_change: Callable[[str, str], None]):
        self.watch_dir = watch_dir
        self.handler = MemoryFileHandler(on_change)
        self.observer = Observer()
        
    def start(self):
        if not os.path.exists(self.watch_dir):
            return
            
        self.observer.schedule(self.handler, self.watch_dir, recursive=False)
        self.observer.start()
        print(f"[MemoryWatcher] Started watching {self.watch_dir}")
        
    def stop(self):
        self.observer.stop()
        self.observer.join()
