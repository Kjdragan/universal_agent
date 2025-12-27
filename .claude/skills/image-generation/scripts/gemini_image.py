#!/usr/bin/env python3
"""
Standalone script for generating images using Gemini 2.5 Flash Image.
Useful for testing or CLI usage outside the MCP environment.
"""

import os
import sys
import argparse
from datetime import datetime
from google import genai
from google.genai.types import GenerateContentConfig, Part
from PIL import Image
import base64
from io import BytesIO

def generate_image(prompt, input_image_path=None, output_dir="output", output_filename=None, model_name="gemini-2.5-flash-image"):
    """Generate or edit an image using the Gemini API."""
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not set in environment")
        return None

    client = genai.Client(api_key=api_key)
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Prepare contents
    parts = []
    
    # If editing, include the input image
    if input_image_path:
        if not os.path.exists(input_image_path):
            print(f"Error: Input image not found: {input_image_path}")
            return None
        
        with open(input_image_path, "rb") as img_file:
            img_bytes = img_file.read()
            parts.append(Part.from_bytes(data=img_bytes, mime_type="image/png"))
    
    # Add text prompt
    parts.append(types.Part.from_text(text=prompt))
    
    contents = [
        types.Content(
            role="user",
            parts=parts
        )
    ]
    
    print(f"Generating image using model: {model_name}...")
    print(f"Prompt: '{prompt}'")

    try:
        # Use streaming as per working example
        response_stream = client.models.generate_content_stream(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            )
        )
        
        saved_path = None
        
        for chunk in response_stream:
            if (chunk.candidates is None or 
                chunk.candidates[0].content is None or 
                chunk.candidates[0].content.parts is None):
                continue
                
            for part in chunk.candidates[0].content.parts:
                # Check for inline_data (image)
                if part.inline_data and part.inline_data.data:
                    # We found an image!
                    if not output_filename:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        output_filename = f"generated_{timestamp}.png"
                    
                    saved_path = os.path.join(output_dir, output_filename)
                    
                    with open(saved_path, "wb") as f:
                        f.write(part.inline_data.data)
                        
                    print(f"✅ Success! Image saved to: {saved_path}")
                elif part.text:
                    print(f"Text output: {part.text}")
        
        if not saved_path:
            print("Warning: No image data received in stream")
            
        return saved_path

    except Exception as e:
        print(f"Error generating image with {model_name}: {e}")
        return None

if __name__ == "__main__":
    from google.genai import types  # Import types explicitly inside main/function scope if needed
    
    parser = argparse.ArgumentParser(description="Generate images with Gemini 2.5 Flash Image")
    parser.add_argument("prompt", help="Text prompt for generation")
    parser.add_argument("--input", "-i", help="Path to input image for editing")
    parser.add_argument("--outdir", "-o", default=".", help="Output directory")
    parser.add_argument("--name", "-n", help="Output filename")
    parser.add_argument("--model", "-m", default="gemini-2.5-flash-image", help="Model version to use")
    
    args = parser.parse_args()
    
    generate_image(args.prompt, args.input, args.outdir, args.name, args.model)
