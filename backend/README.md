# AXERA Model Manager Backend

FastAPI 后端与内置 Web Admin UI。默认配置是只读模式；板端生产配置启用
systemd 控制后端。

## 本地安装与测试

```bash
cd ..
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/pip install -e backend
.venv/bin/python -m pytest backend/tests -q
```

## 本地服务

```bash
./scripts/dev_server.sh
```

- UI: <http://127.0.0.1:18090/>
- API docs: <http://127.0.0.1:18090/docs>
- token: `local-dev-token`

本地生命周期测试使用 `fake-subprocess`；板端使用 systemd transient unit。

## API

### 公开

| Method | Path | Description |
|---|---|---|
| GET | `/health` | 版本与运行模式 |

### 认证读取

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/config` | 配置，token 不返回 |
| GET | `/api/v1/models` | 模型摘要 |
| GET | `/api/v1/models/{id}` | 模型详情与 artifact 检查 |
| GET | `/api/v1/models/{id}/manifest` | package / user / draft manifest |
| GET | `/api/v1/models/{id}/validate` | manifest 与 artifact 校验 |
| GET | `/api/v1/models/{id}/preview` | 预览结构化启动命令 |
| GET | `/api/v1/jobs` | job 列表 |
| GET | `/api/v1/jobs/{job_id}` | job 状态 |
| GET | `/api/v1/jobs/{job_id}/logs` | 日志尾部 |
| GET | `/api/v1/audit` | audit 记录 |
| GET | `/api/v1/dashboard` | 聚合状态 |
| GET | `/api/v1/runtimes` | reconcile systemd runtime 与孤儿 |

### 控制，需要 `enable_control=true`

| Method | Path | Description |
|---|---|---|
| PUT | `/api/v1/config` | 修改 model limit |
| POST | `/api/v1/models/{id}/start` | 启动模型 |
| POST | `/api/v1/models/{id}/stop` | 带 IN USE guard 停止 |
| POST | `/api/v1/models/{id}/restart` | 重启 |
| POST | `/api/v1/models/{id}/switch-to` | 切换模型 |
| POST | `/api/v1/models/{id}/preview` | 预览结构化启动命令 |
| POST | `/api/v1/models/{id}/manifest/confirm` | 确认 inferred manifest |
| PUT | `/api/v1/models/{id}/profiles/{profile}` | 保存 profile |
| DELETE | `/api/v1/models/{id}/profiles/{profile}` | 删除 profile |
| POST | `/api/v1/models/{id}/profiles/{profile}/duplicate` | 复制 profile |
| POST | `/api/v1/models/{id}/profiles/{profile}/reset` | 重置 profile |
| GET | `/api/v1/models/{id}/profiles/{left}/diff/{right}` | profile diff |
| POST | `/api/v1/runtimes/{runtime_id}/adopt` | 接管孤儿 |
| POST | `/api/v1/runtimes/{runtime_id}/stop` | 停止孤儿 |

生产 systemd 后端已实现。`max_running_models` 是保守限制，用户确认风险后
可用 `force: true` 越过。每个 runtime 从 `18080-18089` 分配端口。

OCR / ASR / VoxCPM2 使用稳定包推荐预设。`runtime_type=task` 的 Qwen3-TTS
包需要请求期文本和输出路径，不能作为长驻 OpenAI-compatible service 启动；
API 和 UI 都会拒绝这类 start。
