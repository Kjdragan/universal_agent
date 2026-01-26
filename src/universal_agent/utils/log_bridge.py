import logging
import sys
import io
import asyncio
from typing import Callable, Any, TextIO
from universal_agent.agent_core import EventType

class LogBridgeHandler(logging.Handler):
    """
    A logging handler that bridges Python logs to the Agent's event stream.
    This allows logs (like HTTPX requests) to be visible in the frontend UI.
    """
    def __init__(self, event_callback: Callable[[EventType, dict], Any]):
        super().__init__()
        self.event_callback = event_callback
        # Set a formatter that simplifies the message
        self.setFormatter(logging.Formatter('%(message)s'))

    def emit(self, record):
        try:
            msg = self.format(record)
            
            # Use the loop to emit async event from sync context
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                     loop.create_task(self.event_callback(
                        EventType.STATUS,
                        {
                            "status": msg,
                            "level": record.levelname,
                            "prefix": record.name,
                            "is_log": True
                        }
                    ))
            except RuntimeError:
                pass
                
        except Exception:
            self.handleError(record)


class StdoutInterceptor:
    """
    Intercepts writes to sys.stdout/stderr and bridges them to the UI event stream.
    Also ensures they are still written to the original stream.
    """
    def __init__(self, event_callback: Callable[[EventType, dict], Any], original_stream: TextIO, prefix: str = "stdout"):
        self.event_callback = event_callback
        self.original_stream = original_stream
        self.prefix = prefix
        self._buffer = ""
        self._reentrant = False  # Guard against infinite loops

    def write(self, text: str):
        # Always write to original stream first
        self.original_stream.write(text)
        self.original_stream.flush()  # Ensure immediate terminal output

        if self._reentrant:
            return

        try:
            self._reentrant = True
            
            # Simple buffering to handle fragmented writes, but aggressive flushing on newlines
            if "\n" in text:
                parts = text.split("\n")
                # Add previous buffer to first part
                if self._buffer:
                    parts[0] = self._buffer + parts[0]
                    self._buffer = ""
                
                # Emit all complete lines
                for part in parts[:-1]:
                    if part.strip(): # Optional: Decide if we want empty lines? Maybe yes for formatting.
                        self._emit(part)
                
                # Keep the last part in buffer
                self._buffer = parts[-1]
            else:
                self._buffer += text
                
        except Exception:
            # Fallback to ensure we don't crash
            pass
        finally:
            self._reentrant = False

    def flush(self):
        self.original_stream.flush()
        if self._buffer.strip():
            self._emit(self._buffer)
            self._buffer = ""

    def _emit(self, text: str):
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self.event_callback(
                    EventType.STATUS,
                    {
                        "status": text,
                        "level": "INFO", 
                        "prefix": self.prefix, # "stdout" or "stderr" (or specific tool name if we get fancy)
                        "is_log": True
                    }
                ))
        except RuntimeError:
            pass
            
    # Proxy other attributes to original stream
    def __getattr__(self, name):
        return getattr(self.original_stream, name)
