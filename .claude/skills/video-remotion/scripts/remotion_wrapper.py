import os
import subprocess
import json
import tempfile
import time
import argparse
from typing import Optional, Dict, Any, Union
from dataclasses import dataclass
from dotenv import load_dotenv

# Try importing the official SDK, but handle if it's not installed
try:
    from remotion_lambda import RenderMediaParams, RemotionClient, Privacy
    HAS_LAMBDA_SDK = True
except ImportError:
    HAS_LAMBDA_SDK = False

@dataclass
class RenderResult:
    success: bool
    output_path: Optional[str] = None
    error: Optional[str] = None
    render_id: Optional[str] = None
    bucket_name: Optional[str] = None

class RemotionWrapper:
    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        # Load env vars for Lambda if available
        load_dotenv(os.path.join(project_dir, ".env"))
        
    def render_local(
        self, 
        composition_id: str, 
        output_path: str, 
        props: Dict[str, Any], 
        codec: str = "h264",
        overwrite: bool = True
    ) -> RenderResult:
        """
        Render video locally using the Remotion CLI via subprocess.
        """
        # Create props file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(props, f)
            props_file = f.name
            
        try:
            cmd = [
                "npx", "remotion", "render",
                composition_id,
                output_path,
                f"--props={props_file}",
                f"--codec={codec}",
                "--log=verbose"
            ]
            
            if overwrite:
                cmd.append("--overwrite")

            print(f"Executing: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=self.project_dir
            )
            return RenderResult(success=True, output_path=output_path)
            
        except subprocess.CalledProcessError as e:
            return RenderResult(success=False, error=e.stderr or e.stdout)
        finally:
            if os.path.exists(props_file):
                os.unlink(props_file)

    def render_lambda(
        self,
        composition_id: str,
        props: Dict[str, Any],
        serve_url: Optional[str] = None,
        function_name: Optional[str] = None,
        region: Optional[str] = None,
        privacy: str = "public",
        download_to: Optional[str] = None
    ) -> RenderResult:
        """
        Render video using AWS Lambda via remotion-lambda Python SDK.
        """
        if not HAS_LAMBDA_SDK:
            return RenderResult(success=False, error="remotion-lambda package not installed.")

        # Use passed args or env vars
        region = region or os.getenv('REMOTION_APP_REGION')
        serve_url = serve_url or os.getenv('REMOTION_APP_SERVE_URL')
        function_name = function_name or os.getenv('REMOTION_APP_FUNCTION_NAME')

        if not (region and serve_url and function_name):
            return RenderResult(success=False, error="Missing Lambda configuration (Region, Serve URL, or Function Name).")

        client = RemotionClient(
            region=region,
            serve_url=serve_url,
            function_name=function_name
        )

        render_params = RenderMediaParams(
            composition=composition_id,
            privacy=Privacy.PUBLIC if privacy.lower() == "public" else Privacy.PRIVATE,
            input_props=props
        )

        try:
            print(f"Starting Lambda render for {composition_id}...")
            response = client.render_media_on_lambda(render_params)
            
            print(f"Render started. ID: {response.render_id}")
            
            # Polling
            while True:
                progress = client.get_render_progress(response.render_id, response.bucket_name)
                
                if progress.fatal_error_encountered:
                    return RenderResult(
                        success=False, 
                        error=f"Lambda Error: {progress.errors}",
                        render_id=response.render_id,
                        bucket_name=response.bucket_name
                    )
                
                if progress.done:
                    print(f"Render complete! URL: {progress.output_file}")
                    
                    if download_to:
                        # TODO: Implement download logic if needed, for now just return URL
                        pass
                        
                    return RenderResult(
                        success=True, 
                        output_path=progress.output_file,
                        render_id=response.render_id,
                        bucket_name=response.bucket_name
                    )
                
                p_val = progress.overall_progress * 100 if progress.overall_progress else 0
                print(f"Progress: {p_val:.1f}%")
                time.sleep(2)
                
        except Exception as e:
            return RenderResult(success=False, error=str(e))

def main():
    parser = argparse.ArgumentParser(description="Remotion Render Wrapper")
    parser.add_argument("--mode", choices=["local", "lambda"], default="local", help="Rendering mode")
    parser.add_argument("--project-dir", required=True, help="Path to Remotion project root")
    parser.add_argument("--composition", required=True, help="Composition ID")
    parser.add_argument("--output", help="Output file path (local only)")
    parser.add_argument("--props", help="JSON string or path to JSON file of props")
    parser.add_argument("--save-url", help="File to save the output URL/Path to")
    
    args = parser.parse_args()
    
    # Load Props
    props = {}
    if args.props:
        if os.path.exists(args.props):
            with open(args.props, 'r') as f:
                props = json.load(f)
        else:
            try:
                props = json.loads(args.props)
            except json.JSONDecodeError:
                print("Error: Props is neither a valid file path nor valid JSON string")
                exit(1)

    wrapper = RemotionWrapper(args.project_dir)
    
    if args.mode == "local":
        if not args.output:
            print("Error: --output is required for local mode")
            exit(1)
        result = wrapper.render_local(args.composition, args.output, props)
    else:
        result = wrapper.render_lambda(args.composition, props)

    if result.success:
        print(f"SUCCESS: {result.output_path}")
        if args.save_url and result.output_path:
             with open(args.save_url, 'w') as f:
                f.write(result.output_path)
    else:
        print(f"FAILURE: {result.error}")
        exit(1)

if __name__ == "__main__":
    main()
