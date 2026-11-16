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

尚未实现：

- 启动 / 停止 / 重启 / 切换
- systemd 进程托管
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
