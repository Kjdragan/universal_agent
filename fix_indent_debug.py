
import os
import sys

def fix_indentation(filepath):
    print(f"Processing {filepath}...")
    try:
        with open(filepath, 'r') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return

    fixed_lines = []
    i = 0
    modifications = 0
    
    while i < len(lines):
        line = lines[i]
        fixed_lines.append(line)
        i += 1
        
        # Helper to check if line is the 'with' block
        if 'with logfire.span("llm_response_stream"' in line:
            # Check safely if next line exists
            if i < len(lines):
                loop_line = lines[i]
                if 'async for' in loop_line and 'client.receive_response' in loop_line:
                    print(f"  Found loop at line {i+1}")
                    fixed_lines.append(loop_line)
                    i += 1
                    
                    loop_indent = len(loop_line) - len(loop_line.lstrip())
                    
                    while i < len(lines):
                        curr_line = lines[i]
                        stripped = curr_line.strip()
                        
                        if not stripped:
                            fixed_lines.append(curr_line)
                            i += 1
                            continue
                            
                        curr_indent = len(curr_line) - len(curr_line.lstrip())
                        
                        if curr_indent < loop_indent:
                            # End of loop body
                            print(f"  End of loop at line {i+1} (indent {curr_indent} < {loop_indent})")
                            break
                        
                        # Fix indentation
                        fixed_lines.append("    " + curr_line)
                        i += 1
                        modifications += 1
                        
    print(f"  Writing {len(fixed_lines)} lines ({modifications} modifications)...")
    with open(filepath, 'w') as f:
        f.writelines(fixed_lines)
    print(f"Fixed {filepath}")

files = [
    '/home/kjdragan/lrepos/universal_agent/src/universal_agent/main.py',
    '/home/kjdragan/lrepos/universal_agent/src/universal_agent/agent_core.py',
    '/home/kjdragan/lrepos/universal_agent/src/universal_agent/urw/interview.py'
]

for f in files:
    if os.path.exists(f):
        fix_indentation(f)
    else:
        print(f"File not found: {f}")
