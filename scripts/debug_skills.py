
import os
import yaml
import shutil
import glob

# Mock logic from prompt_assets.py
def check_requirements(frontmatter):
    try:
        clawdbot_meta = frontmatter.get("metadata", {}).get("clawdbot", {})
        requires = clawdbot_meta.get("requires", {})
        
        bins = requires.get("bins", [])
        for binary in bins:
            if not shutil.which(binary):
                return False, f"Missing binary: {binary}"

        any_bins = requires.get("anyBins", [])
        if any_bins:
            if not any(shutil.which(b) for b in any_bins):
                return False, f"Missing any of: {any_bins}"

        return True, "OK"
    except Exception as e:
        return True, f"Error checking requirements (defaulting to True): {e}"

def scan_skills():
    project_root = os.getcwd() # Run from repo root
    skills_dir = os.path.join(project_root, ".claude", "skills")
    
    print(f"Scanning skills in: {skills_dir}")
    if not os.path.exists(skills_dir):
        print("Skills dir not found!")
        return

    processed = []
    
    for skill_name in sorted(os.listdir(skills_dir)):
        skill_path = os.path.join(skills_dir, skill_name)
        if not os.path.isdir(skill_path):
            continue
            
        print(f"\n--- Checking: {skill_name} ---")
        skill_md = os.path.join(skill_path, "SKILL.md")
        
        if not os.path.exists(skill_md):
             print(f"❌ SKIPPED: SKILL.md not found in {skill_path}")
             continue
             
        try:
            with open(skill_md, "r", encoding="utf-8") as f:
                content = f.read()
                
            if not content.startswith("---"):
                print("❌ SKIPPED: Does not start with '---' (Invalid Frontmatter)")
                print(f"First 50 chars: {repr(content[:50])}")
                continue
                
            parts = content.split("---", 2)
            if len(parts) < 3:
                print("❌ SKIPPED: YAML frontmatter not terminated correctly")
                continue
                
            try:
                frontmatter = yaml.safe_load(parts[1])
            except yaml.YAMLError as e:
                print(f"❌ SKIPPED: YAML parsing error: {e}")
                continue
                
            if not frontmatter or not isinstance(frontmatter, dict):
                 print("❌ SKIPPED: Empty or invalid YAML frontmatter")
                 continue
                 
            # Gating Check
            is_avail, reason = check_requirements(frontmatter)
            if not is_avail:
                print(f"🔒 GATED: {reason}")
                meta = frontmatter.get("metadata", {}).get("clawdbot", {}).get("requires", {})
                print(f"   Requirements: {meta}")
            else:
                name = frontmatter.get("name", skill_name)
                print(f"✅ ACCEPTED as '{name}'")
                processed.append(name)

        except Exception as e:
            print(f"❌ ERROR: {e}")

    print("\n\n=== SUMMARY ===")
    print(f"Total processed: {len(processed)}")
    print(f"List: {processed}")

if __name__ == "__main__":
    scan_skills()
