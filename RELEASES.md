# Releases

## 0.4.0 — 2026-11-26

Chinese tabbed Web UI and DDR/resource monitoring release.

- Chinese Web Admin UI with Overview / Models / Verify / Jobs / Settings tabs
- CPU / NPU utilization, OS / CMM memory, DDR bandwidth and SoC temperature rings
- Model status filter (all / started / stopped / ready / invalid)
- Runtime tabs for multiple running models; Images API and web UI link for image_gen
- Terminal-style job log, copyable shell command preview, styled confirm dialogs
- Stopping state with duplicate-start guard
- 10-second model scan cache with forced rescan from Refresh
- `--no-token` installer option for trusted networks
- `--no-tempmon` option and bundled tempmon 2.3.0 (auto-installed when absent)
- Offline aarch64 wheelhouse for CPython 3.10–3.13; no PyPI access needed
- Automatic GitHub Release workflow on `v*` tags
- axdiffusion image-generation runtime support

## 0.3.1 — 2026-11-19

Web Admin UI demo integration.

- Embed an optional Lite WebUI runtime smoke test
- Generate Lite WebUI launch links with endpoint and model preselection
- Add a public Web Admin UI screenshot to the README
- Document the optional Lite WebUI deployment boundary

## 0.3.0 — 2026-11-19

Third-party integration and resource-monitoring release.

- `GET /api/v1/resources` with current board state and process-local maxima
- CPU percent computed from `/proc/stat` deltas
- `POST /api/v1/resources/max/reset`
- dashboard now includes a compatibility `resource_monitor` payload
- model manifests can declare client defaults: system prompt, max output tokens,
  KV cache/attention quantization, and speculative decoding metadata
- profiles can enable llama-server `--metrics`
- public third-party integration guide: `docs/skills.md`

## 0.2.1 — 2026-11-17

Stable OCR / ASR / TTS package presets.

- MiniCPM-OCR recommended 4K server profile
- Qwen3-ASR minimal 4K server profile
- VoxCPM2 8K server profile with acoustic model, threads, HTTP threads, and malloc environment
- Qwen3-TTS packages marked as one-shot tasks instead of long-running services
- 4K / 8K context choices in the launch dialog
- optional launch fields remain unset instead of being replaced by generic LLM defaults

## 0.2.0 — 2026-11-17

First installable single-board release candidate.

- Model discovery and manifest validation
- systemd-run model lifecycle management
- start / stop / restart / switch
- concurrent model runtimes with a port pool
- profile save / duplicate / reset / diff / preview
- runtime reconciliation and orphan adopt/stop
- job logs, filtering, download, and audit
- Web Admin UI and tempmon-backed device status
- install / upgrade / rollback / uninstall tooling
- release tarball and API/UI regression script
- systemd hardening and log rotation compatible with images without logrotate
