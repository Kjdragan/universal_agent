#!/usr/bin/env python3
"""
Generate 5 random passwords and save them to a file.
"""

import random
import string

def generate_password(length=12, use_symbols=True, use_numbers=True):
    """Generate a random password."""
    chars = string.ascii_letters
    if use_numbers:
        chars += string.digits
    if use_symbols:
        chars += "!@#$%^&*()_+-=[]{}|;:,.<>?"

    return ''.join(random.choice(chars) for _ in range(length))

def main():
    # Generate 5 random passwords with different lengths for variety
    passwords = [
        generate_password(length=12),
        generate_password(length=16),
        generate_password(length=14),
        generate_password(length=12),
        generate_password(length=16)
    ]

    # Save passwords to file
    output_path = "/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/passwords.txt"
    with open(output_path, 'w') as f:
        f.write("Generated Passwords\n")
        f.write("=" * 50 + "\n\n")
        for i, pwd in enumerate(passwords, 1):
            f.write(f"Password {i}: {pwd}\n")

    print(f"✓ Successfully generated 5 random passwords")
    print(f"✓ Saved to: {output_path}")

if __name__ == "__main__":
    main()
