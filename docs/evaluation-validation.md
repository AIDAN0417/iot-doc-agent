# 最终 30 题人工复核

2026-10-08，完整 5,912 块语料、Windows 原生 Qwen 7B Q4_K_M、bge-m3 CPU。固定题集为中文 15 题、英文 15 题；每题使用新的 Agent，三轮历史验证独立执行。

**人工复核：24/30（80%），达到 AGENTS.md 的 24 题门槛。** 严格自动检查为 **21/30（70%）**，不得将两者混称。人工复核检查回答是否解决核心问题、事实是否正确、真实引用是否支持核心结论；第 20 / 30 题是预设的不存在 API，正确拒答计为通过。第 2 / 18 题即使结论基本正确，也因引用未支撑核心比较而判未通过。

本机原始回答和工具输入保存在被忽略提交的 `eval/results.json`；完整终端统计保存在 `eval/report.txt`。此次结果 fingerprint 为 `c1b31e92783b8b8271bfb9a5bfd9db0a5a02207c709367ac943f5dd50d9ad3d7`。本记录未手工替换模型回答，也未修改固定题目以抬高得分。

## 真实测量

| 指标 | 最终运行 |
|---|---|
| 自动关键事实 + 预期章节 | 21/30，70.0% |
| 可回答题预期引用章节命中 | 24/28，85.7%（不含两个预设拒答题） |
| 人工准确回答且来源支持 | 24/30，80.0% |
| 伪造 / 越界链接 | 0 |
| 拒答 | 4/30，13.3%；其中 2 个是预期拒答，2 个是不必要拒答 |
| 平均响应 | 2.99 s，包含检索与重试，不含首次加载 embedding |
| 实际生成 | 57.23 tok/s，生成 token 总数 / Ollama eval_duration 总秒数 |
| 合法 ReAct JSON | 40/40，100% |
| 格式 / 语言 / 引用或 API 证据校验后降级 | 5 次；合法 JSON 不代表语义或证据正确 |
| 测试 | 45 项通过，包括断点损坏、预算、修复与降级、真实错误回归、UI 首屏 |

固定 seed 与 temperature=0 减少波动；这些分数只描述此模型、当前文档快照和本次运行。不要把链接白名单、关键词命中或合法 JSON 当作完整事实保证。

## 逐题结论

| 题号 | 自动 | 人工 | 原文核对结论 |
|---|---|---|---|
| 1 | 通过 | 通过 | `esp_deep_sleep(5000000)`，单位微秒，实际 API Reference 支持 |
| 2 | 未通过 | 未通过 | 睡眠状态比较基本正确，但 Deep-sleep Wake Stubs 引用不支持 Light-sleep 部分 |
| 3 | 未通过 | 通过 | Wi-Fi 专题的 Choosing Low Power Mode 支持 Modem-sleep + 自动 Light-sleep；自动预期只列 Sleep Modes |
| 4 | 通过 | 通过 | `esp_sleep_get_wakeup_cause()` |
| 5 | 通过 | 通过 | 本题 RTC_DATA_ATTR 的常规 RTC SLOW 保留域正确；配置例外仍需查看原文 |
| 6 | 通过 | 通过 | 时间单位 microseconds |
| 7 | 未通过 | 通过 | 明确只可使用带 RTC 功能的 GPIO，并列出 ESP32 的对应引脚；自动短语未覆盖该正确表述 |
| 8 | 通过 | 通过 | `esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL)` |
| 9 | 未通过 | 通过 | 正确调用 `rtc_gpio_isolate(GPIO_NUM_12)`，引用实际 GPIO RTC API 定义；自动预期只列 Sleep Modes |
| 10 | 通过 | 通过 | 直接深睡不保持 Wi-Fi，Modem-sleep 作为保留连接的替代方案；更详细的自动 Light-sleep 配置需看原文 |
| 11 | 通过 | 通过 | GPIO34–39 输入专用，无内部可配置上拉 / 下拉 |
| 12 | 通过 | 通过 | GPIO6–11 通常用于模块 SPI flash / PSRAM |
| 13 | 通过 | 通过 | `gpio_config()` 覆盖现有 IO 配置 |
| 14 | 未通过 | 通过 | ADC Continuous Hardware Limitations 原文明确 Wi-Fi / ADC2 保护与冲突；自动只接受 GPIO / ADC Oneshot 页面 |
| 15 | 未通过 | 未通过 | 只给 `(NVS_KEY_NAME_MAX_SIZE-1)`，未回答所问的 15 个字符 |
| 16 | 未通过 | 未通过 | 不必要拒答；正确应为 `nvs_commit()`。此前生成的不存在调用已被 API 证据校验拦截 |
| 17 | 未通过 | 未通过 | 不必要拒答；应给 `esp_task_wdt_add(NULL)` 与 `esp_task_wdt_reset()` |
| 18 | 通过 | 未通过 | IWDT / TWDT 结论正确，但仅引用 TWDT 小节，缺少 IWDT 证据出处 |
| 19 | 通过 | 通过 | 实际工具：0.01 mA × 24 h = 0.24 mAh，datasheet 典型条件与整板限制明确 |
| 20 | 通过 | 通过 | 不存在 `esp_quantum_sleep_enable()`，按整个语料查不到该名称后拒答 |
| 21 | 通过 | 通过 | 安装服务 `gpio_install_isr_service`，添加引脚处理器 `gpio_isr_handler_add` |
| 22 | 通过 | 通过 | `esp_adc/adc_oneshot.h` |
| 23 | 通过 | 通过 | `adc_oneshot_read` |
| 24 | 通过 | 通过 | 不可在 ISR 调用；mutex / ISR 限制与原文一致 |
| 25 | 通过 | 通过 | NVS 为 flash 键值存储，适合相对稳定的小值，不适合不断累积的大数据 |
| 26 | 通过 | 通过 | 默认分区初始化 `esp_err_t nvs_flash_init(void)` |
| 27 | 通过 | 通过 | 默认监控各 CPU Idle Task；busy loop 不让出 CPU 会导致喂狗受阻和超时 |
| 28 | 未通过 | 未通过 | 错答 `esp_task_wdt_delete_user`；任务应使用 `esp_task_wdt_delete(TaskHandle_t task_handle)`，两个实体不同 |
| 29 | 通过 | 通过 | 实际工具：0.8 mA × 10 h = 8 mAh，排除整板其他消耗 |
| 30 | 通过 | 通过 | 英文正确拒答不存在 API，并给最近真实章节 |

## 实际查验的官方来源

- [Sleep Modes / API 与电源域](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/sleep_modes.html)
- [Choosing Low Power Mode in Wi-Fi Scenarios](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/low-power-mode/low-power-mode-wifi.html#choosing-low-power-mode-in-wi-fi-scenarios)
- [GPIO & RTC GPIO](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/peripherals/gpio.html)
- [ADC Continuous Hardware Limitations](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/peripherals/adc/adc_continuous.html#hardware-limitations)
- [ADC Oneshot API](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/peripherals/adc/adc_oneshot.html)
- [NVS Introduction / API Reference](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/storage/nvs_flash.html)
- [Watchdogs Overview / TWDT / API Reference](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/wdts.html)
- [ESP32 Series Datasheet Table 4-2，PDF 第 30 页](https://www.espressif.com/sites/default/files/documentation/esp32_datasheet_en.pdf#page=30)

API 函数名加权与评测都使用完整标识符匹配，不能用 `_delete_user` 的前缀通过 `_delete` 的检查。模型输出中的 API 调用必须在检索文本出现；显式 C 声明还需匹配文档签名。此防护能拦截名称 / 参数编造，仍不能自动判断任务与用户的语义差别或每一条事实的引用完整度，上述六道失败题完整保留。
