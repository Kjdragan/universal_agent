with open('src/universal_agent/gateway_server.py', 'r') as f:
    lines = f.readlines()

# Convert to 0-indexed:
# _build_autonomous_daily_briefing_command: 23412 to 23425
# _autonomous_briefing_day_slug: 23428 to 23438
# _generate_autonomous_daily_briefing_artifact: 23713 to 23915
# _ensure_autonomous_daily_briefing_job: 24236 to 24301
# Call at 13466 to 13469

ranges_to_delete = [
    (13465, 13469), # This corresponds to lines 13466-13469 (0-indexed: 13465:13469 where 13469 is excluded)
    (23411, 23425),
    (23427, 23438),
    (23712, 23915),
    (24235, 24301)
]

# Sort desc to not mess up indices
ranges_to_delete.sort(reverse=True)
for start, end in ranges_to_delete:
    del lines[start:end]

with open('src/universal_agent/gateway_server.py', 'w') as f:
    f.writelines(lines)
print("Done")
