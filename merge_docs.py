import re

with open('docs/Documentation_Status.md', 'r') as f:
    content = f.read()

head_match = re.search(r'<<<<<<< HEAD\n(.*?)\n=======\n(.*?)\n>>>>>>> feature/latest2\n', content, re.DOTALL)

if head_match:
    head_content = head_match.group(1).strip()
    feat_content = head_match.group(2).strip()
    
    # feat_content starts with: **Last updated:** 2026-05-08 (ship-workflow hardening rollout test marker... Earlier 2026-05-08 entry: ...
    # head_content starts with: **Last updated:** 2026-05-08 (post-deploy doc cleanup...
    
    # We'll extract the new entry from feat_content
    feat_part = feat_content.split('Earlier 2026-05-08 entry:')[0].strip()
    
    head_part = head_content.replace('**Last updated:** 2026-05-08 (', 'Earlier 2026-05-08 entry: ')
    
    merged = f"{feat_part} {head_part}\n"
    
    content = content.replace(head_match.group(0), merged)
    
    with open('docs/Documentation_Status.md', 'w') as f:
        f.write(content)
    print("Merged successfully!")
else:
    print("No conflict markers found!")

