# Extending Obsidian Reference

Plugins, themes, CSS snippets, and automation.

---

## Community Plugins

### Installing Plugins

1. Settings → Community plugins
2. Turn off "Restricted mode" (first time only)
3. Click "Browse" to search plugins
4. Click "Install" on desired plugin
5. Click "Enable" to activate

### Managing Plugins

| Action | Location |
|--------|----------|
| Enable/Disable | Settings → Community plugins |
| Update | Click notification or "Check for updates" |
| Configure | Settings → [Plugin name] |
| Uninstall | Settings → Community plugins → [Plugin] → Uninstall |

### Plugin Security

- **Restricted mode:** Disables all community plugins
- Plugins can access files and network
- Review plugin permissions before enabling
- Only install from official directory

### Plugin Directory

Location: `.obsidian/plugins/[plugin-id]/`

```
.obsidian/
└── plugins/
    └── dataview/
        ├── main.js      # Plugin code
        ├── manifest.json
        ├── styles.css   # Plugin styles
        └── data.json    # User settings
```

---

## Themes

### Installing Themes

1. Settings → Appearance → Themes
2. Click "Manage" to browse
3. Select and apply theme

### Light/Dark Variants

- Some themes have both variants
- Toggle in Settings → Appearance
- Theme may auto-switch with system

### Theme Directory

```
.obsidian/
└── themes/
    └── Theme Name/
        └── obsidian.css
```

---

## CSS Snippets

### Creating Snippets

1. Create file in `.obsidian/snippets/my-snippet.css`
2. Settings → Appearance → CSS snippets
3. Enable the snippet

### Common Snippets

**Wider reading view:**
```css
/* Wider content area */
.markdown-source-view,
.markdown-reading-view {
  max-width: 900px;
}
```

**Custom callout colors:**
```css
/* Custom callout */
.callout[data-callout="custom"] {
  --callout-color: 255, 100, 50;
  --callout-icon: lucide-star;
}
```

**Hide elements:**
```css
/* Hide specific UI */
.workspace-ribbon,
.status-bar {
  display: none;
}
```

**Font changes:**
```css
/* Custom fonts */
body {
  --font-text: 'Custom Font', sans-serif;
  --font-monospace: 'JetBrains Mono', monospace;
}
```

**Image sizing:**
```css
/* Consistent image width */
.markdown-preview-view img {
  max-width: 100%;
  border-radius: 8px;
}
```

### CSS Variables

Common Obsidian CSS variables:

```css
body {
  /* Background */
  --background-primary: #ffffff;
  --background-secondary: #f0f0f0;

  /* Text */
  --text-normal: #333333;
  --text-muted: #888888;
  --text-accent: #7c3aed;

  /* Interactive */
  --interactive-accent: #7c3aed;
  --interactive-hover: #e0e0e0;

  /* Spacing */
  --size-4-4: 16px;
  --size-4-8: 32px;
}
```

---

## Obsidian URI

Protocol for deep linking and automation.

### Format

```
obsidian://action?param1=value1&param2=value2
```

### Actions

**Open vault or file:**
```
obsidian://open?vault=MyVault
obsidian://open?vault=MyVault&file=MyNote
obsidian://open?vault=MyVault&file=Folder%2FNote
```

**Create new note:**
```
obsidian://new?vault=MyVault&name=NewNote
obsidian://new?vault=MyVault&name=NewNote&content=Hello%20World
obsidian://new?vault=MyVault&name=NewNote&path=Folder
```

**Search:**
```
obsidian://search?vault=MyVault&query=searchterm
obsidian://search?vault=MyVault&query=tag%3Aproject
```

**Hook integration:**
```
obsidian://hook-get-address?vault=MyVault
```

### Encoding

URL-encode special characters:
- Space: `%20`
- `/`: `%2F`
- `#`: `%23`
- `&`: `%26`

### Use Cases

- Deep links from other apps
- Browser bookmarks to notes
- Script triggers
- Integration with launchers (Alfred, Raycast)

---

## Obsidian CLI

Command-line interface for automation.

### Installation

```bash
# npm
npm install -g obsidian-cli

# brew
brew install obsidian-cli
```

### Commands

```bash
# Open note
obsidian open "Note Name"

# Create note
obsidian create "New Note" --content "Content"

# Search
obsidian search "query"

# Append to note
obsidian append "Daily Note" --content "- New item"
```

See `obsidian-cli` skill for complete command reference.

---

## Obsidian Headless

Sync vaults from command line without desktop app.

### Use Cases

- Server-side sync
- Automated backup
- CI/CD integration
- Scheduled vault operations

---

## Plugin Development

### Development Setup

1. Create plugin folder: `.obsidian/plugins/my-plugin/`
2. Create `manifest.json`:
```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "minAppVersion": "1.0.0",
  "description": "Plugin description",
  "author": "Your Name",
  "authorUrl": "https://example.com"
}
```

3. Create `main.js` with plugin code
4. Enable in Settings → Community plugins

### Hot Reload

Use the Hot Reload plugin for development:
- Automatically reloads plugin on file change
- Speeds up development cycle

### Debugging

1. Open Developer Tools: `Ctrl/Cmd + Shift + I`
2. Check Console for errors
3. Use `console.log()` for debugging

---

## Integration Examples

### With Launchers (Alfred/Raycast)

```bash
# Alfred workflow
obsidian://open?vault=MyVault&file={query}
```

### With Shortcuts (macOS)

Create shortcut with URL action:
```
obsidian://new?vault=MyVault&name={Shortcut Input}&content={Shortcut Input}
```

### With Python

```python
import webbrowser
import urllib.parse

def open_note(vault, note):
    encoded = urllib.parse.quote(note)
    url = f"obsidian://open?vault={vault}&file={encoded}"
    webbrowser.open(url)
```

### With Bash

```bash
#!/bin/bash
VAULT="MyVault"
NOTE="$1"

# URL encode note name
ENCODED=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$NOTE'))")

# Open in Obsidian
xdg-open "obsidian://open?vault=$VAULT&file=$ENCODED"
```
