# Import Notes Reference

Import notes from other apps into Obsidian.

---

## Import Methods

### Official Import Plugins

Install via Settings → Community plugins → Browse.

| Source | Plugin | What's Preserved |
|--------|--------|------------------|
| Notion | Notion Import | Pages, databases, properties |
| Evernote | Evernote Import | Notes, tags, notebooks |
| Roam Research | Roam Research Import | Blocks, links, attributes |
| Bear | Bear Import | Notes, tags, attachments |
| Apple Notes | Apple Notes Import | Notes, folders, images |
| OneNote | OneNote Import | Notes, sections, images |
| Google Keep | Google Keep Import | Notes, labels, images |
| Craft | Craft Import | Documents, blocks |

---

## Notion

### Method: Official Plugin

1. Export from Notion: Settings → Export → Markdown & CSV
2. Download and extract the ZIP
3. Install "Notion Import" plugin in Obsidian
4. Settings → Notion Import → Select export folder
5. Run import

### What's Preserved

- ✅ Page content (Markdown)
- ✅ Properties (frontmatter)
- ✅ Linked databases (as tables)
- ⚠️ Relations (as links)
- ❌ Formulas
- ❌ Views (table, board, etc.)

### Post-Import

- Check database relations converted to wikilinks
- Verify image attachments
- Review property types

---

## Evernote

### Method: Official Plugin

1. Export from Evernote: File → Export as ENEX
2. Install "Evernote Import" plugin
3. Settings → Evernote Import → Select ENEX file
4. Run import

### What's Preserved

- ✅ Note content
- ✅ Tags
- ✅ Notebooks (as folders)
- ✅ Images and attachments
- ✅ Created/modified dates
- ⚠️ Formatting (partial)
- ❌ Stacks (nested notebooks)

---

## Roam Research

### Method: Official Plugin

1. Export from Roam: ... → Export All → JSON
2. Install "Roam Research Import" plugin
3. Settings → Roam Import → Select JSON file
4. Run import

### What's Preserved

- ✅ Pages (as notes)
- ✅ Blocks
- ✅ Wikilinks
- ✅ Block references
- ✅ Tags
- ⚠️ Datalog queries (converted)
- ❌ Graph views

### Conversion Notes

- Roam `[[links]]` → Obsidian `[[links]]`
- Roam `#tags` → Obsidian `#tags`
- Block references → Embedded blocks

---

## Bear

### Method: Official Plugin

1. Export from Bear: File → Export Notes → Markdown
2. Install "Bear Import" plugin
3. Settings → Bear Import → Select export folder
4. Run import

### What's Preserved

- ✅ Note content
- ✅ Tags (Bear `#tag` format)
- ✅ Images
- ✅ File attachments
- ✅ Creation dates

---

## Apple Notes

### Method: Official Plugin

1. Install "Apple Notes Import" plugin
2. Settings → Apple Notes Import
3. Authorize access to Notes app
4. Select notes to import
5. Run import

### What's Preserved

- ✅ Note content
- ✅ Folders
- ✅ Images
- ✅ Checklists
- ⚠️ Tables (partial)
- ❌ Drawing/sketches

---

## Microsoft OneNote

### Method: Official Plugin

1. Export from OneNote (or use plugin directly)
2. Install "OneNote Import" plugin
3. Connect to Microsoft account
4. Select notebooks to import
5. Run import

### What's Preserved

- ✅ Page content
- ✅ Sections (as folders)
- ✅ Images
- ⚠️ Tables
- ⚠️ Formatting
- ❌ Embedded files

---

## Google Keep

### Method: Official Plugin

1. Export via Google Takeout: takeout.google.com
2. Select "Keep" data
3. Download and extract
4. Install "Google Keep Import" plugin
5. Settings → Google Keep Import → Select export folder
6. Run import

### What's Preserved

- ✅ Note content
- ✅ Labels (as tags)
- ✅ Images
- ✅ Checklists
- ✅ Colors (as frontmatter)
- ⚠️ Reminders (as frontmatter)

---

## Craft

### Method: Official Plugin

1. Export from Craft: Settings → Export → Markdown
2. Install "Craft Import" plugin
3. Settings → Craft Import → Select export folder
4. Run import

### What's Preserved

- ✅ Document content
- ✅ Blocks
- ✅ Images
- ⚠️ Formatting
- ❌ Craft-specific features

---

## Markdown Files

### Method: Copy to Vault

1. Open vault folder in file manager
2. Copy `.md` files into vault
3. Obsidian indexes automatically

### Considerations

- Verify wikilink format matches
- Check frontmatter compatibility
- Move attachments to vault Attachments folder
- Update image paths if needed

---

## HTML Files

### Method: Import Plugin

1. Install "HTML Import" plugin
2. Settings → HTML Import → Select files/folder
3. Configure conversion options
4. Run import

### What's Preserved

- ✅ Text content
- ✅ Basic formatting
- ⚠️ Links (converted to Markdown)
- ⚠️ Images (downloaded)
- ❌ JavaScript
- ❌ CSS styling

---

## CSV Files

### Method: Manual or Plugin

1. Install "CSV Import" plugin (or handle manually)
2. Each row becomes a note
3. Columns become properties

### Example

```csv
title,status,priority
Project A,active,high
Project B,done,low
```

Creates notes with:
```yaml
---
title: Project A
status: active
priority: high
---
```

---

## Textbundle

### Method: Copy to Vault

1. Extract `.textbundle` (it's a folder)
2. Copy `text.md` to vault
3. Move images to Attachments
4. Update image paths

---

## Apple Journal

### Method: Export + Import

1. Export from Apple Journal app
2. Convert to Markdown (may need intermediate tool)
3. Import as Markdown files

---

## General Import Tips

### Before Import

1. **Backup vault** if not empty
2. **Clean source data** — remove duplicates, fix formatting
3. **Plan folder structure** — decide where imported notes go
4. **Test with subset** — import a few notes first

### During Import

1. **Monitor errors** — check import log
2. **Verify attachments** — ensure images imported
3. **Check links** — verify wikilinks resolve

### After Import

1. **Fix broken links** — use "Find unlinked mentions"
2. **Standardize properties** — ensure consistent frontmatter
3. **Add tags** — organize imported notes
4. **Create MOC** — index imported content
5. **Archive originals** — keep source files as backup

---

## Troubleshooting

### Missing Images

- Check attachment folder path
- Verify image references use correct format
- Move images to vault Attachments folder

### Broken Links

- Run "Find unlinked mentions" for each note
- Check for encoding issues in filenames
- Verify source used same link format

### Encoding Issues

- Ensure source files are UTF-8
- Check for special characters in filenames
- May need to batch rename files

### Large Imports

- Import in batches
- Disable sync during import
- Check available disk space
