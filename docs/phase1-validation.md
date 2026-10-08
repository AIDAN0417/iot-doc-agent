# Phase 1 实机验证

2026-10-08，ESP-IDF `en/latest/esp32` API Reference 与 API Guides。
281 页（包括导航与迁移记录）、失败 0；正文 1,630 章节；5,912 块；200–393 Qwen tokens。
50 tokens 是相邻窗口重复的源 tokens；跨代码块边界额外恢复语言标签与围栏。

随机种子 42，抽查正文与原始 HTML 的真实锚点，5/5 有效：

| 原章节 | tokens | 正文核对 | 原始引用 |
|---|---:|---|---|
| FreeRTOS (Supplemental Features) > API Reference | 378 | StaticRingbuffer_t 与 RingbufferType_t | [章节](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/freertos_additions.html#api-reference) |
| Downloadable IDF Tools > idf_tools.py Script | 377 | 工具 feature 与 requirements 文件 | [章节](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/tools/idf-tools.html#idf-tools-py) |
| Building Multiple Configurations | 258 | CMake presets 与多个构建目录 | [章节](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-guides/build-system-v2/multiple-configurations.html#building-multiple-configurations) |
| Configuration Options Reference > Component config | 388 | BLE 日志配置符号 | [章节](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/kconfig-reference.html#component-config) |
| Bluetooth® HID Device API > API Reference | 387 | ESP_HIDD_CLOSE_EVT / ESP_HIDD_SEND_REPORT_EVT | [章节](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/bluetooth/esp_hidd.html#api-reference) |

先验证 sleep/GPIO/NVS/watchdog/ADC 的小样本 185 块后再全量抓取。
发现旧 ADC 页只有 meta refresh；抓取器将其指向的 scoped 新页入队，清洗不把迁移通知作为证据。
分词器和 embedding 均直接打开本地 snapshot 路径，避免库的远程元数据检查。
抓取清单保存 URL、UTC 抓取时间、字节数、SHA256 和链接队列；重跑复用校验通过的页面。
