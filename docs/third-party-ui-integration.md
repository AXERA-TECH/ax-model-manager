# AXERA Model Manager 第三方 UI 对接说明

- 文档版本：0.3.1
- 更新日期：2026-11-20
- 适用对象：第三方上位机、桌面客户端、Web UI、测试台架开发团队
- 板端组件：AXERA Model Manager + llama.cpp runtime + [tempmon](https://github.com/AXERA-TECH/tempmon)

本文是给第三方 UI 团队的交付说明。它按当前已经实现的能力描述调用方式；没有实现的能力会明确列为不支持，避免 UI 侧假设错误。

机器可读接口定义可访问：

```text
http://<board-ip>:18090/openapi.json
http://<board-ip>:18090/docs
```

> 文档中的 `<board-ip>`、`<manager-token>`、`<model-id>`、`<runtime-model-id>` 都是占位符。不要把真实 IP、token、密码或内部模型路径写入前端源码、截图或 Git 仓库。

---

## 1. 一页结论：当前能交付什么

Model Manager 是**管理平面**，不是推理数据面。第三方 UI 应同时访问两类服务：

```text
第三方 UI
  ├── Model Manager :18090
  │   ├── 模型发现 / manifest / profile
  │   ├── start / stop / restart / switch
  │   ├── jobs / logs / audit
  │   └── OS / CMM / CPU / 网络 / 温度 / llama RSS 监控
  │
  └── llama-server :18080-18089
      ├── /v1/models
      ├── /v1/chat/completions
      ├── /v1/audio/speech        # TTS 模型支持时
      ├── /slots
      ├── /props
      └── /metrics                # profile.metrics=true 时

tempmon :8080（独立项目）
  └── /api/stats                  # Manager resources API 的数据源
```

### 1.1 已支持

| UI 业务需求 | 当前获取方式 |
|---|---|
| 指定 X6 / 板卡主机地址 | UI 保存 `<board-ip>`，分别拼接 Manager 与 runtime 地址 |
| 获取模型列表 | `GET /api/v1/models` |
| 模型详情 / artifacts / 校验状态 | `GET /api/v1/models/{id}`、`GET .../validate` |
| 动态启动 / 停止 / 重启 / 切换模型 | Manager lifecycle API |
| 自动分配推理端口 | Manager 从 18080-18089 分配 |
| 多个不同模型并行运行 | `max_running_models` + 端口池 |
| 选择模型 profile | manifest `profiles` + start request |
| 配置 context | profile / start override |
| 配置 batch / ubatch | profile / start override；这是计算 batch，不是并发数 |
| 配置 MTP 投机解码 | profile / start override `mtp` |
| 配置采样参数 | profile / start override `sampling` |
| 显示模型推荐 system prompt | manifest `defaults.system_prompt`，如果模型包提供 |
| 显示默认输出长度 | manifest `defaults.max_output_tokens`，如果模型包提供 |
| 显示 KV cache / attention 量化事实 | manifest `defaults.kv_cache_quantization` / `attention_quantization`，如果模型包提供 |
| 限制单次请求输出长度 | OpenAI 请求 `max_tokens` |
| 单窗口多图输入 | OpenAI-compatible `image_url` content parts，前提是模型能力支持 |
| ASR 音频输入 | 支持 `input_audio` content part 的模型可用 |
| VoxCPM2 TTS | 长驻 VoxCPM2 service 启动后调用 `/v1/audio/speech` |
| OS 内存 / CMM / 网络带宽 / NPU 温度 | `GET /api/v1/resources` |
| CPU percent | `GET /api/v1/resources`，基于 `/proc/stat` delta |
| 资源最大值与清除 | `GET /api/v1/resources` + `POST /api/v1/resources/max/reset` |
| 输入 / 输出 token 数 | llama-server final response `usage` |
| Prefill 耗时 / tps | final response `timings.prompt_ms` / `prompt_per_second` |
| Decode 耗时 / tps / TPOT | final response `timings.predicted_*` |
| 观察到的 TTFT / E2E | UI 记录请求开始、首 delta、final chunk 时间 |
| 累计 Prometheus 指标 | profile / start 设置 `metrics=true`，访问 runtime `/metrics` |
| 任务日志与审计 | Manager jobs / logs / audit API |

> tempmon 是独立仓库
> [AXERA-TECH/tempmon](https://github.com/AXERA-TECH/tempmon)，不包含在 Model Manager
> 安装包内。部署 Manager 前请先按 tempmon README 安装并确认
> `curl http://<board-ip>:8080/health` 正常。

### 1.2 当前不支持或只能部分支持

| 需求 | 当前状态 | UI 建议 |
|---|---|---|
| 真实 NPU utilization | 不支持 | 显示 `N/A`，不要用 NPU 频率或温度伪装 |
| KV cache / attention 量化运行时切换 | 不支持 | 只展示模型包声明的事实 |
| ViT 单独耗时 | 不支持 | 不展示，或合并进 prompt 阶段说明 |
| 服务端完整上下文内容回读 | 不支持 | UI 显示自己维护的对话历史 |
| 服务端精确 TTFT 字段 | 部分支持 | 用客户端首 delta 计时；`prompt_ms` 只能作为 prefill 近似 |
| 同一个模型多 batch 并行推理 | 不支持，当前固定 `-np 1` | 多请求会排队，UI 应提示 |
| 4 分屏同时推理同一模型 | 不支持 | 显示排队状态，不要承诺并行 |
| Qwen3-TTS one-shot task | Manager 不作为长驻 service 启动 | 需要单独 task 流程；本期 UI 不纳入 Manager service 演示 |
| 模型下载 / HF 更新 | 不在 Manager 范围 | 模型包由部署流程准备 |
| 多用户角色 / 细粒度权限 | 不支持 | 单 Manager Bearer token |

---

## 2. 地址、认证与安全边界

### 2.1 基本变量

建议 UI 配置页保存：

```text
board_ip        板卡 IP
manager_port    默认 18090
manager_token   Manager Bearer token
```

派生地址：

```text
MANAGER = http://<board-ip>:18090
RUNTIME = http://<board-ip>:<job.port>
```

### 2.2 Manager 认证

除 `GET /health` 外，Manager API 需要：

```http
Authorization: Bearer <manager-token>
```

示例：

```bash
curl "$MANAGER/health"
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/models"
```

### 2.3 推理服务认证

当前 Manager 不代理推理流量，也不为 llama-server 注入 OpenAI API key。第三方 UI 应直接访问 runtime 端口。

部署方需要保证板卡端口只暴露给可信上位机网络。不要把 Manager token 发给 llama-server；两者认证体系不同。

### 2.4 浏览器跨源

浏览器页面直连 Manager 时，需要在 Manager 配置中添加来源：

```json
"allowed_origins": ["http://<frontend-host>:<frontend-port>"]
```

更推荐的生产架构：

```text
Browser → 第三方 UI 后端 / BFF → Model Manager / llama-server
```

这样 token 保存在 UI 后端，不暴露给浏览器。

### 2.5 UI 不允许出现的操作

Model Manager 的设计边界是：

1. 不提交任意 shell 命令；
2. 不让用户输入任意可执行路径；
3. 不代理 prompt / response；
4. 不绕过 `/slots` busy guard；
5. 不在 UI 中伪造后端没有上报的指标。

---

## 3. 标准接入流程

最小闭环：

```text
1. GET  /health
2. GET  /api/v1/models
3. GET  /api/v1/models/{model-id}
4. GET  /api/v1/models/{model-id}/validate
5. POST /api/v1/models/{model-id}/preview
6. POST /api/v1/models/{model-id}/start
7. 轮询 GET /api/v1/jobs/{job-id}
8. job=running 后读取 port / runtime_models
9. GET  http://<board-ip>:<port>/v1/models
10. POST http://<board-ip>:<port>/v1/chat/completions
11. 轮询 GET /api/v1/resources
12. POST /api/v1/models/{model-id}/stop
```

---

## 4. 服务健康与配置

### 4.1 健康检查

```bash
curl "$MANAGER/health"
```

示例返回：

```json
{
  "status": "ok",
  "version": "0.3.1",
  "mode": "control-enabled"
}
```

`mode` 含义：

| 值 | 含义 |
|---|---|
| `read-only` | 只能查询，控制 API 返回 404 |
| `control-enabled` | 可执行 start / stop / restart / switch 等控制动作 |

### 4.2 读取配置

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/config"
```

关键字段：

```text
manager_port
model_root
llama_port_min / llama_port_max
max_running_models
read_only
enable_control
authentication
allowed_origins
```

该接口不返回 token。

### 4.3 修改模型根目录 / 最大并行模型数

修改模型根目录：

```bash
curl -X PUT "$MANAGER/api/v1/config" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model_root": "/mnt/tangqi"}'
```

路径必须是存在的绝对目录，不能是 `/`、Manager data_dir 或其子目录。
有运行中的模型时返回 409，需先停止模型。

保存后可以显式重新扫描：

```bash
curl -X POST "$MANAGER/api/v1/models/scan" \
  -H "Authorization: Bearer $TOKEN"
```

返回发现的模型数量、可启动数量和扫描错误。`GET /api/v1/models` 也会扫描，
`POST /api/v1/models/scan` 只是让“刷新本地模型”的语义更明确。

修改最大并行模型数：

```bash
curl -X PUT "$MANAGER/api/v1/config" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"max_running_models": 2}'
```

限制不能低于当前 active runtime 数量。多模型并行会占用多份权重和资源，UI 应向用户展示风险并允许确认。

---

## 5. 模型列表与模型信息

### 5.1 获取模型列表

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/models"
```

返回 `models[]`。UI 卡片建议重点展示：

```text
id                     Manager 模型 ID
name                   展示名
version                模型包版本
engine                 llama-server / llama-tts / ...
runtime_type           service / task
capabilities           text / image / audio / tts / image_gen
max_context            manifest 声明的最大上下文
profiles               可选启动 profile 名称
defaults               模型包推荐的客户端默认值
ready_to_start         是否可作为长驻服务启动
errors / warnings      manifest 或 artifacts 问题
active_job             当前运行任务
```

UI 规则：

- `runtime_type=task` 的模型不能调用 start 当长驻服务；
- `ready_to_start=false` 时禁用启动按钮并展示 errors；
- `active_job` 非空时展示当前状态和端口；
- `capabilities` 决定 UI 功能入口。

### 5.2 获取模型详情

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "$MANAGER/api/v1/models/$MODEL_ID"
```

详情包含 manifest、artifacts、profiles、defaults、recent jobs。

### 5.3 模型推荐默认值

如果模型包提供 `defaults`，示例：

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

UI 使用规则：

| 字段 | 用法 |
|---|---|
| `system_prompt` | 默认填入 system message；用户可修改 |
| `max_output_tokens` | 默认输出上限；请求用 `max_tokens` 覆盖 |
| `kv_cache_quantization` | 仅展示模型包事实，不支持运行时切换 |
| `attention_quantization` | 仅展示模型包事实，不支持运行时切换 |
| `speculative` | 展示推荐描述；实际是否启用以 profile / preview 为准 |

如果模型包没有 `defaults`，UI 使用自己的安全默认值。

### 5.4 校验模型

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "$MANAGER/api/v1/models/$MODEL_ID/validate"
```

返回：

```text
valid
ready_to_start
errors
warnings
artifacts
```

---

## 6. Profile 与启动参数

### 6.1 预览启动命令

```bash
curl -X POST "$MANAGER/api/v1/models/$MODEL_ID/preview" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile": "default"}'
```

返回结构化 `argv` 和 `env`，可用于高级调试展示。UI 只读展示，不允许用户编辑任意命令。

### 6.2 启动参数

启动请求示例：

```json
{
  "profile": "default",
  "context": 4096,
  "batch": 4096,
  "ubatch": 4096,
  "cache_ram": 0,
  "ctxcp": 0,
  "mtp": false,
  "metrics": false,
  "sampling": {
    "temperature": 0,
    "top_k": 0,
    "top_p": 1.0,
    "presence_penalty": 0,
    "frequency_penalty": 0
  }
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `profile` | manifest 中已存在的 profile 名 |
| `context` | 总上下文长度；UI 应限制在模型 `max_context` 内 |
| `batch` | prompt 计算 batch |
| `ubatch` | micro batch |
| `cache_ram` | KV cache RAM 相关参数 |
| `ctxcp` | KV cache / context copy 相关参数 |
| `mtp` | 是否启用 MTP speculative decoding |
| `metrics` | 是否追加 `--metrics` |
| `sampling` | 启动侧采样默认值 |
| `port` | 可指定端口；默认自动分配 |
| `force` | 达到资源 / 数量阈值时用户确认后使用 |

推荐 UI 提供的 context 档位：

```text
8K / 16K / 32K / 64K / 128K / 256K
```

并按模型 `max_context` 过滤。

### 6.3 保存 / 删除 / 复制 / 重置 Profile

保存：

```bash
curl -X PUT "$MANAGER/api/v1/models/$MODEL_ID/profiles/$PROFILE" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"context": 8192, "batch": 4096, "ubatch": 4096, "mtp": false}'
```

删除非 default profile：

```bash
curl -X DELETE "$MANAGER/api/v1/models/$MODEL_ID/profiles/$PROFILE" \
  -H "Authorization: Bearer $TOKEN"
```

复制：

```bash
curl -X POST "$MANAGER/api/v1/models/$MODEL_ID/profiles/$PROFILE/duplicate" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"target": "benchmark-greedy"}'
```

重置：

```bash
curl -X POST "$MANAGER/api/v1/models/$MODEL_ID/profiles/$PROFILE/reset" \
  -H "Authorization: Bearer $TOKEN"
```

Diff：

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "$MANAGER/api/v1/models/$MODEL_ID/profiles/$LEFT/diff/$RIGHT"
```

---

## 7. 启动、轮询与推理地址

### 7.1 启动模型

```bash
curl -X POST "$MANAGER/api/v1/models/$MODEL_ID/start" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile":"default","context":4096,"batch":4096,"ubatch":4096,"mtp":false}'
```

返回示例结构：

```json
{
  "job": {
    "job_id": "job_xxx",
    "action": "start",
    "model": "model-id",
    "state": "starting"
  }
}
```

### 7.2 轮询 job

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/jobs/$JOB_ID"
```

常见状态：

```text
starting
loading
running
stopping
stopped
failed
```

UI 处理：

| 状态 | 建议 |
|---|---|
| `starting` / `loading` | 显示加载中，禁止重复 start |
| `running` | 读取 `port`、`runtime_models`，开放推理入口 |
| `stopping` | 显示停止中，禁用控制按钮 |
| `stopped` | 显示已停止 |
| `failed` | 读取 `error`、`failure_code`、日志 |

`running` 后读取：

```text
port
runtime_models
runtime_endpoint
log_path
```

重要：`runtime_endpoint` 是板端本机视角，可能是：

```text
http://127.0.0.1:<port>
```

第三方 UI 必须替换为：

```text
http://<board-ip>:<port>
```

### 7.3 runtime model ID

Manager 的 `model_id` 用于模型包管理；llama-server 的 model ID 以下列结果为准：

```bash
curl "$RUNTIME/v1/models"
```

使用：

```text
job.runtime_models[0]
```

或：

```text
GET /v1/models → data[].id
```

不要假设两者一定相同。

---

## 8. 推理数据面调用

### 8.1 文本 Chat

```bash
curl -N -X POST "$RUNTIME/v1/chat/completions" \
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

### 8.2 图片理解 / OCR / 多图输入

前提：模型 `capabilities` 包含 `image`。

OpenAI-compatible content parts 示例：

```json
{
  "model": "<runtime-model-id>",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "请识别图片中的内容。"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,<base64-data>"}}
      ]
    }
  ],
  "max_tokens": 512
}
```

单窗口多图时重复多个 `image_url` part。是否能正确处理多图取决于模型能力。

### 8.3 ASR 音频输入

前提：模型 `capabilities` 包含 `audio`，且模型包声明为可长驻 service。

示例：

```json
{
  "model": "<runtime-model-id>",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "input_audio",
          "input_audio": {
            "format": "wav",
            "data": "<base64-audio>"
          }
        }
      ]
    }
  ]
}
```

### 8.4 VoxCPM2 TTS

前提：启动的是 VoxCPM2 长驻 service。

```bash
curl -sS "$RUNTIME/v1/audio/speech" \
  -H "Content-Type: application/json" \
  -o speech.wav \
  -d '{
    "model": "<runtime-model-id>",
    "input": "今天天气很好。",
    "response_format": "wav"
  }'
```

如果模型服务支持声音克隆，可在请求中增加模型包声明的扩展字段，例如：

```json
{
  "ref_audio": "<base64-reference-wav>",
  "ref_text": "参考音频对应的文本"
}
```

具体扩展字段以对应模型包 README / manifest 说明为准。

---

## 9. 推理指标采集

### 9.1 服务端返回

非流式响应 final body，或流式 final chunk，可读取：

```text
usage.prompt_tokens
usage.completion_tokens
usage.total_tokens
usage.prompt_tokens_details.cached_tokens
timings.prompt_ms
timings.prompt_n
timings.prompt_per_token_ms
timings.prompt_per_second
timings.predicted_n
timings.predicted_ms
timings.predicted_per_token_ms
timings.predicted_per_second
```

UI 展示建议：

| 指标 | 来源 |
|---|---|
| 输入长度 | `usage.prompt_tokens` |
| 输出长度 | `usage.completion_tokens` |
| cache 命中 | `usage.prompt_tokens_details.cached_tokens` |
| Prefill 耗时 | `timings.prompt_ms` |
| Prefill tps | `timings.prompt_per_second` |
| Decode 耗时 | `timings.predicted_ms` |
| Decode tps | `timings.predicted_per_second` |
| TPOT | `timings.predicted_per_token_ms` |
| batch size | UI 自己记录的并发 / 分屏编号 |

### 9.2 客户端计时

UI 应记录：

```text
request_start
first_content_delta_time
final_chunk_time
```

计算：

```text
观察到的 TTFT = first_content_delta_time - request_start
E2E = final_chunk_time - request_start
```

`timings.prompt_ms` 可作为服务端 prefill 耗时，但不是完整网络 TTFT。当前没有独立 ViT 耗时字段。

### 9.3 Prometheus

启动时设置：

```json
{"metrics": true}
```

然后访问：

```bash
curl "$RUNTIME/metrics"
```

适合累计吞吐和趋势。单请求 TTFT / TPOT 仍应使用 final usage / timings 和客户端计时。

---

## 10. 资源监控

### 10.1 当前值与最大值

建议 UI 每 1 秒调用一次，并在应用内统一分发：

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/resources"
```

返回结构：

```json
{
  "current": {
    "available": true,
    "sampled_at": 1790000000,
    "cpu_percent": 3.25,
    "load": {"1": 0.2, "5": 0.1, "15": 0.05},
    "cpu": {},
    "os_memory": {"total_kb": 1, "avail_kb": 1, "used_kb": 1},
    "cmm": {"total_kb": 1, "used_kb": 1},
    "net": {"rx_mbps": 0.0, "tx_mbps": 0.0},
    "npu": {},
    "npu_max_temp_c": 40.0,
    "thermal_zones": [],
    "disks": [],
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

说明：

- CPU percent 来自 Manager 进程内连续两次 `/proc/stat` 采样；
- 第一次请求可能没有 CPU percent，第二次开始稳定；
- max 是 Manager 进程内统计，Manager 重启后重建；
- 当前没有 NPU utilization；
- 监控不可用时显示 `N/A`，不应阻塞已运行推理。

### 10.2 清除最大值

```bash
curl -X POST "$MANAGER/api/v1/resources/max/reset" \
  -H "Authorization: Bearer $TOKEN"
```

只清除 max，不改变 current。

### 10.3 Dashboard 聚合

兼容接口：

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/dashboard"
```

返回模型概况、active jobs、`resources` 当前快照和 `resource_monitor.current/max`。

---

## 11. 停止、重启、切换

### 11.1 停止

```bash
curl -X POST "$MANAGER/api/v1/models/$MODEL_ID/stop" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode":"block","timeout_seconds":15}'
```

如果 llama-server `/slots` 显示正在处理请求，Manager 会返回 409。用户确认可以中断时：

```json
{"mode":"block","timeout_seconds":15,"force":true}
```

UI 必须把 `force` 做成二次确认。

### 11.2 重启

```bash
curl -X POST "$MANAGER/api/v1/models/$MODEL_ID/restart" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile":"default","stop_timeout_seconds":15}'
```

返回 composite job 和 runtime job；UI 应轮询 composite job。

### 11.3 切换

```bash
curl -X POST "$MANAGER/api/v1/models/$TARGET_MODEL_ID/switch-to" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profile":"default","stop_timeout_seconds":15}'
```

切换会先停止当前 runtime，再启动目标模型。若目标模型已在运行，返回 existing job。

---

## 12. Jobs、日志与审计

### 12.1 Job 列表

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/jobs"
```

### 12.2 日志

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "$MANAGER/api/v1/jobs/$JOB_ID/logs?lines=200&errors_only=true"
```

支持参数：

```text
lines        1-5000
search       关键字
errors_only  true/false
output_format=json/text
```

### 12.3 审计

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/audit?limit=100"
```

控制动作会记录 start / stop / restart / switch / config / manifest / profile 等审计事件。

---

## 13. Runtime 诊断

查看 Manager 识别到的 runtime / orphan：

```bash
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/runtimes"
```

接管孤儿：

```bash
curl -X POST "$MANAGER/api/v1/runtimes/$RUNTIME_ID/adopt" \
  -H "Authorization: Bearer $TOKEN"
```

停止孤儿：

```bash
curl -X POST "$MANAGER/api/v1/runtimes/$RUNTIME_ID/stop" \
  -H "Authorization: Bearer $TOKEN"
```

一般第三方 UI 可以只展示诊断结果，不默认提供高危操作。

---

## 14. API 总览

| Method | Path | 说明 |
|---|---|---|
| GET | `/health` | 公开健康检查 |
| GET | `/api/v1/config` | 读取配置 |
| PUT | `/api/v1/config` | 修改模型根目录 / 最大并行模型数 |
| POST | `/api/v1/config/validate` | 校验模型根目录 |
| POST | `/api/v1/models/scan` | 重新扫描模型根目录 |
| GET | `/api/v1/models` | 模型列表 |
| GET | `/api/v1/models/{id}` | 模型详情 |
| GET | `/api/v1/models/{id}/manifest` | manifest |
| POST | `/api/v1/models/{id}/manifest/confirm` | 确认 inferred manifest |
| GET | `/api/v1/models/{id}/validate` | 校验模型 |
| PUT | `/api/v1/models/{id}/profiles/{profile}` | 保存 profile |
| DELETE | `/api/v1/models/{id}/profiles/{profile}` | 删除 profile |
| POST | `/api/v1/models/{id}/profiles/{profile}/duplicate` | 复制 profile |
| POST | `/api/v1/models/{id}/profiles/{profile}/reset` | 重置 profile |
| GET | `/api/v1/models/{id}/profiles/{left}/diff/{right}` | profile diff |
| POST | `/api/v1/models/{id}/preview` | 预览启动命令 |
| POST | `/api/v1/models/{id}/start` | 启动模型 |
| POST | `/api/v1/models/{id}/stop` | 停止模型 |
| POST | `/api/v1/models/{id}/restart` | 重启模型 |
| POST | `/api/v1/models/{id}/switch-to` | 切换模型 |
| GET | `/api/v1/runtimes` | runtime / orphan 诊断 |
| POST | `/api/v1/runtimes/{runtime_id}/adopt` | 接管孤儿 |
| POST | `/api/v1/runtimes/{runtime_id}/stop` | 停止孤儿 |
| GET | `/api/v1/jobs` | job 列表 |
| GET | `/api/v1/jobs/{job_id}` | job 状态 |
| GET | `/api/v1/jobs/{job_id}/logs` | job 日志 |
| GET | `/api/v1/audit` | 审计 |
| GET | `/api/v1/dashboard` | 聚合视图 |
| GET | `/api/v1/resources` | 当前值与 max |
| POST | `/api/v1/resources/max/reset` | 清除资源 max |

控制类 API 在 `read_only=true` 或 `enable_control=false` 时不可用。

---

## 15. 推荐错误处理

| 场景 | HTTP / 状态 | UI 处理 |
|---|---:|---|
| Manager 离线 | 网络错误 | 提示设备不可达，不自动重试控制动作 |
| token 错误 | 401 | 提示重新配置 token |
| 控制未开启 | 404 | 提示板端 Manager 是 read-only |
| 模型不存在 | 404 | 刷新模型列表 |
| `ready_to_start=false` | 400 | 展示校验错误 |
| job failed | job state | 展示 `error` / `failure_code` / logs |
| model limit reached | 409 | 展示资源风险，用户确认后 `force:true` |
| slot busy | 409 | 提示正在推理；用户确认后才 force stop |
| tempmon unavailable | 503 | systemd 模式下阻止启动 |
| runtime endpoint 不通 | 网络错误 | 将 `127.0.0.1` 替换为 `<board-ip>` 后重试 |
| metrics 404/501 | HTTP | 提示未开启 `metrics=true` |
| NPU utilization 缺失 | 字段为空 | 显示 `N/A` |

---

## 16. 交付验收清单

第三方 UI 最小验收流程：

```text
1. 配置 board IP + token，健康检查成功
2. 模型列表显示 ready / task / capabilities / active job
3. 模型详情能展示 profiles 与 defaults
4. preview 能显示结构化启动参数
5. start 后 job 状态正确流转到 running
6. runtime /v1/models 可访问，model id 使用正确
7. 文本 chat 非流式与流式均可用
8. 支持图片的模型可发送单图 / 多图
9. 支持音频的模型可发送 input_audio
10. final usage / timings 可展示
11. resources 每秒刷新，max/reset 可用
12. stop 遇到 slot busy 时先提示，不默认 force
13. jobs / logs / audit 可查看
14. 不显示或伪造 NPU utilization、ViT 耗时等未支持指标
```

---

## 17. 当前边界的明确声明

给第三方团队交付时，以下表述必须保持一致：

```text
1. Manager 管理模型生命周期，不代理推理请求。
2. 当前 llama-server 由 Manager 固定以 -np 1 启动。
3. 多个请求访问同一个模型服务时会在服务内排队。
4. Manager 的多模型并行是多个不同模型进程并行，
   不等价于同一个模型内部多 batch 并行。
5. 当前没有 NPU utilization、ViT 单独耗时、KV/attention 运行时切换。
6. TTFT 与 E2E 由上位机计时；服务端 final timings 提供 prefill/decode。
7. 上下文内容由上位机维护；服务端完整上下文回读不在本期交付。
```
