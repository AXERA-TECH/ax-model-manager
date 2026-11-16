# Releases

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
