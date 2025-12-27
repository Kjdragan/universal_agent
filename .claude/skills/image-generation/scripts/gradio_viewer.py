#!/usr/bin/env python3
"""
Gradio Image Viewer for Universal Agent
Displays images with editing capabilities.
"""

import sys
import os
import gradio as gr
from PIL import Image

def view_image(image_path, port=7860):
    """Launch Gradio viewer for the specified image."""
    if not os.path.exists(image_path):
        print(f"Error: Image not found at {image_path}")
        return
    
    # Load the image
    image = Image.open(image_path)
    
    # Create Gradio interface
    with gr.Blocks(title="Universal Agent Image Viewer") as demo:
        gr.Markdown(f"# Image Viewer\n**File**: `{os.path.basename(image_path)}`")
        
        with gr.Row():
            with gr.Column(scale=2):
                img_display = gr.Image(value=image, label="Current Image", type="pil")
            
            with gr.Column(scale=1):
                gr.Markdown("### Image Details")
                gr.Textbox(value=f"Path: {image_path}", label="Location", interactive=False)
                gr.Textbox(value=f"{image.width} x {image.height}", label="Dimensions", interactive=False)
                gr.Textbox(value=image.mode, label="Mode", interactive=False)
                gr.Textbox(value=f"{os.path.getsize(image_path)} bytes", label="Size", interactive=False)
    
    # Launch the interface
    print(f"\n✅ Gradio viewer launched at http://127.0.0.1:{port}")
    print(f"   Viewing: {image_path}")
    print(f"   Press Ctrl+C to stop the viewer\n")
    
    demo.launch(server_name="127.0.0.1", server_port=port, share=False, quiet=True)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: gradio_viewer.py <image_path> [port]")
        sys.exit(1)
    
    image_path = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 7860
    
    view_image(image_path, port)
