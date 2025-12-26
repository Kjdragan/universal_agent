"""
Session Transcript Builder
Parses trace.json data to create a rich, human-readable markdown transcript of the agent session.
Acts as a "Replay Studio" for understanding agent behavior.
"""

import json
import ast
import re
from datetime import datetime
from typing import Dict, Any, List

def format_timestamp(iso_str: str) -> str:
    """Format ISO timestamp to readable time string (HH:MM:SS)."""
    if not iso_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%H:%M:%S")
    except ValueError:
        return iso_str

def generate_transcript(trace_data: Dict[str, Any], output_path: str):
    """
    Generate a markdown transcript from trace data and save to file.
    
    Args:
        trace_data: Dictionary containing session trace data
        output_path: Path to write the markdown file
    """
    
    # Extract session info
    session_info = trace_data.get("session_info", {})
    start_time = trace_data.get("start_time")
    end_time = trace_data.get("end_time")
    duration = trace_data.get("total_duration_seconds", "N/A")
    trace_id = trace_data.get("trace_id", "N/A")
    
    # Build content
    lines = []
    
    # 1. Header
    lines.append(f"# 🎬 Session Transcript")
    lines.append(f"**generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**")
    lines.append("")
    lines.append("## 📋 Session Info")
    lines.append("| Metadata | Value |")
    lines.append("|----------|-------|")
    lines.append(f"| **User ID** | `{session_info.get('user_id', 'N/A')}` |")
    lines.append(f"| **Trace ID** | `{trace_id}` |")
    lines.append(f"| **Logfire Trace** | [View Full Trace](https://logfire.pydantic.dev/Kjdragan/composio-claudemultiagent?q=trace_id%3D%27{trace_id}%27) |")
    lines.append(f"| **Duration** | {duration}s |")
    lines.append(f"| **Start Time** | {format_timestamp(start_time)} |")
    lines.append(f"| **End Time** | {format_timestamp(end_time)} |")
    
    # Check for iterations
    iterations = trace_data.get("iterations", [])
    if iterations:
        # Show stats from last iteration if available
        last_iter = iterations[-1]
        lines.append(f"| **Iterations** | {len(iterations)} |")
    
    lines.append("")
    
    # 2. Timeline Reconstruction
    lines.append("## 🎞️ Timeline")
    lines.append("")
    
    tool_calls = trace_data.get("tool_calls", [])
    tool_results = trace_data.get("tool_results", [])
    
    # User Query
    query = trace_data.get("query", "N/A")
    lines.append(f"### 👤 User Request")
    lines.append(f"> {query}")
    lines.append("")

    if not iterations:
        lines.append("*No complex tool iterations recorded.*")
    
    for i, iter_data in enumerate(iterations):
        iter_num = iter_data.get("iteration", i + 1)
        
        # Iteration Header
        lines.append(f"---")
        lines.append(f"### 🔄 Iteration {iter_num}")
        
        # Extract thoughts if available (usually in multi-execute inputs)
        iter_calls = [tc for tc in tool_calls if tc.get("iteration") == iter_num]
        
        if not iter_calls:
            lines.append("*No tools called in this iteration.*")
            continue
            
        for call in iter_calls:
            call_id = call.get("id")
            name = call.get("name", "Unknown Tool")
            offset = call.get("time_offset_seconds", 0)
            
            # Parsing Input
            # In trace.json, 'input' is usually a dict. 'input_preview' might be missing.
            tool_input = call.get("input") 
            if tool_input is None:
                # Fallback to preview if input is missing (unlikely in new traces)
                tool_input = call.get("input_preview", {})
            
            # Extract "Thought" from input if it exists (common in Composio tools)
            thought = None
            if isinstance(tool_input, dict):
                 thought = tool_input.get("thought")
            
            # Determine icon based on tool type
            icon = "🛠️"
            if any(x in name.upper() for x in ["WORKBENCH", "CODE", "EXECUTE", "BASH", "PYTHON"]):
                icon = "🏭"
            elif any(x in name.upper() for x in ["SEARCH", "CRAWL", "READER", "GOOGLE"]):
                icon = "🔎"
            elif "EMAIL" in name.upper() or "SLACK" in name.upper():
                icon = "📨"
            elif "TASK" in name.upper():
                icon = "🤖" # Sub-agent delegation
                
            # Render Thought Block if present
            if thought:
                 lines.append(f"#### 💭 Thought")
                 lines.append(f"> {thought}")
                 lines.append("")
            
            lines.append(f"#### {icon} Tool Call: `{name}` (+{offset}s)")
            
            # Input Rendering
            lines.append(f"<details>")
            lines.append(f"<summary><b>Input Parameters</b></summary>")
            lines.append("")
            lines.append("```json")
            try:
                if isinstance(tool_input, (dict, list)):
                    lines.append(json.dumps(tool_input, indent=2))
                else:
                    lines.append(str(tool_input))
            except Exception:
                lines.append(str(tool_input))
            lines.append("```")
            lines.append(f"</details>")
            lines.append("")
            
            # Result Matching
            result = next((tr for tr in tool_results if tr.get("tool_use_id") == call_id), None)
            
            if result:
                content = result.get("content_preview", "No content")
                is_error = result.get("is_error", False)
                if is_error:
                    lines.append(f"> ⚠️ **Error detected**")
                
                # Smasher Logic for Content:
                # 1. content might be a python list repr: "[{'type': 'text', 'text': '...'}]"
                # 2. inside that text might be a json string: '{"result": "..."}'
                # 3. we want to unwrap fully to show the cleanest JSON possible
                
                final_content_str = str(content)
                code_block_lang = "text"
                
                # Attempt to unwrap Python list of TextBlocks
                try:
                    # Attempt safe parsing first
                    if final_content_str.strip().startswith("[") and "'type':" in final_content_str:
                         parsed = ast.literal_eval(final_content_str)
                         if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                             if parsed[0].get("type") == "text":
                                 final_content_str = parsed[0].get("text", "")
                except Exception:
                    # Fallback: Regex for truncated items usually found in trace.json artifacts
                    # Matches: [{'type': 'text', 'text': '...
                    # We want to capture the content inside the 'text': '...'
                    match = re.search(r"^\s*\[\s*\{\s*['\"]type['\"]\s*:\s*['\"]text['\"]\s*,\s*['\"]text['\"]\s*:\s*['\"](.*)", final_content_str, re.DOTALL)
                    if match:
                        # We found the wrapper start. Now we assume the content is everything after that.
                        # But we need to handle the trailing quote/brace if it exists, or just take the rest if truncated.
                        inner = match.group(1)
                        # Check if it ends cleanly
                        clean_end_match = re.search(r"(.*)['\"]\s*\}\s*\]\s*$", inner, re.DOTALL)
                        if clean_end_match:
                            final_content_str = clean_end_match.group(1)
                        else:
                            # Truncated? Just take it, but maybe strip one trailing quote if it looks like the end
                            # If it ends with ... it's definitely truncated
                            final_content_str = inner
                            
                            # Clean up potential "..." artifact if we want pure JSON? No, leave dots to show truncation.
                
                # Now attempt to parse as JSON (unwrap another layer if needed)
                try:
                    parsed_json = json.loads(final_content_str)
                    final_content_str = json.dumps(parsed_json, indent=2)
                    code_block_lang = "json"
                except Exception:
                    # If it's not valid JSON (e.g. truncated), leave as is.
                    # But if it looks like JSON, highlight it as such.
                    if final_content_str.strip().startswith("{") or final_content_str.strip().startswith("["):
                        code_block_lang = "json"

                lines.append(f"**Result Output:**")
                
                # Truncate very long outputs for readability
                if len(final_content_str) > 2000:
                    lines.append(f"```{code_block_lang}")
                    lines.append(final_content_str[:2000] + "\n... (truncated for transcript)")
                    lines.append("```")
                else:
                    lines.append(f"```{code_block_lang}")
                    lines.append(final_content_str)
                    lines.append("```")
            else:
                lines.append("*No result recorded.*")
            
            lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"**End of Transcript** | [Logfire Trace](https://logfire.pydantic.dev/Kjdragan/composio-claudemultiagent?q=trace_id%3D%27{trace_id}%27)")

    # Write to file
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return True
    except Exception as e:
        print(f"Failed to write transcript: {e}")
        return False
