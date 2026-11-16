# AXERA Model Manager

AX8860 单板模型管理平台，用来管理 `/mnt/models` 下的 llama.cpp 模型包，
并提供模型发现、参数配置、启动停止、日志、审计和板卡状态观测。

## 定位

```text
Model Manager: 面向运维 / 用户的单板模型管理平台
LiteWebUI:     面向用户的聊天与轻量诊断前端
tempmon:       只读设备监控 agent
```

Model Manager 只做管理平面；推理流量继续由 llama-server 的
OpenAI-compatible API 直接承载。

## 核心原则

1. 默认模型根目录为 `/mnt/models`，每个模型一个目录，路径可配置。
2. Web 前端不直接执行 shell，不直接 SSH。
3. 启动参数必须是结构化 profile，不允许提交任意命令。
4. llama-server 由 systemd transient unit 托管，Manager 重启不影响推理。
5. 停止、重启和切换前执行 `/slots` IN USE guard。
6. 控制动作写入 audit。
7. tempmon 保持只读。

## 端口

| 服务 | 默认端口 | 说明 |
|---|---:|---|
| tempmon | 8080 | 只读设备监控 |
| llama-server | 18080-18089 | 推理端口池 |
| model-manager | 18090 | 管理 API 与 Web Admin UI |

## 当前功能

### 模型发现与校验

- 扫描模型目录，识别 `llama-server` / `llama-tts`
- 读取和校验 `model.json`
- 无 manifest 时生成 inferred draft，用户确认后保存在 Manager 数据目录
- 校验 binary、GGUF、mmproj、chat template、kernels
- 路径穿越和 symlink escape 防护

### 生命周期管理

- start / stop / restart / switch
- 多模型并行，端口池自动分配
- model limit 达到后可由用户确认 force start
- systemd-run 启动 `ax-model-*` transient unit
- job 状态持久化，Manager 重启后恢复
- 孤儿 runtime 扫描、Adopt、Stop
- 启动前 CMM / OS memory / NPU 温度阈值检查

### Profile

- 保存、删除、复制、重置、Diff
- 启动命令 Preview
- Context 档位固定为 16K / 32K / 64K / 128K / 256K
- 支持 batch、ubatch、cache_ram、ctxcp、MTP、端口和采样参数

### Web Admin UI

- Dashboard 与紧凑 Device Status
- OS memory、CMM、NPU 温度、CPU、网络、磁盘、llama 进程观测
- 模型卡片与详情
- 每个模型卡片内打开启动参数配置
- Jobs、日志尾部跟随、错误高亮、下载
- Audit 固定高度滚动显示
- 明暗主题
- 运行时展示 OpenAI Base URL、Chat endpoint、model ID 和 curl 示例

## 本地开发

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
.venv/bin/pip install -e backend

.venv/bin/python -m pytest backend/tests -q
./scripts/dev_server.sh
```

默认地址：<http://127.0.0.1:18090>。开发 token 为 `local-dev-token`。

如果端口被占用：

```bash
AX_MODEL_MANAGER_PORT=18092 ./scripts/dev_server.sh
```

## 板端安装

以下命令在板端的源码目录中以 root 执行：

```bash
# 首次安装；已有 config 和 token 会保留
./scripts/install.sh \
  --model-root /mnt/models \
  --data-dir /var/lib/axera-model-manager

# 升级；只停止 Manager，不停止已经运行的 ax-model-* 推理服务
./scripts/upgrade.sh

# 查看与恢复升级备份
./scripts/rollback.sh --list
./scripts/rollback.sh

# 卸载；默认保留 config、数据、日志和推理 runtime
./scripts/uninstall.sh

# 确认没有推理任务后，停止 runtime 或清理状态
./scripts/uninstall.sh --stop-runtimes
./scripts/uninstall.sh --stop-runtimes --purge
```

安装位置：

```text
/opt/axera-model-manager
/etc/axera/model-manager/config.json
/var/lib/axera-model-manager
```

systemd 服务：

```text
axera-model-manager.service
axera-model-manager-rotate.timer
axera-model-manager-rotate.service
ax-model-*.service                  # 模型 transient unit
```

板端镜像可能没有 `logrotate`。因此安装了兼容 logrotate 配置，同时内置
每小时执行的 copytruncate 旋转 timer：单个 job log 超过 50 MiB 时压缩，
默认保留 14 个压缩文件。

## 打包与回归

```bash
./scripts/package.sh

AX_MODEL_MANAGER_TOKEN="$TOKEN" \
  ./scripts/regression.sh http://<board-ip>:18090
```

release 包不包含 `.git`、`.venv`、`dist`、`.secrets` 和本地 fixture。

## 上海板部署状态

```text
Model Manager API: http://<board-ip>:18090
部署路径: /opt/axera-model-manager
配置: /etc/axera/model-manager/config.json
状态数据: /var/lib/axera-model-manager
```

访问控制 API 需要 Bearer token。token 在部署时生成，不写入 Git 仓库。

## 暂缓项

- 安全 / 权限体系：当前处于调试和客户送测阶段，先保持简单 token 认证
- 资源准入策略：只保留启动前阈值检查和用户确认 force start
- 多板 Central Console：当前产品定位为单机，不开发
- 模型下载 / 更新：优先级提高，计划支持直接输入 Hugging Face repo 地址并做差异化更新
