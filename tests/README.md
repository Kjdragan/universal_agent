# Universal Agent Test Suite

## 🛡️ The Safety Net (Start Here)

For most development, you only need to run the **Stabilization Suite**. This ensures that the Terminal Direct and Gateway modes are chemically stable.

```bash
# ⚡ Run Verification (Smoke Tests - <30s)
./run_verification.sh

# 🐢 Run Full Parity (Golden Tests - ~3m)
./run_verification.sh --full
```

## 📂 Directory Structure

We have organized the testing suite to separate "Contract/Stability" tests from "Implementation Details".

```text
tests/
├── stabilization/          # 🚨 CRITICAL: The primary safety net. verifying Direct vs Gateway parity.
│   ├── test_smoke_direct.py
│   └── test_smoke_gateway.py
│
├── gateway/                # Implementation details of the Gateway Server & Session Manager
├── durable/                # Durable State, Ledger, and Persistence logic
├── letta/                  # Letta Memory System integration
├── integration/            # End-to-End flows (e.g., Composio, Web UI, Full Workspace)
└── unit/                   # Helper functions and small components
```

## Running Component Tests

If you are working on a specific subsystem, run `pytest` targeted at that directory:

```bash
# Gateway Logic
pytest tests/gateway/ -v

# Durable Execution logic
pytest tests/durable/ -v

# Memory System
pytest tests/letta/ -v
```

## CI Integration

Our CI pipeline runs the **Stabilization Smoke Tests** on every commit to ensure no regression in the core startup loops.
