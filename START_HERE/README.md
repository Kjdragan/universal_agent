# START_HERE

This folder centralizes entry points. **Use only the two recommended scripts below for normal work.**

## ✅ Recommended

### 1) Direct CLI (fastest dev loop)
**When to use:** quick iteration, debugging, prompt testing.
```bash
./start_cli_dev.sh
```

### 2) Full Stack (production‑like: Gateway + API + Web UI)
**When to use:** UI testing, production‑like behavior, shared engine for CLI + UI.
```bash
./start_gateway.sh
```

## Advanced (Diagnostics)

### 3) Multi‑terminal Gateway stack
**When to use:** you want live logs in separate terminals for Gateway/API/UI.
**Requires:** `gnome-terminal`.
```bash
./start_gateway_terminals.sh
```

## Menu Shortcut

Use the guided launcher:
```bash
./START.sh
```

## Deprecated Scripts

Legacy scripts have been renamed with `.deprecated`. The old filenames now just point you back here.

Examples:
- `start_ui.sh` → `start_ui.sh.deprecated`
- `start_terminal.sh` → `start_terminal.sh.deprecated`
- `start_local.sh` → `start_local.sh.deprecated`
- `start_gateway_terminals.sh` → `start_gateway_terminals.sh.deprecated`
