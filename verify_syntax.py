import ast
import sys

try:
    with open("src/universal_agent/scripts/cleanup_report.py", "r") as f:
        source = f.read()
    ast.parse(source)
    print("Syntax OK")
except Exception as e:
    print(f"Syntax Error: {e}")
    sys.exit(1)
