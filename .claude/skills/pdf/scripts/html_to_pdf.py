import sys
import subprocess
import os
import argparse

def html_to_pdf(input_html, output_pdf, chrome_binary="/usr/bin/google-chrome"):
    """
    Convert an HTML file to PDF using Headless Chrome.
    
    Args:
        input_html (str): Path to input HTML file.
        output_pdf (str): Path to output PDF file.
        chrome_binary (str): Path to Chrome/Chromium binary.
    """
    input_path = os.path.abspath(input_html)
    output_path = os.path.abspath(output_pdf)
    
    if not os.path.exists(input_path):
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    cmd = [
        chrome_binary,
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={output_path}",
        f"file://{input_path}"  # Use file:// protocol locally
    ]
    
    print(f"Executing: {' '.join(cmd)}")
    
    try:
        # Run process
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Error converting PDF: {result.stderr}")
            sys.exit(result.returncode)
            
        print(f"Success: PDF generated at {output_path}")
        
    except FileNotFoundError:
        print(f"Error: Chrome binary not found at {chrome_binary}. Please install Google Chrome or Chromium.")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert HTML to PDF using Headless Chrome")
    parser.add_argument("input_html", help="Path to input HTML file")
    parser.add_argument("output_pdf", help="Path to output PDF file")
    parser.add_argument("--chrome", default="/usr/bin/google-chrome", help="Path to Chrome binary")
    
    args = parser.parse_args()
    
    html_to_pdf(args.input_html, args.output_pdf, args.chrome)
