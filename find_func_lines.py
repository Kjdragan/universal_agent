import ast

with open('src/universal_agent/gateway_server.py', 'r') as f:
    source = f.read()

tree = ast.parse(source)
funcs_to_find = [
    '_build_autonomous_daily_briefing_command',
    '_autonomous_briefing_day_slug',
    '_generate_autonomous_daily_briefing_artifact',
    '_ensure_autonomous_daily_briefing_job'
]

for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name in funcs_to_find:
        print(f"{node.name}: {node.lineno} to {node.end_lineno}")
