import sys

class DualWriter:
    """Writes to both a file and the original stream (stdout/stderr)."""
    def __init__(self, file_handle, original_stream):
        self.file = file_handle
        self.stream = original_stream

    def write(self, data):
        self.stream.write(data)
        self.stream.flush()
        try:
            self.file.write(data)
            self.file.flush()
        except ValueError:
            pass # File might be closed

    def flush(self):
        self.stream.flush()
        try:
            self.file.flush()
        except ValueError:
            pass

class ExecutionLogger:
    """
    Captures stdout/stderr to a file for a specific task execution.
    """
    def __init__(self, log_file_path):
        self.log_file_path = log_file_path
        self.file_handle = None
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr

    def __enter__(self):
        self.file_handle = open(self.log_file_path, "w", encoding="utf-8")
        sys.stdout = DualWriter(self.file_handle, self.original_stdout)
        sys.stderr = DualWriter(self.file_handle, self.original_stderr)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr
        if self.file_handle:
            self.file_handle.close()
