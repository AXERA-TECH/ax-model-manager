# 图像生成模型接入（MiniMax-H3-Image / axdiffusion）

本文说明 Manager 如何管理非 llama 的图像生成运行时，以及板端服务的调用方式。
模型与推理程序来源：

- 模型：<https://huggingface.co/AXERA-TECH/MiniMax-H3-Image-AX8860>
- 推理程序：<https://github.com/AXERA-TECH/axdiffusion.cpp>

## 1. 和 llama-server 的区别

H3 Image 是 diffusion 文生图模型，不是 LLM。它不提供 `/v1/chat/completions`，
Context / Batch / KV cache / MTP / Sampling 等 llama 参数对它没有意义。
Manager 对 `capabilities` 包含 `image_gen` 的模型：

- 卡片上直接 `Start` / `Stop` / `Restart`，不弹出 LLM 参数对话框。
- 启动后显示 `Open web UI`，指向 axdiffusion-server 自带的生图网页。
- 模型详情显示 `Engine`、`API`、`Endpoints`、`API base`、`Web UI`。

## 2. 板端部署

模型目录（示例）：

```text
<model-root>/MiniMax-H3-Image-AX8860/
├── axdiffusion.json          # 模型架构描述
├── bin/
│   ├── axdiffusion-cli
│   └── axdiffusion-server
├── axmodels/                 # NPU 用的 AXModel
├── dit/ text_encoder/ vae/ vit/
├── loras/                    # 可选，LoRA .safetensors
└── output/                   # 生成图片
```

Manager 的 user manifest（`/var/lib/axera-model-manager/models/MiniMax-H3-Image-AX8860.json`）：

```json
{
  "schema_version": 1,
  "id": "minimax-h3-image",
  "name": "MiniMax-H3-Image",
  "engine": "axdiffusion-server",
  "artifacts": {
    "binary": "bin/axdiffusion-server",
    "model": "axdiffusion.json"
  },
  "capabilities": ["image_gen"],
  "runtime_type": "service",
  "max_context": 20480,
  "estimated_cmm_mb": 34816,
  "profiles": {"default": {"startup_timeout_seconds": 900}},
  "launch": {
    "argv": ["--model", ".", "--port", "{port}", "--out-dir", "output", "--lora-dir", "loras"],
    "health_path": "/health",
    "out_dir": "output",
    "default_port": 18500
  },
  "api": {
    "type": "openai-images",
    "base_path": "/v1",
    "endpoints": [
      "/v1/images/generations",
      "/v1/models",
      "/v1/loras",
      "/v1/jobs",
      "/health"
    ],
    "web_ui": "/",
    "notes": "OpenAI Images compatible; reference editing via /v1/jobs."
  }
}
```

说明：

- `launch.argv` 是数组，Manager 直接 exec，不经过 shell；占位符支持 `{port}`、`{model_dir}`、`{out_dir}`。
- `default_port` 让 H3 固定使用 18500（与 axdiffusion 官方示例一致）；端口被占用时启动返回 409。
- `startup_timeout_seconds=900` 覆盖首次从磁盘加载约 30 GB 权重的情况。
- `estimated_cmm_mb` 目前用于展示；常驻 NPU 内存约 27–31 GiB，参考图编辑后约 34 GiB。

## 3. 通过 Manager 启停

```bash
MANAGER=http://<board-ip>:18090
TOKEN=<manager token>

curl -X POST -H "Authorization: Bearer $TOKEN" \
  "$MANAGER/api/v1/models/minimax-h3-image/start" \
  -H "Content-Type: application/json" -d '{"profile":"default"}'

# 轮询 job，state=running 后使用 job.port
curl -H "Authorization: Bearer $TOKEN" "$MANAGER/api/v1/jobs/<job_id>"

# 停止（等待当前图片完成后退出）
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "$MANAGER/api/v1/models/minimax-h3-image/stop" \
  -H "Content-Type: application/json" -d '{}'
```

## 4. 运行时 API

假设 Manager 分配的端口是 `18500`，以下地址都在板端服务上（不是 Manager 的 18090）。

### 4.1 内置网页

```text
http://<board-ip>:18500/
```

支持提示词（带 token 计数）、画幅、种子、LoRA 叠加、参考图编辑、进度与排队、历史图。

### 4.2 OpenAI Images API

```bash
curl http://<board-ip>:18500/v1/images/generations \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "minimax-h3-image",
    "prompt": "a lighthouse on a cliff at sunset",
    "n": 2,
    "size": "1024x1024",
    "response_format": "url",
    "seed": 42,
    "loras": [{"name": "my_style", "scale": 0.8}]
  }'
```

| 字段 | 说明 |
---|---|
| `prompt` | 必填；上限由模型档位决定，当前 512 token |
| `model` | 可省略；`GET /v1/models` 返回 `minimax-h3-image` |
| `n` | 1–4 |
| `size` | 如 `1024x1024`；可用值见 `GET /health` 的 `sizes` |
| `response_format` | `b64_json`（默认）或 `url` |
| `seed` / `steps` / `loras` | 扩展字段 |

`response_format=url` 时图片地址形如 `http://<board-ip>:18500/files/xxx.png`。

### 4.3 A1111 / SD WebUI

```bash
curl http://<board-ip>:18500/sdapi/v1/txt2img \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a lighthouse <lora:my_style:0.8>","width":1024,"height":1024,"seed":-1}'
```

`steps`、`cfg_scale`、`sampler_name`、`negative_prompt` 会被忽略（6 步蒸馏、无 CFG）。

### 4.4 异步任务与参考图编辑

```bash
# 提交编辑任务（images 为 base64 PNG/JPEG，1–3 张）
curl -X POST http://<board-ip>:18500/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"task":"edit","prompt":"Make the cat black","width":1024,"height":1024,"images":["<base64>"]}'

curl http://<board-ip>:18500/v1/jobs/<id>          # 状态与进度
curl http://<board-ip>:18500/v1/jobs/<id>/events  # SSE
curl -X DELETE http://<board-ip>:18500/v1/jobs/<id>
```

OpenAI 的 `/v1/images/edits` 未实现；参考图编辑请走 `/v1/jobs`、A1111 或内置网页。

### 4.5 其它

| 路径 | 说明 |
---|---|
| `GET /health` | 状态、模型、可用尺寸、NPU 内存、队列 |
| `GET /v1/models` | OpenAI 格式模型列表 |
| `GET /v1/loras` | `--lora-dir` 中可用的 LoRA |
| `GET /files/<name>.png` | `response_format=url` 生成的图片 |

## 5. 运行限制

- 同一时刻只生成一张图，并发请求自动排队。
- 收到 SIGTERM/SIGINT 时等当前任务完成后退出，不要用 `kill -9`。
- 宽高需为 32 的倍数、≥256；序列长度上限 16896（编辑 20480）。
- 首次启动要从磁盘读入约 30 GB 权重，之后约 23 s（本次实测 10.7 s，文件已缓存）。
- NPU 内存不足 31 GiB 或有其他进程占用 CMM 时启动会失败。
