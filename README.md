# 批量视频水印工具

> **English?** Jump to [English Section](#english).

---

## 📖 简介

基于 **Python + FFmpeg** 的 Windows 桌面工具，支持批量为视频添加**图片水印**或**文字水印**，可在右下角、左上角等预设位置精确放置，半专业模式提供 CRF、编码预设等完整参数调节。

**FFmpeg 优先从 exe 同目录读取**，只需把 `ffmpeg.exe` 和本程序放同一文件夹，无需安装，无需联网。

---

## ✨ 功能特性

- 🖼️ **图片水印** — 支持 PNG/JPG/WebP 等格式，自动缩放
- 🔤 **文字水印** — 支持自定义字体、大小、颜色，可选阴影
- 📍 **6 种预设位置** — 左上 / 右上 / 左下 / 右下 / 居中 / 自定义坐标
- 🖱️ **拖拽定位** — 拖动画布上的水印即可快速设置位置
- 📦 **批量处理** — 一次选中多个视频，自动排队处理
- 🎚️ **小白 / 半专业双模式** — 小白模式简洁安全，半专业模式展开完整 FFmpeg 参数
- 🎵 **保留音频** — 默认保留原视频音频轨道
- 🔄 **实时日志** — 处理进度和 FFmpeg 输出实时显示
- ⛔ **随时停止** — 支持中途停止任务

---

## 🖥️ 快速开始

### 方式一：下载 exe 直接运行（推荐）

👉 **下载最新版本 → [Releases 页面](https://github.com/CommonerOfWestWall/batch-watermark-tool/releases)**

**使用方法（只需 3 步）：**

```
1. 下载并解压 zip
2. 把 ffmpeg.exe 放入解压后的文件夹（和 .exe 同目录）
3. 双击运行 批量水印处理工具.exe
```

> 💡 FFmpeg 下载地址：https://ffmpeg.org/download.html（Windows → builds → ffmpeg-master-latest-win64-gpl.zip）

> ⚠️ 首次运行如果杀毒软件报毒（PyInstaller 壳程序特征），请添加信任，这是正常现象。

---

### 方式二：从源码运行

#### 环境要求

- Python 3.10+
- FFmpeg（加入 PATH 或放在脚本同目录）

#### 安装依赖

```bash
git clone https://github.com/CommonerOfWestWall/batch-watermark-tool.git
cd batch-watermark-tool
pip install Pillow

# 运行
python 批量加水印_优化版.py
```

#### 打包为 exe

```bash
pip install pyinstaller
pyinstaller 批量水印处理工具.spec
```

打包产物在 `dist/` 目录中。

---

## 📸 使用说明

### 基础步骤

1. **选择视频** — 点"选择视频"或"选择文件夹"（支持子文件夹扫描）
2. **设置水印**
   - 图片水印：选一张图片，调节宽度
   - 文字水印：输入文字，选择字体、字号、颜色
3. **调整透明度** — 拖动透明度滑块
4. **设置位置** — 选预设位置，或直接在预览画布上拖动水印
5. **选择输出目录** — 默认输出到同目录 `watermarked` 子文件夹
6. **开始处理** — 点"开始批量处理"，观察日志和进度条

### 封面长图水印技巧

如需在全片头/片尾加水印，建议：
- 图片水印宽度设为 200~400 px
- 放在右下角，透明度 80%~90%
- 这样在全片展示时水印不会遮挡主要内容

---

## ⚙️ 半专业模式参数说明

| 参数 | 说明 | 推荐值 |
|------|------|--------|
| CRF | 视频质量（越小越清晰） | 18~23 |
| 编码预设 | 编码速度（slow 质量高但慢） | medium |
| 透明度 | 水印透明度 | 0.7~0.9 |
| 字号 | 文字水印大小 | 36~60 |
| 边距 | 水印距边缘距离 | 30~60 px |

---

## 🛠️ 技术栈

| 组件 | 技术 |
|------|------|
| GUI 框架 | Tkinter（Python 内置） |
| 视频处理 | FFmpeg（external） |
| 图片预览 | Pillow |
| 打包工具 | PyInstaller |
| 许可证 | MIT |

---

## 📄 许可证

本项目采用 **MIT 许可证**开源。详见 [LICENSE](LICENSE) 文件。

---

## 🌟 Star History

如果这个工具对你有帮助，欢迎点个 ⭐ Star！

---

<a name="english"></a>

---

## Batch Video Watermark Tool

### Overview

A lightweight **Python + FFmpeg** Windows desktop tool for batch adding image or text watermarks to videos. Drag the watermark on the preview canvas to set position precisely. FFmpeg is loaded from the same directory as the exe first, so just place them together and run — no installation or internet required.

### Features

- 🖼️ Image watermark with auto-scaling
- 🔤 Text watermark with font, size, color and shadow options
- 📍 6 preset positions + drag-to-position on preview canvas
- 📦 Batch process multiple videos at once
- 🎚️ Beginner / Pro mode toggle
- 🎵 Audio track preserved by default
- 🔄 Real-time FFmpeg log output
- ⛔ Stop mid-task anytime

### Quick Start (exe)

1. Download latest release from **[Releases page](https://github.com/CommonerOfWestWall/batch-watermark-tool/releases)**
2. Extract the zip
3. Place `ffmpeg.exe` in the same folder as the tool
4. Double-click the exe and run

> Download FFmpeg: https://ffmpeg.org/download.html → Windows builds

### Run from Source

```bash
git clone https://github.com/CommonerOfWestWall/batch-watermark-tool.git
cd batch-watermark-tool
pip install Pillow
python 批量加水印_优化版.py
```

### Build exe

```bash
pip install pyinstaller
pyinstaller 批量水印处理工具.spec
```

### License

MIT — see [LICENSE](LICENSE).
