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

- **Staging:** merge into `develop` → auto-deploys to staging
- **Production:** promote `develop` SHA to `main` via GitHub Actions

See [docs/deployment/architecture_overview.md](docs/deployment/architecture_overview.md) for full details.
