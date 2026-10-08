# IoT 文档问答 Agent

面向 ESP32 开发者的本地 RAG 问答 Agent；按 `AGENTS.md` 分阶段构建，模型推理使用本机 Ollama，零云端推理 API 费用。

当前交付：**Phase 0 环境脚本**。数据采集、检索、ReAct、聊天界面、Docker 打包均待后续阶段确认后实现。

## 硬件要求

| 物理内存 | 对话模型 | 选择方式 |
|---|---|---|
| 至少 16 GiB | `qwen2.5:7b-instruct` | 自动推荐 |
| 8–16 GiB（不含 16） | `qwen2.5:3b-instruct` | 自动降级 |
| 少于 8 GiB | 不支持 | 立即停止，不安装依赖/模型 |

Embedding 默认 `BAAI/bge-m3`，CPU 推理。初始化要求项目所在磁盘至少留出 15 GiB，覆盖虚拟环境、模型与下载临时空间；Ollama 模型目录若在另一块磁盘，也需预留对话模型空间。模型大小估算：7B 约 4.7 GB、3B 约 1.9 GB、bge-m3 约 2.3 GB，脚本成功后会报告 Ollama 的实际模型大小。GPU 加速、tok/s 和回答效果留到实际运行后测量。

## 环境与运行步骤

使用 Python 3.11+ 和原生 Ollama。依赖装在项目的 `.venv`，embedding 缓存放在忽略提交的 `data/models/embedding`。首轮需要联网下载依赖与模型；运行问答时不使用云 API。**当前初始化脚本可运行，聊天应用尚未实现。**

1. 从 [Ollama 官方下载页](https://ollama.com/download) 安装 Ollama。安装后重开终端，确认 `ollama --version` 可用。
2. 启动本地 Ollama 服务，并禁用云功能。

Windows PowerShell，在单独终端运行：

```powershell
$env:OLLAMA_NO_CLOUD = '1'
ollama serve
```

Linux/macOS，在单独终端运行：

```bash
OLLAMA_NO_CLOUD=1 ollama serve
```

若已由桌面应用启动服务，先退出 Ollama 应用，再用上述方式启动；避免占用同一端口。Ollama 模型存储默认由宿主机服务管理，可在启动服务前设置 `OLLAMA_MODELS` 指向有充足空间的目录。

3. 在仓库根目录运行初始化。Linux/macOS 或 Windows Git Bash：

```bash
bash setup.sh
```

原生 Windows（无需 Bash，也不需修改 PowerShell 执行策略）：

```powershell
python -m src.bootstrap
```

两种入口使用相同实现：检测内存与 Python/pip → 预查 Ollama → 创建 `.venv` → 安装 CPU Torch 与 requirements → 验证依赖 → 拉取 Qwen → 预下载 embedding → 强制离线加载验证 → 输出总结。命令旁附预期输出注释，任何失败返回非零退出码并显示中文修复建议。重复执行可复用环境与模型缓存。

4. 已初始化后，执行只读检查。不会安装、拉取模型或创建缓存目录：

```bash
bash setup.sh --check
# 原生 Windows：python -m src.bootstrap --check
```

`--check` 仍需本机 Ollama 服务运行；它只访问本机健康状态和模型列表。Embedding 会设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、`local_files_only=True`，验证缓存完整。

## 配置

所有配置在 `src/config.py`。支持环境变量：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `IOT_CHAT_MODEL` | 根据内存选择 | 仅支持指定的 Qwen 7B/3B；允许强机器选 3B |
| `IOT_EMBED_MODEL` | `BAAI/bge-m3` | 可显式降为 `sentence-transformers/all-MiniLM-L6-v2`，仅限英文场景 |
| `IOT_OLLAMA_URL` | `http://localhost:11434` | 仅允许 localhost、回环 IP 或 Docker 的 host.docker.internal |
| `IOT_DATA_DIR` | `data` | 相对路径以仓库根目录为基准 |
| `IOT_NUM_CTX` | `8192` | 后续 Ollama 请求上下文上限 |
| `IOT_KEEP_ALIVE` | `30m` | 后续 Ollama 请求模型保留时间 |

`setup.sh` 可用 `PYTHON` 指定解释器。禁止云模型名与外部 Ollama 地址；无需 API Key。

## Phase 0 验证

2026-10-08 本机检查：Windows、Python 3.11.9、pip 24.0、物理内存约 31.43 GiB，推荐 7B。磁盘可用约 190.87 GiB。

- 自动化测试：15/15 通过，覆盖内存/磁盘门槛、模型与地址约束、真实回环 HTTP 健康检查、失败提示及离线检查无下载。完整安装成功分支使用模拟依赖/模型，不代表真实模型已安装。
- 原生入口实测：`python -m src.bootstrap --check` 正确识别内存/Python/pip，在缺少 Ollama 时返回 1 并显示官方下载链接及重跑建议。
- 依赖实测：已安装到 `.venv`，`pip check` 通过；所有声明依赖导入通过，SentenceTransformer 的离线/缓存参数签名检查通过，FAISS CPU 内积索引小样本检索通过。Torch 为 `2.14.1+cpu`，未下载 embedding 权重。
- Bash 入口实测：当前机器缺少可用 Bash，未实际执行。
- 完整安装/模型下载/embedding 离线加载：待安装并启动 Ollama 后实测。

测试仅使用 Python 标准库，无需先下载模型：

```bash
python -m unittest discover -s tests -v
```

## 故障排查

| 现象 | 修复 |
|---|---|
| Python 版本低或缺 pip | 安装 Python 3.11+；运行 `python -m ensurepip --upgrade` |
| 找不到 `ollama` | 安装官方 Ollama，重开终端再运行 |
| 本机 11434 连接失败 | 先运行 `ollama serve`，检查 `IOT_OLLAMA_URL` |
| 内存不足 | 少于 8 GiB 停止；8–16 GiB 使用指定 3B 模型 |
| pip/模型下载失败 | 检查网络与磁盘，重跑复用缓存；首次下载须联网 |
| embedding 离线加载失败 | 缓存不完整，联网重跑初始化；pip 包不含模型权重 |
| Apple Silicon 无 faiss wheel | 在 Python 3.11+ 的 conda 环境用 `conda install -c pytorch faiss-cpu`，再将该环境的可用包按需提供给项目环境；具体安装路径在实际失败后验证 |

## 后续阶段实测结果占位（Phase 4 填写）

- 架构图：待完成。
- Docker 与 `host-gateway`：待 Phase 4。
- 问答截图 1 / 2 / 3：待实际应用运行。
- 30 题引用准确率、拒答率、平均响应时长：待评测。
- 实测 tok/s、JSON 合规率、三轮追问结果：待真实模型运行。

## 实现参考

- [Ollama 本地服务与关闭云功能](https://docs.ollama.com/faq)
- [Ollama 本地模型列表](https://docs.ollama.com/api/tags)
- [SentenceTransformer 模型缓存与离线加载参数](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html)
- [PyTorch 官方 CPU 安装源](https://pytorch.org/get-started/locally/)
- [FAISS 官方安装说明](https://github.com/facebookresearch/faiss/blob/main/INSTALL.md)
