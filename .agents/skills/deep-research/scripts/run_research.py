# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-genai",
# ]
# ///

import argparse
import base64
from datetime import datetime
import os
import time

from google import genai
from google.genai.errors import APIError


def main():
    try:
        from pathlib import Path
        import sys
        repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        sys.path.append(str(repo_root / "src"))
        from universal_agent import infisical_loader
        infisical_loader.initialize_runtime_secrets()
    except Exception as e:
        print(f"Failed to load infisical secrets: {e}")

    parser = argparse.ArgumentParser(description="Run Gemini Deep Research.")
    parser.add_argument("--prompt", required=True, help="The research prompt.")
    parser.add_argument("--output-dir", required=True, help="Directory to save outputs.")
    parser.add_argument("--max", action="store_true", help="Use the deep-research-max agent.")
    args = parser.parse_args()

    agent_id = "deep-research-max-preview-04-2026" if args.max else "deep-research-preview-04-2026"
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(args.output_dir, f"deep_research_{timestamp}")
    os.makedirs(run_dir, exist_ok=True)
    
    log_file = os.path.join(run_dir, "research_log.txt")
    report_file = os.path.join(run_dir, "report.md")

    print(f"Starting deep research...")
    print(f"Agent: {agent_id}")
    print(f"Output directory: {run_dir}")
    print(f"Prompt: {args.prompt}")

    client = genai.Client() # Assumes GEMINI_API_KEY is in env

    interaction_id = None
    last_event_id = None
    is_complete = False

    # Variables to accumulate the final report text
    final_text_chunks = []

    def process_stream(stream):
        nonlocal interaction_id, last_event_id, is_complete
        
        with open(log_file, "a", encoding="utf-8") as f_log:
            for chunk in stream:
                if getattr(chunk, 'event_type', None) == "interaction.start":
                    interaction_id = chunk.interaction.id
                    msg = f"--- Interaction Started: {interaction_id} ---\n"
                    print(msg, end="")
                    f_log.write(msg)
                    
                if getattr(chunk, 'event_id', None):
                    last_event_id = chunk.event_id
                    
                if getattr(chunk, 'event_type', None) == "content.delta":
                    delta = chunk.delta
                    if delta.type == "text":
                        print(delta.text, end="", flush=True)
                        f_log.write(delta.text)
                        final_text_chunks.append(delta.text)
                    elif delta.type == "thought_summary":
                        thought_msg = f"\n[Thought]: {delta.content.text}\n"
                        print(thought_msg, end="", flush=True)
                        f_log.write(thought_msg)
                    elif delta.type == "image":
                        # Save the image
                        try:
                            image_bytes = base64.b64decode(delta.data)
                            image_path = os.path.join(run_dir, f"image_{last_event_id}.png")
                            with open(image_path, "wb") as img_f:
                                img_f.write(image_bytes)
                            img_msg = f"\n[Image Generated]: Saved to {image_path}\n"
                            print(img_msg, end="", flush=True)
                            f_log.write(img_msg)
                        except Exception as e:
                            err_msg = f"\n[Error saving image]: {e}\n"
                            print(err_msg, end="", flush=True)
                            f_log.write(err_msg)
                
                elif getattr(chunk, 'event_type', None) in ("interaction.complete", "error"):
                    is_complete = True
                    msg = f"\n--- Interaction Finished (Status: {chunk.event_type}) ---\n"
                    print(msg, end="")
                    f_log.write(msg)

    try:
        stream = client.interactions.create(
            input=args.prompt,
            agent=agent_id,
            background=True,
            stream=True,
            agent_config={"type": "deep-research", "thinking_summaries": "auto", "visualization": "auto"},
        )
        process_stream(stream)
    except Exception as e:
        print(f"Error starting interaction: {e}")
        return

    # Reconnect if the connection drops
    retry_count = 0
    max_retries = 5
    while not is_complete and interaction_id and retry_count < max_retries:
        try:
            print(f"\nChecking interaction status for {interaction_id}...")
            status = client.interactions.get(interaction_id)
            if status.status != "in_progress":
                print(f"Interaction status is now {status.status}. Stopping.")
                break
            
            print(f"Reconnecting to stream from event {last_event_id}...")
            resume_stream = client.interactions.get(
                id=interaction_id, stream=True, last_event_id=last_event_id,
            )
            process_stream(resume_stream)
            retry_count = 0 # reset retries on successful connection
        except Exception as e:
            print(f"\nError reconnecting: {e}. Retrying in 10s...")
            retry_count += 1
            time.sleep(10)

    if final_text_chunks:
        with open(report_file, "w", encoding="utf-8") as f_report:
            f_report.write("".join(final_text_chunks))
        print(f"\nReport successfully saved to {report_file}")
    else:
        print("\nNo report text was generated.")

if __name__ == "__main__":
    main()
