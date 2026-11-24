# AXERA Model Manager 第三方接入 Skills

本文面向需要开发上位机、调试工具或客户界面的工程师，也适合作为 coding agent 操作本项目的上下文说明。目标是用一份文档说清楚：项目边界、架构、API 调用流程、资源配置、推理指标和安全注意事项。

## 1. 项目定位

AXERA Model Manager 是 **AX8860 / AX8860P 单板模型生命周期管理平面**。它负责：

```text
模型发现与校验
manifest / profile 管理
启动、停止、重启、切换模型
端口池与多模型运行时管理
任务日志、审计、runtime 恢复
板端资源当前值和最大值观测
```

它不是推理数据面，不代理 prompt 和 response。聊天、图片、OCR、ASR、TTS 请求必须直接访问 Manager 启动出来的 llama-server 端口。

板端资源监控依赖独立项目 [AXERA tempmon](https://github.com/AXERA-TECH/tempmon)；
Model Manager 不包含 tempmon agent，部署前需先安装并确认 `http://<board-ip>:8080/health` 可访问。

## 2. 架构与调用面

```text
上位机 / 客户 Web UI
  ├── Model Manager API :18090
  │   ├── 管理、配置、manifest、profile
  │   ├── start / stop / restart / switch
  │   ├── jobs / logs / audit
  │   └── resources / dashboard
  │
  └── llama-server :18080-18089
      ├── /v1/models
      ├── /v1/chat/completions
      ├── /slots
      └── /metrics，可选，由 profile.metrics 开启

板端只读监控：
  tempmon agent :8080
      └── /api/stats
```

推荐职责划分：

| 数据 | 获取位置 |
|---|---|
| 模型列表、启动参数、能力 | Manager API |
| 启动 / 停止状态和日志 | Manager API |
| OS / CMM / CPU / 网络 / 温度 / 磁盘 | Manager resources API（原始数据来自 tempmon） |
| Chat / Vision / OCR / ASR / TTS 推理 | llama-server |
| 单请求 usage / timings | llama-server final response |
| Prometheus 累计指标 | llama-server `/metrics` |
| 快速人工聊天冒烟 | Manager 内嵌 Lite WebUI，实际请求仍直连 llama-server |
| 板端原始资源数据 | tempmon `:8080/api/stats` |

## 3. 基本变量与认证

下文使用以下占位符：

```bash
MANAGER=http://<board-ip>:18090
RUNTIME=http://<board-ip>:<llama-port>
TOKEN=<manager-token>
MODEL_ID=<model-id>
JOB_ID=<job-id>
```

除 `GET /health` 外，Manager API 都需要：

```http
Authorization: Bearer <manager-token>
```

示例：

```bash
curl "$MANAGER/health"
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/models"
```

不要把 token、真实板卡 IP、密码写入前端源码、文档或 Git 仓库。

## 4. 标准接入流程

### Step 1：确认 Manager 在线

```bash
curl "$MANAGER/health"
```

期望：

```json
{
  "status": "ok",
  "version": "<version>",
  "mode": "control-enabled"
}
```

### Step 2：读取模型列表

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/models"
```

重点字段：

```text
id                       模型 ID
ready_to_start           是否可作为长驻服务启动
runtime_type             service / task
capabilities             text / image / audio / tts / image_gen
max_context              manifest 声明的最大上下文
profiles                 可用启动配置
defaults                 模型包提供的客户端默认值
active_job               当前运行任务
```

### Step 3：读取模型详情和默认值

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/models/$MODEL_ID"
```

`defaults` 可包含：

```json
{
  "system_prompt": "You are a concise assistant.",
  "max_output_tokens": 4096,
  "kv_cache_quantization": "E4M3",
  "attention_quantization": "MXFP4",
  "speculative": {
    "type": "draft-mtp",
    "enabled": false,
    "draft_n_max": 3
  }
}
```

使用方式：

- `system_prompt` 是模型包推荐的默认 system message；上位机仍应在请求中显式发送。
- `max_output_tokens` 是默认输出长度；每轮请求可用 OpenAI `max_tokens` 覆盖。
- KV cache / attention quantization 是模型包事实，仅用于展示，不支持运行时切换。
- speculative 是推荐配置描述；实际启动参数以 profile 和 preview 命令为准。

### Step 4：预览启动命令

```bash
curl -X POST "$MANAGER/api/v1/models/$MODEL_ID/preview" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile": "default"}'
```

返回结构化 `argv` 和 `env`。第三方 UI 可以直接展示，但不要让用户输入任意 shell。

### Step 5：启动模型

```bash
curl -X POST "$MANAGER/api/v1/models/$MODEL_ID/start" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "profile": "default",
    "context": 4096,
    "batch": 4096,
    "ubatch": 4096,
    "mtp": false,
    "metrics": false,
    "sampling": {"temperature": 0}
  }'
```

常用字段：

| 字段 | 说明 |
|---|---|
| `profile` | manifest 中的 profile 名 |
| `context` | 总上下文 |
| `batch` / `ubatch` | prompt 计算 batch，不等于并发用户数 |
| `cache_ram` / `ctxcp` | KV cache 相关启动参数 |
| `mtp` | 是否启用 MTP speculative decoding |
| `metrics` | 是否为 llama-server 追加 `--metrics` |
| `sampling` | greedy 等采样参数 |
| `port` | 可指定端口；默认从 18080-18089 自动分配 |

启动是异步任务，返回 `job_id` 后轮询：

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/jobs/$JOB_ID"
```

状态变为 `running` 后读取：

```text
port
runtime_models
runtime_endpoint
log_path
```

注意：`runtime_endpoint` 是板端本机视角，可能是 `http://127.0.0.1:<port>`。上位机必须访问：

```text
http://<board-ip>:<port>
```

### Step 6：调用 llama-server

先获取实际 runtime model ID：

```bash
curl "$RUNTIME/v1/models"
```

发送 chat：

```bash
curl -X POST "$RUNTIME/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<runtime-model-id>",
    "messages": [
      {"role": "system", "content": "You are a concise assistant."},
      {"role": "user", "content": "你好，请介绍一下你自己。"}
    ],
    "temperature": 0,
    "max_tokens": 512,
    "stream": true
  }'
```

### Step 7：展示推理指标

优先使用最终响应的 `usage` 和 `timings`：

```text
prompt_tokens
completion_tokens
prompt_tokens_details.cached_tokens
timings.prompt_ms
timings.prompt_per_second
timings.predicted_ms
timings.predicted_per_second
timings.predicted_per_token_ms
```

推荐展示：

```text
输入 tokens
输出 tokens
cache 命中 tokens
prefill 耗时 / tps
decode 耗时 / tps
TPOT
```

客户端应另外记录：

```text
request_start
first_content_delta_time
final_chunk_time
```

由此计算：

```text
观察到的 TTFT = first_content_delta_time - request_start
E2E = final_chunk_time - request_start
```

不要用浏览器计时伪造后端未提供的指标。

### Step 8：停止模型

停止前默认会检查 llama-server `/slots`：

```bash
curl -X POST "$MANAGER/api/v1/models/$MODEL_ID/stop" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode": "block", "timeout_seconds": 15}'
```

如果模型正在处理请求，会返回冲突。确认可以中断时：

```json
{"mode": "block", "timeout_seconds": 15, "force": true}
```

## 5. 资源监控 API

### 当前值和最大值

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/resources"
```

返回：

```json
{
  "current": {
    "available": true,
    "sampled_at": 1790000000,
    "cpu_percent": 3.25,
    "load": {"1": 0.2, "5": 0.1, "15": 0.05},
    "cpu": {
      "cores": 16,
      "frequency_mhz": [1650],
      "frequency_min_mhz": 1650,
      "frequency_max_mhz": 1650,
      "percent": 3.25
    },
    "os_memory": {"total_kb": 1, "avail_kb": 1, "used_kb": 1},
    "cmm": {"total_kb": 1, "used_kb": 1},
    "net": {"rx_mbps": 0.0, "tx_mbps": 0.0},
    "npu": {"tdp_freq_mhz": 800, "clock_mhz": [800]},
    "npu_max_temp_c": 40.0,
    "llama": {"process_count": 1, "rss_kb": 1}
  },
  "max": {
    "cpu_percent": 3.25,
    "cpu_load_1m": 0.2,
    "os_used_kb": 1,
    "cmm_used_kb": 1,
    "rx_mbps": 0.0,
    "tx_mbps": 0.0,
    "npu_temp_c": 40.0,
    "llama_rss_kb": 1
  },
  "sample_count": 1,
  "max_reset_at": null,
  "sampling": "on-request"
}
```

建议上位机每 1 秒轮询一次，并在应用内统一分发状态，避免多个控件重复请求。

说明：

- `cpu_percent` 来自 Manager 进程两次采样间的 `/proc/stat` delta。
- 第一次采样可能没有 CPU percent；从第二次开始稳定输出。
- max 是 Manager 进程内统计，Manager 重启后重建。
- reset 只清空 max，不改变 current。
- NPU utilization 来自 tempmon 2.2.0+ 的 `npu.util_avg`；不要用 NPU 频率或温度伪装占用率。

### 清除最大值

```bash
curl -X POST "$MANAGER/api/v1/resources/max/reset" \
  -H "Authorization: Bearer $TOKEN"
```

返回：

```json
{
  "cleared_max": {},
  "current": {},
  "max": {},
  "sample_count": 1,
  "max_reset_at": 1790000000
}
```

## 6. Dashboard 兼容接口

已有客户端可以继续使用：

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/dashboard"
```

`dashboard.resources` 保持为当前快照。新增 `dashboard.resource_monitor` 包含 `current / max / sample_count`，适合一个请求同时刷新模型列表和资源概况。

## 7. Prometheus `/metrics`

Profile 或启动请求设置：

```json
{"metrics": true}
```

Manager 会为 llama-server 追加：

```text
--metrics
```

模型启动后访问：

```bash
curl "$RUNTIME/metrics"
```

适合累计吞吐和长期趋势。单请求 TTFT / TPOT 仍应优先读取 final response 的 usage / timings。

## 8. 浏览器跨源接入

如果浏览器页面直接访问 Manager，需要在配置中添加来源：

```json
"allowed_origins": ["http://<frontend-host>:<frontend-port>"]
```

更安全的架构是把 Manager token 保存在上位机后端，由上位机后端做 BFF 代理：

```text
Browser → Third-party frontend backend → Model Manager / llama-server
```

桌面软件或服务端调用没有 CORS 限制，但仍应安全保存 token。

## 9. 错误处理建议

| 场景 | 建议处理 |
|---|---|
| `/health` 失败 | 提示 Manager 或网络不可用，不自动重试控制动作 |
| 模型 `ready_to_start=false` | 展示 manifest 错误 / draft 状态 |
| job `starting/loading` | 继续轮询，不重复 start |
| job `failed` | 读取 job error 和 logs |
| 409 slot busy | 向用户确认是否 force stop |
| resources unavailable | 显示监控不可用，不阻塞已运行推理 |
| runtime endpoint 失败 | 将 hostname 替换为 `<board-ip>` 后重试 |

## 10. 快速回归建议

功能开发默认使用小型文本模型，例如 2B 模型；大模型只用于内存、长上下文或性能专项。最小回归顺序：

```text
1. GET /health
2. GET /api/v1/models
3. GET /api/v1/models/<2b-model-id>
4. POST /preview
5. POST /start
6. poll job until running
7. GET /v1/models
8. POST /v1/chat/completions
9. poll /api/v1/resources
10. POST /stop
11. verify active_job is null
```

这套流程可以覆盖第三方界面最常用的模型生命周期和资源信息获取路径。

## 11. Agent 开发守则

修改本项目时必须遵守：

1. Manager 不做推理代理，不接收用户 prompt。
2. 所有控制动作必须保留 audit。
3. 停止模型前保留 `/slots` busy guard。
4. 模型路径必须限制在 model root 内。
5. 不提交真实 IP、token、密码或内部模型路径。
6. 新 API 需要更新 README / 用户指南、OpenAPI 行为和测试。
7. 默认用小型 2B 模型做生命周期回归。
8. 不能伪造 NPU utilization、ViT 耗时或后端未上报的性能指标。
