# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "google-genai",
# ]
# ///

import argparse
import os

from google import genai


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

    parser = argparse.ArgumentParser(description="Run baseline standard Gemini.")
    parser.add_argument("--prompt", required=True, help="The research prompt.")
    parser.add_argument("--output-dir", required=True, help="Directory to save outputs.")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    report_file = os.path.join(args.output_dir, "report.md")

    print(f"Starting baseline generation...")
    print(f"Output directory: {args.output_dir}")
    print(f"Prompt: {args.prompt}")

    client = genai.Client() # Assumes GEMINI_API_KEY is in env
    
    # Use standard generate_content (gemini-3.1-pro)
    # Enable search grounding for a fairer comparison
    response = client.models.generate_content(
        model="gemini-3.1-pro",
        contents=args.prompt,
        config={"tools": [{"google_search": {}}]}
    )

    with open(report_file, "w", encoding="utf-8") as f_report:
        f_report.write(response.text)
    
    print(f"\nReport successfully saved to {report_file}")

if __name__ == "__main__":
    main()
