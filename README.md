# LUT Tool — 3D LUT 应用与 AI 风格评估工具

## 概述

- **C 模块 (`lut_tool.c`)** — ANSI C 编写的高性能 3D LUT 引擎
  - 解析 `.cube` 格式 LUT 文件，四面体插值应用 LUT
- **Python AI 模块 (`engine.py` + `app.py`)**
  - 自动发现 `.cube` 文件（含 `.zip` 内）
  - 调用 C 工具生成水印效果图 → 提取色彩统计 → AI 评估
  - **TUI** (curses) 或 **CLI** 双模式
  - **兼容所有 OpenAI API 格式的提供商**

## 项目结构

```
lut-tool/
├── Makefile            # ANSI C 编译
├── README.md           # 本文档
├── lut_tool.c          # C 源码 (ANSI C, BSD Allman 风格)
├── engine.py           # 核心引擎 (LUT 发现/处理/色彩统计/API 调用)
├── app.py              # 主入口 (TUI + CLI 双模式)
```

## 编译 C 工具

```bash
cd lut-tool
make
```

要求: `gcc` + `libm`

## 运行

### TUI 模式 (默认)

```bash
python app.py
```

TUI 界面操作:
| 按键 | 功能 |
|------|------|
| `1` | 开始评估 |
| `2` | 查看/编辑配置 |
| `3` | 导出 JSON |
| `R` | 重新扫描 LUT |
| `Q` | 退出 |
| `1-9` | 查看对应 LUT 详情 |
| `ESC` | 返回上级 |

### CLI 模式

```bash
python app.py --cli --image photo.jpg
python app.py --cli -i photo.jpg --json          # 导出 JSON
python app.py --cli --help                        # 查看全部参数
```

## 环境变量 (API 配置)

本工具兼容所有 **OpenAI API 格式** 的提供商:

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `OPENAI_API_KEY` | API 密钥 | — |
| `OPENAI_BASE_URL` | API 地址 | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | 模型名 | `gpt-4o` |

### 示例: 使用不同提供商

```bash
# OpenAI
export OPENAI_API_KEY='sk-...'
python app.py

# DeepSeek
export OPENAI_API_KEY='sk-...'
export OPENAI_BASE_URL='https://api.deepseek.com/v1'
export OPENAI_MODEL='deepseek-chat'
python app.py

# Groq
export OPENAI_API_KEY='gsk-...'
export OPENAI_BASE_URL='https://api.groq.com/openai/v1'
export OPENAI_MODEL='llama-3.3-70b-versatile'
python app.py
```

> 无 API Key 时会自动回退到本地色彩统计分析模式。

## 工作流程

```
用户运行 app.py
       │
       ▼
  扫描目录 → 发现 .cube (含 .zip 内)
       │
       ▼
  发现测试图 (PPM/PNG/JPEG...)
       │
       ▼
  逐个 LUT:
    ├─ C 工具应用 LUT → PPM
    └─ 添加水印 → PNG → 色彩统计提取
       │
       ▼
  AI 评估 (或本地回退)
       │
       ▼
  结果展示: 排名/评分/风格标签/最优推荐
```

## 评估方式

### AI 模式 (需 API Key)
- 提取每张水印图的 **色彩统计数据**（平均色、饱和度、直方图、主色调等）
- 将这些数据以文本形式发给 LLM，LLM 基于数据判断风格
- 无需图像视觉能力，**兼容所有 LLM 提供商**

### 本地模式 (无 API Key)
- 基于色彩统计数据做基础分析
- 计算饱和度变化、对比度变化、暖冷偏差等指标
- 给出粗略排序和标签

## 依赖 (运行时可缺失，部分功能降级)

| 依赖 | 用途 | 缺失时 |
|------|------|--------|
| `Pillow` | 图像转换/水印/统计 | C 工具 + 基础功能可用 |
| `requests` | API 调用 | 仅本地模式 |

## 技术细节

- **C 引擎**: ANSI C (C89)、BSD Allman 风格、四面体插值
- **色彩统计**: RGB/HSV 分析、8-bin 直方图、主色调量化 (64 色)、对比度计算
- **输出**: 水印 PNG 图保存至 `results/` 目录
