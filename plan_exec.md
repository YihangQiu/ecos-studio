# ECOS Studio × eda-agent-dev v1 Execution Checklist

## Baseline

- ecos-studio branch: `iccd`; initial status contained pre-existing dirty files: `Makefile`, `MODULE.bazel.lock`, `ECOS-Studio_0.1.0-alpha.4_amd64.AppImage`.
- eda-agent-dev branch: `master`; initial status contained pre-existing dirty file: `uv.lock`.
- Staging rule: use exact `git add <file>` only; never use `git add .`.

## Phase Boundaries

1. Phase 1: read-only ECOS bridge, Agent API, and GUI chat API wiring.
2. Phase 2: async workspace clone/run-from-step and Agent progress events.
3. Phase 3: guarded strategy write endpoints and eda-agent submodule pointer.

## Fast Verification Commands

```bash
cd ecos-studio/ecos/server && .venv/bin/pytest tests/test_dispatch.py tests/test_ecos_agent_bridge.py -q
cd eda-agent-dev && pytest tests/unit/test_config_loader.py tests/unit/test_request_router.py tests/unit/test_ecos_studio.py -q
cd ecos-studio/ecos/gui && pnpm test
cd ecos-studio/ecos/gui && pnpm build
```
