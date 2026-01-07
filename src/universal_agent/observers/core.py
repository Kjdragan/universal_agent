import asyncio
import json
import os
from datetime import datetime
from typing import Any

import logfire

from universal_agent.search_config import SEARCH_TOOL_CONFIG


async def observe_and_save_search_results(
    tool_name: str, content: Any, workspace_dir: str
) -> None:
    """
    Observer: Parse SERP tool results and save cleaned artifacts.
    Uses Claude SDK typed content (list of TextBlock objects).
    """
    with logfire.span("observer_search_results", tool=tool_name):
        # Match search tools but exclude tool discovery (SEARCH_TOOLS)
        tool_upper = tool_name.upper()

        # Exclude tool discovery - COMPOSIO_SEARCH_TOOLS searches for tools, not web
        if "SEARCH_TOOLS" in tool_upper:
            return

        # Comprehensive allowlist for Composio search providers only
        # Note: Native WebSearch excluded - we only want intentional Composio research
        # MULTI_EXECUTE included because it may wrap search calls - inner parsing filters by tool_slug
        search_keywords = [
            # Composio native search (SEARCH_TOOLS excluded above)
            "COMPOSIO_SEARCH",
            # SerpAPI variants
            "SERPAPI",
            "SERP_API",
            # Future providers
            "EXA_SEARCH",
            "EXA_",
            "TAVILY",
            "TAVILI",  # Common misspelling
            # Generic Composio patterns
            "SEARCH_NEWS",
            "SEARCH_WEB",
            "SEARCH_GOOGLE",
            "SEARCH_BING",
            # Wrapper that may contain search results - inner parsing filters by tool_slug
            "MULTI_EXECUTE",
            # Fallback WebSearch
            "WEBSEARCH",
            "WEB_SEARCH",
        ]
        is_serp_tool = any(kw in tool_upper for kw in search_keywords)

        if not is_serp_tool:
            return

        try:
            # Extract JSON text from Claude SDK TextBlock objects
            raw_json = None

            if isinstance(content, list):
                # Claude SDK: [TextBlock(type='text', text='<json>')]
                for item in content:
                    if hasattr(item, "text"):
                        raw_json = item.text
                        break
                    if isinstance(item, dict) and item.get("type") == "text":
                        raw_json = item.get("text", "")
                        break
            elif isinstance(content, str):
                raw_json = content

            if not raw_json:
                return

            # Special handling for Claude's native WebSearch format:
            # "Web search results for query: ...\n\nLinks: [{...}, {...}]"
            if "WebSearch" in tool_name or raw_json.startswith("Web search results"):
                import re

                links_match = re.search(r"Links:\s*(\[.*\])", raw_json, re.DOTALL)
                if links_match:
                    try:
                        links_list = json.loads(links_match.group(1))
                        # Convert to our standard format
                        data = {
                            "organic_results": [
                                {
                                    "title": item.get("title"),
                                    "link": item.get("url"),
                                    "snippet": item.get("snippet", ""),
                                }
                                for item in links_list
                                if isinstance(item, dict)
                            ]
                        }
                    except json.JSONDecodeError:
                        return
                else:
                    return
            else:
                # Parse JSON normally
                try:
                    data = json.loads(raw_json)
                except json.JSONDecodeError:
                    return

            if not isinstance(data, dict):
                return

            # Prepare list of payloads to process
            payloads = []

            # 1. Handle Nested "data" wrapper
            root = data
            if isinstance(root, dict) and "data" in root:
                root = root["data"]

            # 2. Check for MULTI_EXECUTE_TOOL structure
            if isinstance(root, dict) and "results" in root and isinstance(root["results"], list):
                # Multi-execute result
                for item in root["results"]:
                    if isinstance(item, dict) and "response" in item:
                        inner_resp = item["response"]

                        # Handle string response
                        if isinstance(inner_resp, str):
                            try:
                                inner_resp = json.loads(inner_resp)
                            except json.JSONDecodeError:
                                continue

                        # Now safe to process dict
                        if isinstance(inner_resp, dict):
                            inner_data = inner_resp.get("data") or inner_resp.get(
                                "data_preview"
                            )
                            inner_slug = item.get("tool_slug", tool_name)

                            if inner_data:
                                payloads.append((inner_slug, inner_data))

            else:
                # Single tool result
                payloads.append((tool_name, root))

            # 3. Process each payload
            saved_count = 0
            for slug, payload in payloads:
                if not isinstance(payload, dict):
                    continue

                # Helper to unwrap 'results' key if it hides the actual SERP data
                search_data = payload
                if "results" in payload and isinstance(payload["results"], dict):
                    search_data = payload["results"]

                # Robust extraction helper
                def safe_get_list(data, key):
                    val = data.get(key, [])
                    if isinstance(val, dict):
                        return list(val.values())
                    if isinstance(val, list):
                        return val
                    return []

                cleaned = None

                # ---------------------------------------------------------
                # DYNAMIC SCHEMA PARSING
                # Priority: Special formats FIRST, then config-driven fallback
                # ---------------------------------------------------------

                # PRIORITY 1: Special "Answer + Citations" format (COMPOSIO_SEARCH_WEB)
                if "answer" in search_data and "citations" in search_data:
                    citations = safe_get_list(search_data, "citations")
                    cleaned = {
                        "type": "web_answer",
                        "timestamp": datetime.now().isoformat(),
                        "tool": slug,
                        "answer": search_data.get("answer", ""),
                        "results": [
                            {
                                "position": idx + 1,
                                "title": c.get("source", c.get("id", "")),
                                "url": c.get("id", c.get("source", "")),
                                "snippet": c.get("snippet", ""),
                            }
                            for idx, c in enumerate(citations)
                            if isinstance(c, dict)
                        ],
                    }

                # PRIORITY 2: News Results (explicit news_results key)
                elif "news_results" in search_data:
                    raw_list = safe_get_list(search_data, "news_results")
                    cleaned = {
                        "type": "news",
                        "timestamp": datetime.now().isoformat(),
                        "tool": slug,
                        "articles": [
                            {
                                "position": idx + 1,
                                "title": a.get("title"),
                                "url": a.get("link"),
                                "source": (
                                    a.get("source", {}).get("name")
                                    if isinstance(a.get("source"), dict)
                                    else a.get("source")
                                ),
                                "snippet": a.get("snippet"),
                            }
                            for idx, a in enumerate(raw_list)
                            if isinstance(a, dict)
                        ],
                    }

                # PRIORITY 3: Organic Results (raw SERP format)
                elif "organic_results" in search_data:
                    raw_list = safe_get_list(search_data, "organic_results")
                    cleaned = {
                        "type": "web",
                        "timestamp": datetime.now().isoformat(),
                        "tool": slug,
                        "results": [
                            {
                                "position": r.get("position") or (idx + 1),
                                "title": r.get("title"),
                                "url": r.get("link"),
                                "snippet": r.get("snippet"),
                            }
                            for idx, r in enumerate(raw_list)
                        ],
                    }

                # PRIORITY 4: Config-driven parsing (Scholar, Amazon, Shopping, etc.)
                else:
                    config = SEARCH_TOOL_CONFIG.get(slug)
                    if config:
                        list_key = config["list_key"]
                        url_key = config["url_key"]
                        raw_list = safe_get_list(search_data, list_key)

                        if raw_list:
                            cleaned = {
                                "type": "search_result",
                                "timestamp": datetime.now().isoformat(),
                                "tool": slug,
                                config["list_key"]: [
                                    {
                                        "position": idx + 1,
                                        "title": item.get("title", f"Result {idx+1}"),
                                        "url": item.get(url_key),
                                        "snippet": item.get(
                                            "snippet", item.get("description", "")
                                        ),
                                        "source": item.get("source"),
                                    }
                                    for idx, item in enumerate(raw_list)
                                    if isinstance(item, dict)
                                ],
                            }

                # Save if we found cleanable data
                if cleaned and workspace_dir:
                    filename = "unknown"  # Initialize before try block
                    try:
                        search_dir = os.path.join(workspace_dir, "search_results")
                        os.makedirs(search_dir, exist_ok=True)

                        # Make filename unique
                        timestamp_str = datetime.now().strftime("%H%M%S")
                        suffix = f"_{saved_count}" if len(payloads) > 1 else ""
                        filename = os.path.join(
                            search_dir, f"{slug}{suffix}_{timestamp_str}.json"
                        )

                        # Write file with explicit error handling
                        with open(filename, "w") as f:
                            json.dump(cleaned, f, indent=2)

                        # Verify file was actually created
                        if os.path.exists(filename):
                            file_size = os.path.getsize(filename)
                            print(
                                f"\n📁 [OBSERVER] Saved: {filename} ({file_size} bytes)"
                            )
                            logfire.info(
                                "observer_artifact_saved",
                                path=filename,
                                type=cleaned.get("type"),
                                size=file_size,
                            )
                            saved_count += 1
                        else:
                            print(f"\n❌ [OBSERVER] File not created: {filename}")
                            logfire.error("observer_file_not_created", path=filename)

                    except Exception as file_error:
                        print(f"\n❌ [OBSERVER] File I/O error: {file_error}")
                        logfire.error(
                            "observer_file_io_error",
                            error=str(file_error),
                            path=filename if "filename" in locals() else "unknown",
                        )

            # === JIT DELEGATION GUIDE RAIL (console output for logging) ===
            if saved_count > 0:
                print(f"\n   ✅ {saved_count} Search Result File(s) Saved for Sub-Agent.")
                print(
                    "   ⚠️ Reminder: Delegate to 'report-creation-expert' for full analysis."
                )

        except Exception as exc:
            print(f"\n❌ [OBSERVER] Parse error: {exc}")
            logfire.warning("observer_error", tool=tool_name, error=str(exc))


async def observe_and_save_workbench_activity(
    tool_name: str, tool_input: dict, tool_result: str, workspace_dir: str
) -> None:
    """
    Observer: Capture COMPOSIO_REMOTE_WORKBENCH activity (inputs/outputs).
    Saves code execution details to workbench_activity/ directory.
    """
    with logfire.span("observer_workbench_activity", tool=tool_name):
        if "REMOTE_WORKBENCH" not in tool_name.upper():
            return

        try:
            workbench_dir = os.path.join(workspace_dir, "workbench_activity")
            os.makedirs(workbench_dir, exist_ok=True)

            timestamp_str = datetime.now().strftime("%H%M%S")
            filename = os.path.join(workbench_dir, f"workbench_{timestamp_str}.json")

            # Parse result for metadata
            result_data = {}
            try:
                if isinstance(tool_result, str):
                    import ast

                    parsed_list = ast.literal_eval(tool_result)
                    for item in parsed_list:
                        if isinstance(item, dict) and item.get("type") == "text":
                            result_json = json.loads(item.get("text", "{}"))
                            result_data = result_json.get("data", {})
                            break
            except (json.JSONDecodeError, ValueError, SyntaxError):
                result_data = {
                    "raw": tool_result[:500] if isinstance(tool_result, str) else ""
                }

            activity_log = {
                "timestamp": datetime.now().isoformat(),
                "tool": tool_name,
                "input": {
                    "code": tool_input.get("code_to_execute", "")[:1000],
                    "session_id": tool_input.get("session_id"),
                    "current_step": tool_input.get("current_step"),
                    "thought": tool_input.get("thought"),
                },
                "output": {
                    "stdout": result_data.get("stdout", ""),
                    "stderr": result_data.get("stderr", ""),
                    "results": result_data.get("results", ""),
                    "successful": result_data.get("successful"),
                },
            }

            with open(filename, "w") as f:
                json.dump(activity_log, f, indent=2)

            print(f"\n📁 [OBSERVER] Saved workbench activity: {filename}")
            logfire.info("workbench_activity_saved", path=filename)

        except Exception as exc:
            logfire.warning("workbench_observer_error", tool=tool_name, error=str(exc))


SAVED_REPORTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "SAVED_REPORTS",
)


async def observe_and_save_work_products(
    tool_name: str, tool_input: dict, tool_result: str, workspace_dir: str
) -> None:
    """
    Observer: Copy work product reports to persistent SAVED_REPORTS directory.
    This supplements the session workspace save - reports are saved to BOTH locations.
    """
    with logfire.span("observer_work_products", tool=tool_name):
        # Check for both native Write tool and legacy write_local_file
        tool_lower = tool_name.lower()
        is_write_tool = "write" in tool_lower and ("__write" in tool_lower or tool_lower.endswith("write"))
        if not is_write_tool:
            return

        # Only process if this is a work_products file
        file_path = tool_input.get("path", "") or tool_input.get("file_path", "")
        if "work_products" not in file_path:
            return

        try:
            # Ensure persistent directory exists
            os.makedirs(SAVED_REPORTS_DIR, exist_ok=True)

            # Extract original filename
            original_filename = os.path.basename(file_path)
            name_part, ext = os.path.splitext(original_filename)

            # Add timestamp for uniqueness
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            persistent_filename = f"{name_part}_{timestamp_str}{ext}"
            persistent_path = os.path.join(SAVED_REPORTS_DIR, persistent_filename)

            # Get the full path of the source file
            abs_workspace_dir = (
                os.path.abspath(workspace_dir)
                if not os.path.isabs(workspace_dir)
                else workspace_dir
            )
            # Normalize the path - handle both relative and absolute paths
            if os.path.isabs(file_path):
                source_path = file_path
            else:
                # Find the session directory from the path
                base_dir = os.path.dirname(os.path.dirname(abs_workspace_dir))
                source_path = os.path.join(base_dir, file_path)

            # Wait a moment for the file to be written
            await asyncio.sleep(0.5)

            # Copy the file
            if os.path.exists(source_path):
                import shutil

                shutil.copy2(source_path, persistent_path)
                print(f"\n📁 [OBSERVER] Saved to persistent: {persistent_path}")
                logfire.info(
                    "work_product_saved_persistent",
                    source=file_path,
                    dest=persistent_path,
                )
            else:
                logfire.warning("work_product_source_not_found", path=source_path)

        except Exception as exc:
            logfire.warning("work_product_observer_error", tool=tool_name, error=str(exc))


async def observe_and_save_video_outputs(
    tool_name: str, tool_input: dict, tool_result: str, workspace_dir: str
) -> None:
    """
    Observer: Copy video/audio outputs to session work_products directory.
    Triggered when video_audio or youtube MCP tools produce output files.
    """
    with logfire.span("observer_video_outputs", tool=tool_name):
        # Only process video_audio and youtube MCP output tools
        video_tools = [
            "trim_video",
            "concatenate_videos",
            "extract_audio",
            "convert_video",
            "add_text_overlay",
            "add_image_overlay",
            "reverse_video",
            "compress_video",
            "rotate_video",
            "change_video_speed",
            "download_video",
            "download_audio",
        ]

        if not any(tool in tool_name.lower() for tool in video_tools):
            return

        try:
            import shutil

            # Try to extract output path from result
            output_path = None

            # Check for output_video_path in input
            if "output_video_path" in tool_input:
                output_path = tool_input["output_video_path"]
            elif "output_audio_path" in tool_input:
                output_path = tool_input["output_audio_path"]
            elif "output_path" in tool_input:
                output_path = tool_input["output_path"]

            # Also try to extract from result message
            if not output_path and "successfully" in tool_result.lower():
                # Try to find path in result (e.g., "Videos concatenated successfully to /path/to/file.mp4")
                if " to " in tool_result:
                    potential_path = tool_result.split(" to ")[-1].strip().rstrip('"}')
                    if potential_path.endswith(
                        (".mp4", ".mp3", ".wav", ".webm", ".avi", ".mov")
                    ):
                        output_path = potential_path

            if not output_path or not os.path.exists(output_path):
                return

            # Check if this is a "final" output (not intermediate like last_15_seconds.mp4)
            filename = os.path.basename(output_path)
            intermediate_patterns = ["last_", "first_", "temp_", "tmp_", "_part"]
            if any(pat in filename.lower() for pat in intermediate_patterns):
                return  # Skip intermediate files

            # Create work_products/media directory in session workspace
            media_dir = os.path.join(workspace_dir, "work_products", "media")
            os.makedirs(media_dir, exist_ok=True)

            # Copy to session workspace
            dest_path = os.path.join(media_dir, filename)
            shutil.copy2(output_path, dest_path)
            print(f"\n🎬 [OBSERVER] Saved media to session: {dest_path}")
            logfire.info(
                "video_output_saved_session", source=output_path, dest=dest_path
            )

        except Exception as exc:
            logfire.warning("video_observer_error", tool=tool_name, error=str(exc))


def verify_subagent_compliance(
    tool_name: str, tool_content: str, workspace_dir: str
) -> str | None:
    """
    Verify that report-creation-expert sub-agent saved required artifacts.
    Returns an error message to inject if compliance failed, None if OK.
    """
    # Only check for Task (sub-agent) tool results
    if "task" not in tool_name.lower():
        return None

    # Check if this looks like a report sub-agent completion
    content_lower = tool_content.lower() if isinstance(tool_content, str) else ""
    is_report_task = any(
        keyword in content_lower
        for keyword in ["report", "comprehensive", "html", "work_products"]
    )

    if not is_report_task:
        return None

    # Check for Evidence of Research Data (search_results/*.md)
    search_results_dir = os.path.join(workspace_dir, "search_results")

    has_search_results = False
    if os.path.exists(search_results_dir):
        # Check if directory is not empty
        if any(os.scandir(search_results_dir)):
            has_search_results = True

    if has_search_results:
        return None  # Compliant: Data preserved

    # Conditional Check: Did we promise a "Comprehensive" report?
    # If the output claims "Comprehensive" or "Deep Dive", we EXPECT data to allow audit.
    # If it's just a summary, allow skipping extraction.
    is_claimed_comprehensive = any(
        keyword in content_lower
        for keyword in ["comprehensive", "deep dive", "full analysis", "detailed report"]
    )

    if is_claimed_comprehensive:
        logfire.warning(
            "subagent_compliance_failed",
            reason="comprehensive_report_without_data",
            workspace=workspace_dir,
        )
        return (
            "\n\n❌ **COMPLIANCE ERROR**: The report claimed to be 'Comprehensive' but no "
            "raw research data (search_results) was saved. \n"
            "**Rule**: For comprehensive reports, you MUST Use `crawl_parallel` to extract and preserve source data.\n"
            "If this was a simple summary, do not label it as 'Comprehensive'."
        )

    return None
