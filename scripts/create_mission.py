#!/usr/bin/env python3
import json
import os
import argparse
import uuid

def create_mission_manifest(mission_root, tasks, output_dir="."):
    """
    Creates a mission.json file strictly following the Anthropic-like harness pattern.
    """
    manifest = {
        "mission_id": str(uuid.uuid4()),
        "mission_root": mission_root,
        "status": "IN_PROGRESS",
        "tasks": []
    }
    
    for idx, t_desc in enumerate(tasks, 1):
        task_obj = {
            "id": str(idx),
            "description": t_desc,
            "status": "PENDING",
            "steps": [], # Agent populates this
            "artifacts": [] # Agent populates this
        }
        manifest["tasks"].append(task_obj)
        
    output_path = os.path.join(output_dir, "mission.json")
    with open(output_path, "w") as f:
        json.dump(manifest, f, indent=2)
        
    print(f"✅ Created Mission Manifest at: {output_path}")
    print(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a mission.json manifest for the Harness")
    parser.add_argument("--objective", required=True, help="The high level mission objective")
    parser.add_argument("--tasks", nargs="+", required=True, help="List of sub-tasks to complete")
    parser.add_argument("--output", default=".", help="Directory to save mission.json")
    
    args = parser.parse_args()
    
    create_mission_manifest(args.objective, args.tasks, args.output)
