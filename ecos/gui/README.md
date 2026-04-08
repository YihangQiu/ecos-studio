# ECOS Studio (GUI)

Desktop chip-design frontend built with **Tauri + Vue 3 + TypeScript**, working with backends such as `ecos/server` as part of ECOS Studio.

## Prerequisites

- **Node.js** (LTS recommended)
- **pnpm** (this repo uses pnpm for dependencies)
- **Rust toolchain** (only needed for local Tauri dev and packaging)

For a fuller end-to-end setup (Python, `uv`, Bazel, etc.), see the [ECOS package README](../README.md) and the [repository root README](../../README.md).

## Quick start

### Install dependencies

```bash
pnpm install
```

### Development

```bash
# Tauri shell + frontend
pnpm run tauri:dev
```

### Build and preview

```bash
# Typecheck + production build (output to dist/, used by Tauri beforeBuildCommand)
pnpm run build
```

## Stack

- **Tauri 2** — Rust desktop shell and system APIs
- **Vue 3** — Composition API
- **PixiJS 8** — WebGL/WebGPU canvas and editor rendering
- **PrimeVue 4** — UI components (Aura theme)
- **Tailwind CSS v4** — styling
- **Vite 7** — dev and build

## Source layout (overview)

| Path | Description |
|------|-------------|
| `src/applications/editor/` | Canvas editor core, layout rendering, plugins, tile logic |
| `src/components/` | Reusable UI (toolbar, sidebars, panels, etc.) |
| `src/views/` | Routed pages |
| `src/composables/` | Composables (workspace, menus, Tauri wrappers, etc.) |
| `src/stores/` | Pinia state |
| `src/api/` | HTTP / SSE client wrappers |
| `src-tauri/` | Tauri backend (Rust), packaging and window config |

## Related docs

- [ECOS package README](../README.md) — overall quick start and release notes for `ecos/server` + GUI  
- [ECOS Studio user guide](../docs/user-guide.md) — product usage  
- [Repository root README](../../README.md) — monorepo overview  
- [ECC development](../../ecc/docs/development.md), [ECC architecture](../../ecc/docs/architecture.md) — ECC toolchain docs  

---

Built by the ECOS Team
