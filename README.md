# Universal Agent

## Local development

```bash
./scripts/dev_up.sh     # start local stack → http://localhost:3000
./scripts/dev_down.sh   # stop local stack
./scripts/dev_status.sh # check what's running
./scripts/dev_reset.sh  # wipe local data and start fresh
```

Full guide: [docs/development/LOCAL_DEV.md](docs/development/LOCAL_DEV.md)

## Testing

```bash
make test          # run all tests
make test-unit     # unit tests only
make test-file FILE=tests/unit/test_foo.py  # single file
```

## Deployment

Push to any feature branch, open a PR to `main`. The `pr-validate.yml` workflow runs `py_compile` + `ruff` + `pytest tests/unit` on every PR. When CI is green, click Merge — the merge to `main` triggers `.github/workflows/deploy.yml`, which deploys to the production VPS.

The `develop` branch was retired 2026-05-10 (the staging environment never materialized; the chain added failure modes without integration value). See [docs/deployment/architecture_overview.md](docs/deployment/architecture_overview.md) and [docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md](docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md) for full details.
