# 020 - Startup Scripts Guide (Simplified)

> **Recommended entry points:** use only the two scripts below for day‑to‑day work.

---

## ✅ Recommended Scripts

### 1) `./start_cli_dev.sh` — Direct CLI (fastest dev loop)
Use this for quick iteration and debugging.

```bash
./start_cli_dev.sh
```

### 2) `./start_gateway.sh` — Full Stack (production‑like)
Starts the shared execution engine plus API + Web UI.

```bash
./start_gateway.sh
```

---

## When to Use Which

- **Fast dev / debugging:** `./start_cli_dev.sh`
- **Production‑like stack / UI testing:** `./start_gateway.sh`

---

## Deprecated Scripts

Legacy scripts have been renamed with a `.deprecated` suffix and replaced by small wrappers that point back to the two recommended entry points. If you see one of these, use the recommended scripts above instead.

Examples:
- `start_ui.sh` → `start_ui.sh.deprecated`
- `start_terminal.sh` → `start_terminal.sh.deprecated`
- `start_local.sh` → `start_local.sh.deprecated`
- `start_gateway_terminals.sh` → `start_gateway_terminals.sh.deprecated`
