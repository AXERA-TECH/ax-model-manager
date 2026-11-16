# AXERA Model Manager

AX8860 板端模型管理平台，用于管理 `/mnt/models` 下的模型目录，并提供模型发现、配置、启动、停止、切换、日志与状态观测能力。

## 当前定位

```text
Model Manager: 面向运维 / 用户的模型管理平台
LiteWebUI:     面向用户的聊天与轻量诊断前端
tempmon:       只读设备监控 agent
```

## 核心原则

1. 模型目录默认为 `/mnt/models`，每个模型一个目录，路径可由用户配置。
2. Web 前端不直接执行 shell，不直接 SSH。
3. 启动参数必须是结构化 profile，不允许提交任意命令。
4. llama-server 由 systemd / process manager 托管，Manager 重启不影响推理服务。
5. 停止与切换前必须执行 IN USE guard。
6. 所有控制动作写审计日志。
7. tempmon 保持只读，不承担控制功能。

## 端口规划

| 服务 | 默认端口 | 说明 |
|---|---:|---|
| tempmon | 8080 | 板端只读监控 |
| llama-server | 18080-18089 | 推理服务端口池 |
| model-manager | 18090 | 板端管理 API |
| central console | 18091 | 多板管理端，可选 |

## 目录

```text
ax-model-manager/
  backend/
    ax_model_manager/
    tests/
  scripts/
  docs/
    开发测试记录-20261116.md
```

## 当前实现状态

已完成 **Phase 0 / Phase 1 本机只读 MVP**：

- FastAPI 只读后端
- 配置文件与环境变量加载
- 模型根目录扫描
- `model.json` 读取与校验
- 无 manifest 时生成 inferred draft
- GGUF / mmproj / chat template / kernels / llama-server 完整性检查
- 路径穿越与 symlink escape 防护
- 可选 Bearer Token 认证
- 本地 fixture 模型生成脚本
- 25 个单元 / API 测试

同时完成了 **Phase 2 的本机 fake 生命周期原型**：

- JobStore 与异步任务状态
- fake-subprocess 进程后端
- `start` / `stop` 控制端点
- `18080-18089` 端口自动分配
- `/health` ready 轮询
- `/slots` IN USE guard
- force stop
- audit JSONL 记录
- fake llama-server，用于完全本机测试

尚未实现：

- systemd 进程托管
- 重启 / 切换
- Web Admin UI
- 多板 Central Console
- 模型下载与更新

## 本地运行，不需要板子

```bash
cd <repository-directory>
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/pip install -e backend

.venv/bin/python -m pytest backend/tests -q
./scripts/dev_server.sh
```

默认本地地址：

```text
http://127.0.0.1:18090
```

开发 token：

```text
local-dev-token
```

如果本机 `18090` 被占用：

```bash
AX_MODEL_MANAGER_PORT=18092 ./scripts/dev_server.sh
```

FastAPI 仍然只负责管理平面；聊天推理继续由 llama-server 的 OpenAI-compatible API 提供。

当前 `fake-subprocess` 后端只用于本机测试，不是生产进程管理方案。

## 上海板部署状态

2026-11-16 已在上海板上部署验证：

```text
Model Manager API: http://<board-ip>:18090
部署路径: /opt/axera-model-manager
配置: /etc/axera/model-manager/config.json
状态数据: /var/lib/axera-model-manager
systemd: axera-model-manager.service
```

当前能力：

```text
扫描 /mnt/models 下 13 个模型目录
识别 llama-server / llama-tts
manifest draft 确认
systemd-run 启动独立 transient unit
/health ready 检测
/slots IN USE guard
停止模型
切换模型
重启模型
多模型并行，自动分配不同端口
达到模型数量限制后可由用户强制越过
保存 / 删除 launch profile
job 持久化
audit 记录
```

已验证：

```text
Qwen3.5-0.8B 启动 / 停止
Qwen3.6-35B-A3B 启动 / 停止
Qwen3.5-0.8B → Qwen3.6-35B-A3B 切换
Qwen3.5-0.8B 重启
Qwen3.5-0.8B + Qwen3-ASR-1.7B 双模型并行
benchmark_greedy profile 保存与启动
Manager 重启不影响运行中的 llama-server
Manager 重启后 job 状态可恢复
```

访问 API 需要部署时生成的 Bearer token。本机不把 token 写入 Git 仓库，密钥文件位于：

```text
<repository-directory>/.secrets/shanghai.token
```

## Web Admin UI

上海板已部署内置 Web 管理界面：

```text
http://<board-ip>:18090/
```

打开页面后在右上角输入 Bearer token 并点击 **Connect**。

当前页面支持：

```text
Dashboard 概览
Dashboard 显示 OS memory / CMM / model limit
Device Status 面板以紧凑摘要显示关键板卡状态，详细指标默认折叠
Model limit 可在 Dashboard 顶部选择并保存
模型列表
valid / invalid 状态
manifest 来源
模型大小与能力
active job 状态
确认 inferred manifest
启动模型
停止模型
切换模型
重启模型
在模型卡片内打开启动参数配置
启动参数：context / batch / ubatch / cache_ram / ctxcp / MTP / port
采样参数：temperature / top-k / top-p / min-p / repeat / presence / frequency
白天 / 黑夜主题切换
job 列表
job 日志查看，默认每秒自动刷新并跟随尾部
audit 记录查看
audit 固定高度滚动显示
Models 卡片区固定高度滚动显示
OpenAI-compatible API 信息展示
```

Device Status 面板包括：

```text
tempmon 在线状态
CMM
OS memory
NPU 最高温度
CPU 1 分钟负载
网络 RX/TX
llama-server 进程数
```

展开 **Show advanced device metrics** 后才显示 agent/BSP/chip、CPU 频率、
thermal zones、NPU clocks 和磁盘详情。

控制按钮只在 `enable_control=true` 时显示。只读部署仍可查看模型、job 与 audit。

模型运行后，页面顶部的 **OpenAI-compatible API** 区域会显示：

```text
OpenAI Base URL
Chat Completions endpoint
运行中模型实际广告的 model ID
可直接复制的 curl 请求
```

例如：

```text
Base URL: http://<board-ip>:18080/v1
Chat:     http://<board-ip>:18080/v1/chat/completions
Model ID: /mnt/models/Qwen3.5-0.8B-AX8860-llama.cpp/qwen3.5-0.8b-q8_0.gguf
```
