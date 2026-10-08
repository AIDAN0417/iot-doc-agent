# IoT 设备问答 Agent — Codex 构建指令（全本地零成本版）

> 使用方式:把本文档全文作为第一条 prompt 发给 Codex,或保存为仓库根目录的 `AGENTS.md` 后让 Codex 按阶段执行。
> 本版本所有组件均在本地运行，**零 API 费用、断网可用**（初次下载模型与依赖时需要联网一次）。

---

## 1. 项目目标

构建一个面向 ESP32 开发者的 RAG 问答 Agent，**全本地运行**。用户用自然语言提问（中英文均可），如"ESP32 怎么配置深度睡眠省电？"，Agent 检索官方文档后给出准确回答，附代码示例和文档引用，并能解释关键 trade-off（如功耗 vs 唤醒延迟）。

**一句话验收**：30 个预设测试问题中，至少 24 个回答准确且引用来源正确。

---

## 2. 技术栈（已锁定，不要自行更换）

| 组件 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.11+ | 全类型注解 |
| 对话模型 | Ollama `qwen2.5:7b-instruct`（约 4.7GB） | 中英双语好，本地 7B 档综合最稳；内存不足时降级 `qwen2.5:3b-instruct`，见 §3 |
| Embedding | sentence-transformers `BAAI/bge-m3`（约 2.3GB） | 多语言，支持中文问题检索英文文档；极弱机器可降级 `all-MiniLM-L6-v2`（纯英文场景） |
| 向量库 | FAISS `IndexFlatIP`（`faiss-cpu`） | 无需外部服务 |
| Agent 机制 | **手写 ReAct 循环 + 严格 JSON 解析**（见 §7） | 不依赖模型原生 function calling（7B 小模型工具调用不稳定），不引入 LangChain 等重框架 |
| 前端 | Streamlit | 单文件 `src/app.py` |
| 打包 | Docker（仅打包 App）+ Ollama 原生安装 | Ollama 不进容器，见 §8 |

**不允许引入任何云端 API、任何付费服务。** 配置（模型名、top-k、路径、Ollama 地址）全部集中在 `src/config.py`，支持环境变量覆盖。

---

## 3. 硬件要求与预期性能（立项前先确认）

| 配置 | 对话模型 | 预期表现 |
|---|---|---|
| 16GB+ 内存（推荐） | `qwen2.5:7b-instruct` | CPU 推理约 8–20 tok/s，可用；有独显（NVIDIA/Apple Silicon）更快 |
| 8–12GB 内存 | `qwen2.5:3b-instruct` | 能跑，回答质量下降，评测标准不变但允许更简洁 |
| <8GB 内存 | 不建议 | 直接告知用户硬件不足，停止搭建 |

`setup.sh`（见 §5 Phase 0）必须在开始时检测内存并给出模型建议。Ollama 参数：`num_ctx=8192`，`keep_alive=30m`。

**上下文预算（7B 模型必须遵守）**：系统 prompt ≤800 tokens；检索 top-5 chunks，每块 ≤400 tokens；对话历史只保留最近 3 轮。超预算直接截断最旧历史，不许让 prompt 爆 context。

---

## 4. 环境准备（一次性，Codex 要写成脚本）

`setup.sh` 必须完成以下全部检查与安装，任何一步失败要给出中文报错与修复建议：

1. 检查 Python ≥3.11、`pip` 可用；
2. `pip install -r requirements.txt`（`requirements.txt` 必须 pin 主版本号，如 `faiss-cpu>=1.8`）；
3. 检查 `ollama` 命令是否存在，不存在则提示去 https://ollama.com/download 安装后重跑；
4. 检查 `ollama serve` 是否在运行（`curl localhost:11434`），未运行则提示启动；
5. 按 §3 的内存检测结果 `ollama pull` 对应模型（对话模型 + 无需 pull embedding，pip 包自带下载）；
6. 打印总结：各组件状态、模型大小、预计磁盘占用。

---

## 5. 仓库结构

```
iot-doc-agent/
├── AGENTS.md               # 本文件
├── README.md               # Phase 4 写，必须含"硬件要求"一节
├── requirements.txt        # pin 主版本号
├── setup.sh                # §4 一键环境脚本
├── Dockerfile
├── docker-compose.yml
├── src/
│   ├── config.py           # 全部配置集中处
│   ├── ingest/
│   │   ├── crawl.py
│   │   ├── clean.py
│   │   └── chunk.py
│   ├── rag/
│   │   ├── embed.py        # bge-m3 封装，CPU 推理
│   │   ├── index.py
│   │   └── retrieve.py     # 向量召回 top-20 → 关键词加权 → 取 top-5
│   ├── agent/
│   │   ├── llm.py          # Ollama 调用唯一入口（/api/chat），超时 120s，重试 2 次
│   │   ├── tools.py        # search_docs / estimate_power
│   │   ├── prompts.py      # 短促明确的 prompt（见 §7）
│   │   └── loop.py         # ReAct 主循环
│   ├── eval/
│   │   └── run_eval.py
│   └── app.py              # Streamlit
├── eval/questions.json     # 30 题：中英各半，含期望引用章节
├── data/                   # gitignore
└── tests/
```

---

## 6. 分阶段交付（严格按顺序，每阶段结束必须可运行）

### Phase 0 — 环境脚本
按 §4 写出 `setup.sh` 并在干净思路下自检逻辑（每条检查命令写出预期输出示例注释）。交付：在 README 留空位，Phase 4 填实测结果。

### Phase 1 — 数据管道
1. `crawl.py`：抓 `https://docs.espressif.com/projects/esp-idf/en/latest/esp32/` 下 API 参考与技术概念页。遵守 robots，限速 1 req/s，失败重试 3 次，存 `data/raw/`，**记录抓取清单**（URL、时间、字节数）到 `data/crawl_manifest.json`，断点可续抓。
2. `clean.py`：去导航栏/页眉页脚/广告，保留正文与代码块（含语言标记）。
3. `chunk.py`：按 h1/h2 分块，每块 **200–400 tokens**（小模型上下文小，块必须更小），相邻块 50 tokens 重叠。每块元数据：`{section_path, url, chunk_id}`。
4. 交付验证：随机抽 5 块，章节路径与 URL 正确；统计总块数打印出来。

### Phase 2 — RAG 核心
1. `embed.py` + `index.py`：bge-m3 生成 embedding（CPU batch=32），FAISS 建索引持久化到 `data/index/`。**索引构建必须可断点续跑**（分批写临时文件，完成后合并）。
2. `retrieve.py`：向量召回 top-20 → 用问题中的 API 名/寄存器名/中文关键词做加权提权 → 取 top-5。
3. `prompts.py` 系统 prompt 模板（针对小模型，短而硬）：
   - 只用检索文档回答，禁止编造 API 名、参数、寄存器地址；
   - 每个事实断言后标引用 `[章节名](url)`；
   - 文档不足时说"文档中没有找到相关内容"，给最接近的章节建议；
   - **用提问者的语言回答**（中文问中文答，英文问英文答），引用保留英文原章节名。
4. 交付验证：`eval/questions.json` 前 10 题，8/10 以上引用正确。

### Phase 3 — Agent 化
1. `tools.py`：
   - `search_docs(query)`：调 retrieve.py；
   - `estimate_power(mode, duration_h)`：功耗估算。电流参数表硬编码在工具内，**数值必须来自 ESP32 datasheet 典型值，代码注释注明出处章节**；输出估算电量 + 一句话解释。
2. `loop.py`：按 §7 的 ReAct 协议实现。多轮对话保留最近 3 轮。
3. 交付验证：3 轮追问场景上下文不丢；工具参数解析正确。

### Phase 4 — 产品化与文档
1. `app.py`：Streamlit 聊天界面，展示引用链接与"思考过程"折叠区，支持清空历史；**首屏显示当前模型与硬件状态**（从 config 读取）。
2. `Dockerfile` + `docker-compose.yml`：见 §8。
3. `README.md`：一句话介绍、架构图（mermaid）、**硬件要求**、运行步骤（含 `setup.sh`）、3 个问答截图位、测试得分。
4. `eval/run_eval.py`：跑 30 题，输出引用准确率、拒答率、平均响应时长，打印到终端并存 `eval/report.txt`。

---

## 7. 小模型专项设计约束（本版本成败关键）

1. **ReAct 输出协议**：模型每轮只输出严格 JSON：`{"thought": "...", "action": "search_docs|estimate_power|final", "action_input": {...}}`。`final` 时 `action_input` 为 `{"answer": "..."}`。
2. **JSON 修复**：解析失败时，把报错信息拼回 prompt 重试一次；仍失败则降级为"直接回答"（跳过工具），不许崩溃、不许死循环。
3. **Prompt 纪律**：系统 prompt 不超过 800 tokens；指令用短句；给 1 个完整 few-shot 示例（放在 `prompts.py` 注释中说明，不计入每次请求则更好——Codex 自行决定放系统 prompt 还是只在文档说明，**但必须实测 JSON 合规率**）。
4. **最大 4 轮工具调用**，超轮直接 final。
5. `llm.py` 超时 120 秒，重试 2 次；Ollama 连不上时报错必须提示"先运行 `ollama serve`"。

---

## 8. Docker 与网络（最容易踩的坑）

- Ollama **原生安装**在宿主机，不进容器（容器内跑 GPU/大模型内存开销不可控）。
- App 容器内通过 `http://host.docker.internal:11434` 访问 Ollama。
- **Linux 宿主机**必须在 `docker-compose.yml` 加 `extra_hosts: ["host.docker.internal:host-gateway"]`，否则容器内 DNS 解析失败——这是本项目最高频翻车点，Codex 写完 compose 文件后必须自查这一行存在。
- `config.py` 中 Ollama 地址默认 `http://localhost:11434`，Docker 环境下由 compose 的环境变量覆盖为 `host.docker.internal`。

---

## 9. 代码规范

- 公开函数全类型注解 + 一行 docstring；
- 日志用 `logging`，禁止残留 `print` 调试；
- 配置只在 `src/config.py`；
- 每个 `src/` 子模块至少 1 个单测；
- 所有"魔法数字"（top-k、温度、超时）必须来自 `config.py`，禁止散落。

---

## 10. 故障排查手册（README 必须收录精简版）

| 现象 | 原因 | 修法 |
|---|---|---|
| `ollama: command not found` | 未安装 | 去 ollama.com/download 安装后重跑 `setup.sh` |
| `Connection refused` localhost:11434 | Ollama 服务没起 | 终端运行 `ollama serve`（或检查是否开机自启） |
| 容器内连不上 Ollama | 缺 host-gateway | 检查 compose 的 `extra_hosts`（§8） |
| 进程被杀 / OOM | 内存不足 | 换 `qwen2.5:3b-instruct`，见 §3 |
| 回答极慢 | CPU 推理 + 上下文过大 | 减小 `num_ctx`、关其他大程序；独显用户确认 Ollama 用到 GPU（`ollama ps` 看 PROCESSOR） |
| Apple Silicon 装不上 faiss-cpu | wheel 缺失 | 用 conda 装或 `pip install faiss-cpu` 换源重试，写进 README |
| 中文问题检索跑偏 | embedding 模型不对 | 确认用的是 bge-m3，不是 MiniLM |
| 模型老输出非 JSON | prompt 被截断或过长 | 检查 §3 上下文预算，减少 chunk 数到 3 再测 |
| 首次 embedding 巨慢 | bge-m3 下载中 | 正常，约 2.3GB 一次性；看终端进度 |

---

## 11. 禁止事项

- 禁止任何云端 API、付费服务、硬编码密钥（本版本根本不该出现 Key 的概念）；
- 禁止编造引用、URL、API 签名、电流参数；不确定走拒答；
- 禁止提交 `data/`、`.env`、模型文件到 git；
- 未经确认不得更换 §2 技术栈、不得引入新重型依赖；
- 禁止把性能问题藏起来：实测 tok/s 必须写进 README。

---

## 12. 最终验收清单

- [ ] `bash setup.sh` 在新机器思路下一次跑通（含内存检测与模型 pull）；
- [ ] `docker compose up` 后 `localhost:8501` 可用，Ollama 连接正常；
- [ ] 30 题：引用准确率 ≥80%，零编造引用；
- [ ] 3 轮追问上下文正确；JSON 解析失败有降级不崩溃；
- [ ] README 含硬件要求、架构图、运行步骤、实测 tok/s；
- [ ] `git log` 干净，无 data、无模型文件。

---

## 13. 给 Codex 的启动语（复制即用）

> 按 AGENTS.md 从 Phase 0 开始执行。全本地零成本，不许引入任何云 API。每完成一个 Phase 停下来告诉我验证结果，确认后再进下一 Phase。遇到文档结构或模型行为与预期不符，先小样本验证再全量跑。
