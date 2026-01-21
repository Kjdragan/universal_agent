
import os
import re

def fix_indentation(filepath):
    print(f"Processing {filepath}...")
    with open(filepath, 'r') as f:
        lines = f.readlines()

    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        new_lines.append(line)
        
        # Check if this line is the 'with logfire.span...' wrapper I added
        # AND the NEXT line is the 'async for' loop
        if 'with logfire.span("llm_response_stream"' in line and i + 1 < len(lines):
            next_line = lines[i+1]
            if 'async for' in next_line and 'client.receive_response' in next_line:
                # Found the pattern!
                # The 'async for' line is already captured in the next iteration? 
                # No, I need to indent the 'async for' line and the body.
                
                # Actually, my previous multi_replace replaced:
                # Old:
                # async for ...
                # New:
                # with logfire...
                #     async for ...
                
                # So 'async for' IS already indented in the file relative to the 'with' block?
                # Let's check the file content I saw in `view_file` (Step 222).
                # Line 4946: with logfire.span...
                # Line 4947:     async for msg ...
                # Line 4948:     if isinstance...  <-- THIS IS THE PROBLEM. It has 4 spaces less than 4947.
                
                # So the 'async for' IS indented correctly relative to 'with'.
                # But the BODY (lines 4948+) is NOT indented relative to 'async for'.
                
                # So I need to find the `async for` line, determine its indentation, 
                # and then indent all subsequent lines that share the *previous* body indentation.
                
                # Wait, the `async for` is indented by X spaces. 
                # The body *should* be X+4.
                # Currently the body is likely X (or X-4 relative to the new async for?).
                
                # In Step 222:
                # 4946:         with logfire.span("llm_response_stream"):  (8 spaces)
                # 4947:             async for msg in client.receive_response(): (12 spaces)
                # 4948:             if isinstance(msg, ResultMessage): (12 spaces)
                
                # Wait, if 4947 is 12 spaces and 4948 is 12 spaces, then 4948 is INSIDE the async for loop (Python requires a suit).
                # BUT `if` is a statement.
                # `async for ...:` expects an indented block.
                # If 4948 has 12 spaces, it is at the SAME level as `async for`.
                # So it is NOT inside the loop.
                # It expects 16 spaces.
                
                # So, I need to take everything that WAS at 12 spaces (original body indentation?) 
                # and indent it to 16 spaces.
                
                # Original indentation of `async for` was 8 spaces.
                # Original body was 12 spaces.
                # Now `async for` is 12 spaces.
                # Body is still 12 spaces.
                # So I need to indent lines that are 12 spaces (and deeper) to be +4 spaces.
                # Until I hit something that is 8 spaces (end of loop).
                
                target_indent = len(next_line) - len(next_line.lstrip()) # Should be 12
                # But actually, I should just look for the block following.
                
                pass # Continue to next line loop execution

        # Check if we are inside a block that needs indenting
        # Actually, simpler logic:
        # If I see `with logfire.span("llm_response_stream"...):`
        # I know the structure should be:
        #   with ...:
        #       async for ...:
        #           BODY
        
        # My previous edit made:
        #   with ...:
        #       async for ...:
        #   BODY (at same level as async for)
        
        # So I need to detect this state and indent BODY.
        
    # Let's do a second pass or a more stateful pass.
    
    final_lines = []
    
    iterator = iter(lines)
    for line in iterator:
        final_lines.append(line)
        if 'with logfire.span("llm_response_stream"' in line:
            # Look at next line
            try:
                loop_line = next(iterator)
                final_lines.append(loop_line)
                
                if 'async for' in loop_line and 'client.receive_response' in loop_line:
                    # Determine indentation of the loop line
                    loop_indent = len(loop_line) - len(loop_line.lstrip())
                    
                    # We expect the body to be loop_indent + 4.
                    # In the broken file, the body starts at loop_indent (or less?).
                    # Usually the body was indented relative to the *old* async for position.
                    # The old async for position was loop_indent - 4 (because I added `with` wrapper and indented `async for`).
                    # So the body is likely at loop_indent.
                    
                    # We need to indent everything that is >= loop_indent until we hit something < loop_indent.
                    
                    # Consuming the body
                    while True:
                        # We need to peek or read. 
                        # Since we can't easily peek output of iterator, we'll read and append if we stop.
                        # But wait, we need to modify.
                        pass 
                        # This logic is hard with just iterator.
                        break
            except StopIteration:
                break
                
    # New strategy: Index based
    
    fixed_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        fixed_lines.append(line)
        i += 1
        
        if 'with logfire.span("llm_response_stream"' in line:
            # Check if next line is the loop
            if i < len(lines) and 'async for' in lines[i] and 'client.receive_response' in lines[i]:
                # Grab the loop line
                loop_line = lines[i]
                fixed_lines.append(loop_line)
                i += 1
                
                # Current indentation of the loop line
                loop_indent = len(loop_line) - len(loop_line.lstrip())
                
                # Now indent the body
                while i < len(lines):
                    curr_line = lines[i]
                    stripped = curr_line.strip()
                    
                    if not stripped:
                        # Empty line, just keep it (maybe indent it? doesn't matter much but cleaner to not)
                        fixed_lines.append(curr_line)
                        i += 1
                        continue
                        
                    curr_indent = len(curr_line) - len(curr_line.lstrip())
                    
                    # If current indentation is less than the loop's indentation, 
                    # it means we dropped out of the loop block.
                    # (Because the body currently is at loop_indent level, or maybe deeper)
                    if curr_indent < loop_indent:
                        break
                    
                    # If it is >= loop_indent, it belongs to the body (in the broken state)
                    # We add 4 spaces.
                    fixed_lines.append("    " + curr_line)
                    i += 1
                    
    with open(filepath, 'w') as f:
        f.writelines(fixed_lines)
    print(f"Fixed {filepath}")

# List of files to fix
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
