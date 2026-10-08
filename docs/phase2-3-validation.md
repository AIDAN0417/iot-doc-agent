# Phase 2 / 3 实测记录

验证日期：2026-10-08。模型为原生 Ollama 0.40.1 的 `qwen2.5:7b-instruct` Q4_K_M；31.43 GiB 内存、Core Ultra 9 275HX、RTX 5060 Laptop GPU 8 GiB。Embedding 为 CPU `BAAI/bge-m3`，未调用云推理服务。

## 完整索引

- 输入 5,912 块，Qwen 分词后每块 200–393 tokens。
- `FAISS IndexFlatIP` 为 5,912 行 × 1,024 维，与元数据逐行对应。
- 全量向量均为有限值；L2 范数实测 0.999999821–1.000000119。
- CPU batch=32；本机 24 线程比 8 / 16 线程的小样本吞吐更好，全量使用 24 线程。
- 构建中断后复用 SHA256 验证通过的批次；完整发布后重跑直接复用索引。单测另验证损坏批次会重新生成，未完成索引不会覆盖活动版本。
- 数据、批次、权重、索引只在被 gitignore 的 `data/` 中。

## 前十题直接 RAG

最终版本、全量语料、无 Agent 追加工具：自动关键词与预期章节检查 **6/10**，预期引用章节命中 **8/10**，达到 Phase 2 的引用门槛；逐题原文复核 **7/10 回答正确且来源支持**。自动检查把第 3 题的有效 Wi-Fi 专题章节判为非预期章节，第 7 题的正确 RTC 限制表述也未被关键词规则覆盖。没有伪造或越界引用。Agent 的完整质量验收另见 `evaluation-validation.md`。

| 题号 | 人工复核 | 依据 / 问题 |
|---|---|---|
| 1 | 通过 | `esp_deep_sleep(5000000)` 为文档列出的直接定时深睡调用，参数微秒 |
| 2 | 未通过 | 结论基本正确，但引用 Deep-sleep Wake Stubs Introduction 不支持 Light-sleep 部分 |
| 3 | 通过 | Wi-Fi 低功耗专题支持 Modem-sleep 配合自动 Light-sleep 保持连接 |
| 4 | 通过 | `esp_sleep_get_wakeup_cause()` |
| 5 | 未通过 | 错将配置例外当默认，并声称 `esp_sleep_pd_config()` 改变变量放置位置 |
| 6 | 通过 | `time_in_us` 为微秒 |
| 7 | 通过 | 明确只允许具有 RTC 功能的 GPIO，并给出 ESP32 的 RTC GPIO 范围 |
| 8 | 通过 | `esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL)` |
| 9 | 通过 | `rtc_gpio_isolate(GPIO_NUM_12)` |
| 10 | 未通过 | 正确说明直接深睡不保持 Wi-Fi，但漏答所问替代方案 |

此轮平均响应 2.77 s，实际模型生成 58.57 tok/s。真实模型存在遗漏和错误概括；链接白名单只能防止编造 URL，不能证明事实正确。

## 实际三轮追问

同一 Agent 实例的连续输入与输出关键事实：

1. “ESP32 如何设置 5 秒后从深度睡眠唤醒？” → `esp_deep_sleep(5000000)`。
2. “那时间参数的单位是什么？” → 上一轮深睡调用的单位是微秒。
3. “那怎么把所有唤醒源关闭？” → `esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL)`。

三轮均引用原始 Sleep Modes 章节，无降级；每轮工具输入包含当前追问和此前问题，历史实际保留 3 个完整问答。

## 实际功耗工具

| 输入语言 | 解析结果 | 工具输出 |
|---|---|---|
| 中文 | `mode=deep_sleep, duration_h=24` | 0.01 mA × 24 h = **0.24 mAh** |
| 英文 | `mode=light_sleep, duration_h=10` | 0.8 mA × 10 h = **8 mAh** |

最终数值由工具格式化，避免模型改写算术结果。电流来自实际查看过的 ESP32 Series Datasheet v5.3 **Table 4-2 / PDF 第 30 页**；两种输出都说明芯片典型条件和整板外设/稳压器限制。Wi-Fi TX 的 Table 5-4 / PDF 第 52 页条件亦经表格图像核对。

上述 5 次实际问答共有 **7/7 次合法 ReAct JSON**，无修复降级。系统 prompt 实测 **501 Qwen tokens**，小于 800；完整 chat template 在发送前计算预算。运行期间拦截 socket 连接，记录仅为 `127.0.0.1:11434` 与 `[::1]:11434`，模型、分词器、FAISS 从本地缓存读取。

## 故障路径

单测覆盖重复 JSON 键、NaN、错误动作和功耗参数、单次修复、修复失败后的直接回答 / 诚实拒答、最多四次工具操作、历史截断、未知 API 拒答、错误 URL 与云地址拒绝。真实 GPU 问答测量和这些故障模拟测试分别记录，不以模拟结果代替真实模型质量。
