import os
import re
import sys
import shlex
import queue
import copy
import shutil
import threading
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".webm", ".m4v")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def resource_font_path() -> str:
    """Return a reasonable default CJK-capable font path."""
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return ""


def safe_filename_suffix(text: str) -> str:
    text = text.strip() or "wm"
    return re.sub(r'[<>"/\\|?*\x00-\x1f]+', "_", text)


def ffmpeg_escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg drawtext filter."""
    return (
        text.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace("\n", r"\n")
    )


def ffmpeg_escape_path(path: str) -> str:
    """For drawtext fontfile. Windows drive colon must be escaped."""
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


@dataclass
class WatermarkConfig:
    """单个水印的完整配置。"""
    name: str = "水印 1"
    watermark_type: str = "image"           # "image" or "text"
    image_path: str = ""
    text: str = "示例水印"
    font_path: str = ""
    font_size: int = 42
    font_color: str = "#FFFFFF"
    shadow: bool = True
    img_width: int = 220
    opacity: float = 0.85
    position_preset: str = "右下角"
    margin_x: int = 36
    margin_y: int = 36
    custom_x: int = 500
    custom_y: int = 280
    pos_x_ratio: float = 0.78
    pos_y_ratio: float = 0.78
    animated: bool = False
    anim_movement: str = "none"             # none / horizontal / vertical / diagonal / circular
    anim_flicker: bool = False
    anim_speed: float = 1.0

    def clone(self) -> "WatermarkConfig":
        return copy.deepcopy(self)


@dataclass
class ErrorDiagnosis:
    category: str
    severity: str
    chinese_title: str
    chinese_reason: str
    chinese_solution: str
    original_lines: List[str]


class FFmpegErrorAnalyzer:
    """把 FFmpeg stderr 和退出码翻译成用户能看懂的中文。"""

    PATTERNS: List[Tuple[str, str, str, str, str, str]] = [
        (
            r"Unknown encoder\s+'([^']+)'|Encoder\s+(\w+)\s+not found|"
            r"Codec\s+(\w+)\s+not found|unknown encoder|Invalid argument.*codec|Unsupported codec",
            "codec", "error", "编码器不支持",
            "FFmpeg 不支持请求的编码器。可能您的 FFmpeg 版本较旧，或编译时未启用该编码器。",
            "1) 运行 `ffmpeg -encoders` 查看支持的编码器；\n"
            "2) 前往 https://ffmpeg.org/download.html 下载最新版 FFmpeg；\n"
            "3) 尝试更换编码器，如 h264_nvenc（NVIDIA）、h264_amf（AMD）或 libx264。"
        ),
        (
            r"No NVENC capable devices|Cannot load nvEncodeAPI|NVENC Error|"
            r"Failed to initialize.*encoder|h264_nvenc.*not supported|h265_nvenc.*not supported|"
            r"Cannot init CUDA|CUDA_ERROR|nvenc",
            "hwaccel", "error", "显卡硬件编码失败",
            "NVIDIA NVENC 硬件编码器无法初始化。可能是显卡不支持、驱动过旧，或 FFmpeg 未编译 NVENC 支持。",
            "1) 更新显卡驱动到最新版（建议从 NVIDIA 官网下载）；\n"
            "2) 改用软件编码：把编码器换成 libx264 或 libx265；\n"
            "3) 确认显卡支持 NVENC（较老的 GTX 或笔记本核显可能不支持）；\n"
            "4) 笔记本请检查是否启用了独立显卡。"
        ),
        (
            r"Could not load font|Fontconfig error|No such file or directory.*font|"
            r"Cannot load font|fontfile.*not found|failed to load font",
            "font", "error", "字体文件问题",
            "FFmpeg 无法加载指定的字体文件。中文水印需要正确的字体支持。",
            "1) 确认字体文件路径正确且文件存在；\n"
            "2) 推荐使用 Windows 自带字体：C:\\Windows\\Fonts\\msyh.ttc（微软雅黑）或 simhei.ttf（黑体）；\n"
            "3) 路径含空格时用引号包裹，或把字体复制到程序同目录使用相对路径。"
        ),
        (
            r"No such file or directory|Invalid data found when processing input|"
            r"Failed to open input|Error opening input",
            "file", "error", "输入文件不存在或损坏",
            "FFmpeg 无法打开输入文件。文件可能已被移动、删除，或路径含有特殊字符。",
            "1) 检查文件是否仍在原位置；\n"
            "2) 避免路径中出现 #、%、& 等特殊字符；\n"
            "3) 若路径含中文，确保 FFmpeg 使用 UTF-8；\n"
            "4) 尝试把文件复制到纯英文路径（如 D:\\test\\video.mp4）后再处理。"
        ),
        (
            r"Permission denied.*output|Permission denied.*write|Could not write header|Failed to open output",
            "permission", "error", "输出目录无写入权限",
            "FFmpeg 无法在输出目录写入文件。可能目录被其他程序占用，或没有写入权限。",
            "1) 检查输出目录是否被播放器、资源管理器等占用，关闭后重试；\n"
            "2) 更换输出目录，如桌面或文档文件夹；\n"
            "3) 以管理员身份运行本程序；\n"
            "4) 检查杀毒软件是否拦截了写入。"
        ),
        (
            r"Error configuring filter|Error initializing filter|Invalid filtergraph|Bad filtergraph|Syntax error in filtergraph|"
            r"No such filter|Failed to configure output|Filter.*has an unconnected output|"
            r"Invalid argument.*filter",
            "filter", "error", "滤镜参数错误",
            "FFmpeg 滤镜链（filter_complex）语法有误。可能是参数格式错误、滤镜名称拼写错误，或输入流连接有问题。",
            "1) 检查水印位置参数是否包含非法字符；\n"
            "2) 若启用了动画，确认表达式语法正确；\n"
            "3) 减少水印数量，先测试单个水印是否正常；\n"
            "4) 查看日志中的详细错误行，定位具体滤镜。"
        ),
        (
            r"Unsupported pixel format|pixel format.*not supported|Incompatible pixel format|"
            r"Conversion from .* not supported",
            "format", "error", "像素格式不支持",
            "视频的像素格式不被当前编码器支持。libx264 等编码器只支持特定像素格式。",
            "1) 确保输出选项中勾选了“质量优先”，这会自动添加 -pix_fmt yuv420p；\n"
            "2) 若源视频是 10-bit/HDR，可先转换为 8-bit 或更换编码器。"
        ),
        (
            r"No space left on device|Disk full|insufficient disk space|Write error",
            "disk", "error", "磁盘空间不足",
            "输出磁盘已满，FFmpeg 无法继续写入。",
            "1) 清理输出磁盘空间；\n"
            "2) 更换输出目录到有足够空间的磁盘；\n"
            "3) 提高 CRF 值（如 23~28）以减小文件体积。"
        ),
        (
            r"Cannot allocate memory|out of memory|Allocation failed|malloc failed",
            "memory", "error", "内存/显存不足",
            "FFmpeg 无法分配足够的内存或显存。4K/HDR/HEVC 视频处理或硬件编码时容易出现。",
            "1) 关闭其他占用内存/显存的程序；\n"
            "2) 降低输出分辨率或码率；\n"
            "3) 改用软件编码并降低并发处理数量；\n"
            "4) 增加物理内存或虚拟内存。"
        ),
        (
            r"hevc.*error|h\.265.*error|Invalid data found when processing input|corrupt input|"
            r"Failed to decode.*hevc|Failed to decode.*h265|Error parsing.*hevc",
            "codec", "error", "HEVC/H.265 解码失败",
            "FFmpeg 无法解码 HEVC/H.265 视频。文件可能损坏，或缺少 HEVC 解码器。",
            "1) 用 VLC 播放器打开视频确认未损坏；\n"
            "2) 更新 FFmpeg 到最新版；\n"
            "3) 安装 HEVC 视频扩展（Windows 商店）或换用支持 HEVC 的 FFmpeg 版本；\n"
            "4) 尝试先用 FFmpeg 转封装或重编码：ffmpeg -i input.mkv -c:v libx264 -crf 23 temp.mp4"
        ),
        (
            r"Unknown decoder|Decoder not found|Failed to decode|corrupt input",
            "codec", "error", "解码失败",
            "FFmpeg 无法解码输入视频。视频文件可能损坏，或使用了不常见的编码格式。",
            "1) 用 VLC 播放器打开视频确认未损坏；\n"
            "2) 更新 FFmpeg 到最新版；\n"
            "3) 尝试先用 FFmpeg 转码一次：ffmpeg -i input.mp4 -c:v libx264 -crf 23 temp.mp4"
        ),
    ]

    EXIT_CODES: dict[int, Tuple[str, str, str, str, str]] = {
        # 0xC0000005 STATUS_ACCESS_VIOLATION
        3221225781: (
            "crash", "error", "FFmpeg 进程崩溃",
            "FFmpeg 异常退出（内存访问冲突）。常见于 4K/HDR/HEVC 解码或 NVENC/AMF 硬件编码时驱动/内存不稳定，也可能是输入文件损坏导致解码器崩溃。",
            "1) 关闭硬件加速，改用软件编码 libx264 / libx265；\n"
            "2) 更新显卡驱动到最新版；\n"
            "3) 降低分辨率、码率，或关闭其他占用显存的程序；\n"
            "4) 尝试用 VLC 等播放器确认视频文件未损坏；\n"
            "5) 换一个 FFmpeg 版本重试。"
        ),
        # 0xC00000FD STATUS_STACK_OVERFLOW
        3221225725: (
            "crash", "error", "FFmpeg 堆栈溢出",
            "FFmpeg 出现堆栈溢出。可能是滤镜链过于复杂或输入文件异常。",
            "1) 减少水印数量，简化滤镜链；\n"
            "2) 降低视频分辨率或拆分处理；\n"
            "3) 检查输入文件是否损坏。"
        ),
        # 0xC0000022 STATUS_ACCESS_DENIED
        3221225506: (
            "permission", "error", "FFmpeg 被系统拒绝",
            "FFmpeg 进程被系统拒绝执行。可能是权限不足或杀毒软件拦截。",
            "1) 以管理员身份运行本程序；\n"
            "2) 把程序目录加入杀毒软件白名单；\n"
            "3) 检查 ffmpeg.exe 是否被其他程序锁定。"
        ),
        # 0xFFFFFFD8 = -40，常见于硬件编码器初始化失败
        4294967256: (
            "hwaccel", "error", "编码器初始化失败",
            "FFmpeg 返回 -40（功能未实现/初始化失败），通常是请求的硬件编码器（如 h264_nvenc）无法初始化。",
            "1) 确认显卡支持并启用 NVENC；\n"
            "2) 更新显卡驱动；\n"
            "3) 改用软件编码 libx264 / libx265；\n"
            "4) 笔记本请检查是否使用独立显卡。"
        ),
        # 0xFFFFFFFE = -2，常见于文件未找到或初始化失败
        4294967294: (
            "file", "error", "FFmpeg 初始化失败",
            "FFmpeg 进程初始化失败（返回 -2），通常是输入文件不存在、命令参数错误，或依赖 DLL 缺失。",
            "1) 检查输入文件是否存在；\n"
            "2) 确认 ffmpeg.exe 与所有依赖 DLL 完整；\n"
            "3) 尝试在命令行手动运行相同命令排查。"
        ),
    }

    @classmethod
    def analyze(cls, stderr_lines: List[str], exit_code: Optional[int] = None) -> List[ErrorDiagnosis]:
        diagnoses: List[ErrorDiagnosis] = []
        used_indices: set = set()

        # 先根据退出码判断崩溃类错误
        if exit_code is not None and exit_code != 0:
            if exit_code in cls.EXIT_CODES:
                category, severity, title, reason, solution = cls.EXIT_CODES[exit_code]
                diagnoses.append(ErrorDiagnosis(
                    category=category,
                    severity=severity,
                    chinese_title=title,
                    chinese_reason=reason,
                    chinese_solution=solution,
                    original_lines=[f"退出码：{exit_code}"]
                ))
            else:
                # 记录未知退出码
                diagnoses.append(ErrorDiagnosis(
                    category="exit_code",
                    severity="error",
                    chinese_title="FFmpeg 异常退出",
                    chinese_reason=f"FFmpeg 返回了非零退出码 {exit_code}。",
                    chinese_solution="1) 查看下方原始日志；\n"
                                     "2) 尝试简化参数或减少水印后重试；\n"
                                     "3) 将退出码和日志复制到搜索引擎查找解决方案。",
                    original_lines=[f"退出码：{exit_code}"]
                ))

        for pattern, category, severity, title, reason, solution in cls.PATTERNS:
            regex = re.compile(pattern, re.IGNORECASE)
            for i, line in enumerate(stderr_lines):
                if i in used_indices:
                    continue
                m = regex.search(line)
                if m:
                    diagnoses.append(ErrorDiagnosis(
                        category=category,
                        severity=severity,
                        chinese_title=title,
                        chinese_reason=reason,
                        chinese_solution=solution,
                        original_lines=[line]
                    ))
                    used_indices.add(i)
                    break

        if not diagnoses and stderr_lines:
            diagnoses.append(ErrorDiagnosis(
                category="unknown",
                severity="error",
                chinese_title="未知错误",
                chinese_reason="FFmpeg 返回了错误，但无法识别具体原因。",
                chinese_solution="1) 尝试减少水印数量或简化设置后重试；\n"
                                 "2) 确认 FFmpeg 版本较新（建议 5.0+）；\n"
                                 "3) 将完整日志复制到搜索引擎查找解决方案。",
                original_lines=stderr_lines[-6:]
            ))
        return diagnoses


class WatermarkEditor:
    """单个水印在界面上的编辑器变量集合。"""

    def __init__(self):
        self.watermark_type = tk.StringVar(value="image")
        self.image_path = tk.StringVar(value="")
        self.text = tk.StringVar(value="示例水印")
        self.font_path = tk.StringVar(value=resource_font_path())
        self.font_size = tk.IntVar(value=42)
        self.font_color = tk.StringVar(value="#FFFFFF")
        self.shadow = tk.BooleanVar(value=True)
        self.img_width = tk.IntVar(value=220)
        self.opacity = tk.DoubleVar(value=0.85)
        self.position_preset = tk.StringVar(value="右下角")
        self.margin_x = tk.IntVar(value=36)
        self.margin_y = tk.IntVar(value=36)
        self.custom_x = tk.IntVar(value=500)
        self.custom_y = tk.IntVar(value=280)
        self.animated = tk.BooleanVar(value=False)
        self.anim_movement = tk.StringVar(value="none")
        self.anim_flicker = tk.BooleanVar(value=False)
        self.anim_speed = tk.DoubleVar(value=1.0)

    def load(self, wm: WatermarkConfig):
        self.watermark_type.set(wm.watermark_type)
        self.image_path.set(wm.image_path)
        self.text.set(wm.text)
        self.font_path.set(wm.font_path or resource_font_path())
        self.font_size.set(wm.font_size)
        self.font_color.set(wm.font_color)
        self.shadow.set(wm.shadow)
        self.img_width.set(wm.img_width)
        self.opacity.set(wm.opacity)
        self.position_preset.set(wm.position_preset)
        self.margin_x.set(wm.margin_x)
        self.margin_y.set(wm.margin_y)
        self.custom_x.set(wm.custom_x)
        self.custom_y.set(wm.custom_y)
        self.animated.set(wm.animated)
        self.anim_movement.set(wm.anim_movement)
        self.anim_flicker.set(wm.anim_flicker)
        self.anim_speed.set(wm.anim_speed)

    def save(self, wm: WatermarkConfig):
        wm.watermark_type = self.watermark_type.get()
        wm.image_path = self.image_path.get()
        wm.text = self.text.get()
        wm.font_path = self.font_path.get()
        wm.font_size = int(self.font_size.get() or 42)
        wm.font_color = self.font_color.get()
        wm.shadow = bool(self.shadow.get())
        wm.img_width = int(self.img_width.get() or 220)
        wm.opacity = float(self.opacity_var_or_default())
        wm.position_preset = self.position_preset.get()
        wm.margin_x = int(self.margin_x.get() or 0)
        wm.margin_y = int(self.margin_y.get() or 0)
        wm.custom_x = int(self.custom_x.get() or 0)
        wm.custom_y = int(self.custom_y.get() or 0)
        wm.animated = bool(self.animated.get())
        wm.anim_movement = self.anim_movement.get() or "none"
        wm.anim_flicker = bool(self.anim_flicker.get())
        wm.anim_speed = float(self.anim_speed.get() or 1.0)

    def opacity_var_or_default(self) -> float:
        try:
            return max(0.05, min(1.0, float(self.opacity.get())))
        except Exception:
            return 0.85


class WatermarkApp:
    def __init__(self, master: tk.Tk):
        self.master = master
        self.master.title("批量水印处理工具 Pro")
        self.master.geometry("1240x820")
        self.master.minsize("1080", "720")

        self.video_files: list[str] = []
        self.output_dir = tk.StringVar(value="")
        self.running = False
        self.stop_requested = False
        self.log_queue: queue.Queue = queue.Queue()

        self.preview_w = 640
        self.preview_h = 360
        self.dragging = False
        self.preview_photo = None
        self.watermark_photo = None

        # 多水印数据
        self.watermarks: List[WatermarkConfig] = [WatermarkConfig()]
        self.current_watermark_index = 0
        self.editor = WatermarkEditor()
        self.watermark_buttons: List[ttk.Button] = []

        self._build_vars()
        self._build_ui()
        self._bind_traces()
        self._sync_editor_to_current()
        self._set_mode_visibility()
        self._update_preset_position()
        self._redraw_preview()
        self._poll_log_queue()

        if not shutil.which("ffmpeg"):
            self._log("⚠ 未检测到 ffmpeg。请先安装 FFmpeg，并把 ffmpeg 加入系统 PATH。")

    def _build_vars(self):
        self.mode_var = tk.StringVar(value="beginner")
        self.recursive_var = tk.BooleanVar(value=False)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.copy_audio_var = tk.BooleanVar(value=True)
        self.keep_quality_var = tk.BooleanVar(value=True)
        self.accel_var = tk.StringVar(value="CPU")
        self.suffix_var = tk.StringVar(value="_watermark")
        self.crf_var = tk.IntVar(value=20)
        self.preset_var = tk.StringVar(value="medium")

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Section.TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 11, "bold"), padding=(12, 7))
        style.configure("Danger.TButton", padding=(12, 7))
        style.configure("Selected.TButton", font=("Microsoft YaHei UI", 9, "bold"))

        root = ttk.Frame(self.master, padding=14)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=0, minsize=400)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="批量加水印", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="小白模式简洁安全，半专业模式可添加多水印与浮动效果", foreground="#666").grid(row=1, column=0, sticky="w")
        mode_box = ttk.Frame(header)
        mode_box.grid(row=0, column=1, rowspan=2, sticky="e")
        ttk.Radiobutton(mode_box, text="小白推荐", variable=self.mode_var, value="beginner", command=self._set_mode_visibility).pack(side="left", padx=4)
        ttk.Radiobutton(mode_box, text="半专业调节", variable=self.mode_var, value="pro", command=self._set_mode_visibility).pack(side="left", padx=4)

        self.left = ttk.Frame(root)
        self.left.grid(row=1, column=0, sticky="nswe", padx=(0, 12))
        self.left.columnconfigure(0, weight=1)

        self.right = ttk.Frame(root)
        self.right.grid(row=1, column=1, sticky="nsew")
        self.right.rowconfigure(0, weight=1)
        self.right.columnconfigure(0, weight=1)

        self._build_file_section()
        self._build_watermark_section()
        self._build_position_section()
        self._build_output_section()
        self._build_action_section()
        self._build_preview_log_section()

    def _build_file_section(self):
        box = ttk.LabelFrame(self.left, text="1. 输入视频", style="Section.TLabelframe")
        box.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        box.columnconfigure(0, weight=1)

        row = ttk.Frame(box, padding=(8, 8))
        row.grid(row=0, column=0, sticky="ew")
        ttk.Button(row, text="选择视频", command=self.select_videos).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="选择文件夹", command=self.select_folder).pack(side="left", padx=(0, 6))
        self.file_count_label = ttk.Label(row, text="未选择视频", foreground="#666")
        self.file_count_label.pack(side="left", padx=8)

        self.pro_file_options = ttk.Frame(box, padding=(8, 0, 8, 8))
        self.pro_file_options.grid(row=1, column=0, sticky="ew")
        ttk.Checkbutton(self.pro_file_options, text="扫描子文件夹", variable=self.recursive_var).pack(side="left", padx=(0, 12))

    def _build_watermark_section(self):
        box = ttk.LabelFrame(self.left, text="2. 水印内容", style="Section.TLabelframe")
        box.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        box.columnconfigure(0, weight=1)

        # 水印列表（仅半专业模式）
        self.watermark_list_frame = ttk.Frame(box, padding=(8, 8))
        self.watermark_list_frame.grid(row=0, column=0, sticky="ew")
        self.watermark_list_inner = ttk.Frame(self.watermark_list_frame)
        self.watermark_list_inner.pack(side="left", fill="x", expand=True)
        ttk.Button(self.watermark_list_frame, text="+ 增加", command=self._on_add_watermark).pack(side="left", padx=(6, 0))
        self.delete_wm_btn = ttk.Button(self.watermark_list_frame, text="删除当前", command=self._on_delete_watermark)
        self.delete_wm_btn.pack(side="left", padx=(6, 0))

        # 类型选择
        type_row = ttk.Frame(box, padding=(8, 0))
        type_row.grid(row=1, column=0, sticky="ew")
        ttk.Radiobutton(type_row, text="图片水印", variable=self.editor.watermark_type, value="image", command=self._set_type_visibility).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(type_row, text="文字水印", variable=self.editor.watermark_type, value="text", command=self._set_type_visibility).pack(side="left")

        # 图片设置
        self.image_frame = ttk.Frame(box, padding=(8, 0, 8, 8))
        self.image_frame.grid(row=2, column=0, sticky="ew")
        self.image_frame.columnconfigure(1, weight=1)
        ttk.Button(self.image_frame, text="选择水印图片", command=self.select_watermark_image).grid(row=0, column=0, sticky="w")
        ttk.Label(self.image_frame, textvariable=self.editor.image_path, foreground="#666", wraplength=280).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(self.image_frame, text="宽度").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.img_width_spin = ttk.Spinbox(self.image_frame, from_=20, to=2000, textvariable=self.editor.img_width, width=8)
        self.img_width_spin.grid(row=1, column=1, sticky="w", pady=(8, 0), padx=(8, 0))
        ttk.Label(self.image_frame, text="px").grid(row=1, column=1, sticky="w", padx=(70, 0), pady=(8, 0))

        # 文字设置
        self.text_frame = ttk.Frame(box, padding=(8, 0, 8, 8))
        self.text_frame.grid(row=3, column=0, sticky="ew")
        self.text_frame.columnconfigure(1, weight=1)
        ttk.Label(self.text_frame, text="文字").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.text_frame, textvariable=self.editor.text).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(self.text_frame, text="字号").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(self.text_frame, from_=8, to=300, textvariable=self.editor.font_size, width=8).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        self.pro_text_options = ttk.Frame(self.text_frame)
        self.pro_text_options.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(self.pro_text_options, text="字体文件", command=self.select_font).pack(side="left")
        ttk.Button(self.pro_text_options, text="颜色", command=self.pick_color).pack(side="left", padx=6)
        ttk.Checkbutton(self.pro_text_options, text="阴影", variable=self.editor.shadow).pack(side="left", padx=6)

        # 公共透明度
        common = ttk.Frame(box, padding=(8, 0, 8, 8))
        common.grid(row=4, column=0, sticky="ew")
        common.columnconfigure(1, weight=1)
        ttk.Label(common, text="透明度").grid(row=0, column=0, sticky="w")
        ttk.Scale(common, from_=0.05, to=1.0, variable=self.editor.opacity).grid(row=0, column=1, sticky="ew", padx=8)
        self.opacity_label = ttk.Label(common, width=5)
        self.opacity_label.grid(row=0, column=2, sticky="e")

        self._set_type_visibility()

    def _build_position_section(self):
        box = ttk.LabelFrame(self.left, text="3. 位置", style="Section.TLabelframe")
        box.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        box.columnconfigure(1, weight=1)
        presets = ["左上角", "右上角", "左下角", "右下角", "居中", "自定义"]
        top = ttk.Frame(box, padding=(8, 8))
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="预设").pack(side="left")
        ttk.Combobox(top, values=presets, textvariable=self.editor.position_preset, state="readonly", width=12).pack(side="left", padx=8)

        self.pro_position_options = ttk.Frame(box, padding=(8, 0, 8, 8))
        self.pro_position_options.grid(row=1, column=0, sticky="ew")
        for i, (label, var) in enumerate((
            ("横向边距", self.editor.margin_x),
            ("纵向边距", self.editor.margin_y),
            ("自定义 X", self.editor.custom_x),
            ("自定义 Y", self.editor.custom_y)
        )):
            ttk.Label(self.pro_position_options, text=label).grid(row=i // 2, column=(i % 2) * 2, sticky="w", pady=3)
            ttk.Spinbox(self.pro_position_options, from_=0, to=9999, textvariable=var, width=7).grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(5, 12), pady=3)

        # 动画选项（仅半专业模式）
        self.anim_frame = ttk.Frame(box, padding=(8, 0, 8, 8))
        self.anim_frame.grid(row=2, column=0, sticky="ew")
        ttk.Checkbutton(self.anim_frame, text="启用漂浮动画", variable=self.editor.animated).grid(row=0, column=0, sticky="w")
        ttk.Label(self.anim_frame, text="方向").grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Combobox(self.anim_frame, values=["水平往返", "垂直往返", "对角往返", "圆周"], textvariable=self.editor.anim_movement, state="readonly", width=10).grid(row=0, column=2, sticky="w", padx=8)
        ttk.Label(self.anim_frame, text="速度").grid(row=0, column=3, sticky="w")
        ttk.Spinbox(self.anim_frame, from_=0.2, to=3.0, increment=0.2, textvariable=self.editor.anim_speed, width=5).grid(row=0, column=4, sticky="w", padx=8)
        ttk.Checkbutton(self.anim_frame, text="透明度闪烁", variable=self.editor.anim_flicker).grid(row=1, column=0, sticky="w", pady=(6, 0))

    def _build_output_section(self):
        box = ttk.LabelFrame(self.left, text="4. 输出", style="Section.TLabelframe")
        box.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        box.columnconfigure(1, weight=1)
        ttk.Button(box, text="选择输出目录", command=self.select_output_dir).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        ttk.Label(box, textvariable=self.output_dir, foreground="#666", wraplength=280).grid(row=0, column=1, padx=8, pady=8, sticky="ew")

        self.pro_output_options = ttk.Frame(box, padding=(8, 0, 8, 8))
        self.pro_output_options.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Label(self.pro_output_options, text="文件后缀").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.pro_output_options, textvariable=self.suffix_var, width=14).grid(row=0, column=1, sticky="w", padx=(6, 12))
        ttk.Checkbutton(self.pro_output_options, text="覆盖同名文件", variable=self.overwrite_var).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(self.pro_output_options, text="复制音频", variable=self.copy_audio_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(self.pro_output_options, text="质量优先", variable=self.keep_quality_var).grid(row=1, column=2, sticky="w", pady=(6, 0))
        ttk.Label(self.pro_output_options, text="CRF").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(self.pro_output_options, from_=16, to=32, textvariable=self.crf_var, width=6).grid(row=2, column=1, sticky="w", padx=(6, 12), pady=(6, 0))
        ttk.Label(self.pro_output_options, text="编码预设").grid(row=2, column=2, sticky="w", pady=(6, 0))
        ttk.Combobox(self.pro_output_options, values=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"], textvariable=self.preset_var, state="readonly", width=10).grid(row=2, column=3, sticky="w", padx=(6, 0), pady=(6, 0))
        ttk.Label(self.pro_output_options, text="硬件加速").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(
            self.pro_output_options,
            values=["CPU", "自动选择", "NVIDIA GPU", "AMD GPU", "Intel GPU"],
            textvariable=self.accel_var,
            state="readonly",
            width=12,
        ).grid(row=3, column=1, columnspan=3, sticky="w", padx=(6, 0), pady=(6, 0))

    def _build_action_section(self):
        box = ttk.Frame(self.left)
        box.grid(row=4, column=0, sticky="ew", pady=(2, 0))
        box.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(box, mode="determinate")
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.start_btn = ttk.Button(box, text="开始批量处理", style="Primary.TButton", command=self.process_videos)
        self.start_btn.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        self.stop_btn = ttk.Button(box, text="停止", style="Danger.TButton", command=self.stop_processing, state="disabled")
        self.stop_btn.grid(row=1, column=1, sticky="ew")

    def _build_preview_log_section(self):
        notebook = ttk.Notebook(self.right)
        notebook.grid(row=0, column=0, sticky="nsew")

        preview_tab = ttk.Frame(notebook, padding=10)
        preview_tab.columnconfigure(0, weight=1)
        preview_tab.rowconfigure(1, weight=1)
        notebook.add(preview_tab, text="预览")
        ttk.Label(preview_tab, text="拖动画布上高亮边框的水印可快速设置位置。半专业模式下可叠加多个水印。", foreground="#666").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.canvas = tk.Canvas(preview_tab, width=self.preview_w, height=self.preview_h, bg="#1f2937", highlightthickness=1, highlightbackground="#999")
        self.canvas.grid(row=1, column=0, sticky="n")
        self.canvas.bind("<Button-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag_watermark)
        self.canvas.bind("<ButtonRelease-1>", self._end_drag)

        log_tab = ttk.Frame(notebook, padding=10)
        log_tab.rowconfigure(0, weight=1)
        log_tab.columnconfigure(0, weight=1)
        notebook.add(log_tab, text="日志")
        self.log_text = tk.Text(log_tab, height=12, wrap="word", relief="flat", bg="#111827", fg="#e5e7eb", insertbackground="#e5e7eb")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        ybar = ttk.Scrollbar(log_tab, orient="vertical", command=self.log_text.yview)
        ybar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=ybar.set)

    def _bind_traces(self):
        # 编辑器变量变化时，保存回当前配置并重绘
        editor_vars = [
            self.editor.watermark_type, self.editor.image_path, self.editor.text,
            self.editor.font_size, self.editor.font_color, self.editor.shadow,
            self.editor.img_width, self.editor.opacity, self.editor.position_preset,
            self.editor.margin_x, self.editor.margin_y, self.editor.custom_x,
            self.editor.custom_y, self.editor.animated, self.editor.anim_movement,
            self.editor.anim_flicker, self.editor.anim_speed,
        ]
        for var in editor_vars:
            var.trace_add("write", lambda *_: self._on_editor_changed())

    def _on_editor_changed(self):
        """编辑器任何字段变化时同步回数据模型。"""
        if 0 <= self.current_watermark_index < len(self.watermarks):
            # 勾选动画但未选方向时，默认水平往返
            if self.editor.animated.get() and (not self.editor.anim_movement.get() or self.editor.anim_movement.get() == "none"):
                self.editor.anim_movement.set("水平往返")
            self.editor.save(self.watermarks[self.current_watermark_index])
            self._update_preset_position()
            self._redraw_preview()
            self._refresh_watermark_list()

    def _sync_editor_to_current(self):
        """把当前水印配置加载到编辑器。"""
        if 0 <= self.current_watermark_index < len(self.watermarks):
            self.editor.load(self.watermarks[self.current_watermark_index])
            self._update_preset_position()

    def _refresh_watermark_list(self):
        """刷新顶部水印列表按钮。"""
        for btn in self.watermark_buttons:
            btn.destroy()
        self.watermark_buttons.clear()
        for i, wm in enumerate(self.watermarks):
            short_type = "图" if wm.watermark_type == "image" else "文"
            name = f"{wm.name} [{short_type}]"
            style = "Selected.TButton" if i == self.current_watermark_index else "TButton"
            btn = ttk.Button(self.watermark_list_inner, text=name, style=style,
                             command=lambda idx=i: self._on_select_watermark(idx))
            btn.pack(side="left", padx=(0, 4))
            self.watermark_buttons.append(btn)
        # 删除按钮状态
        self.delete_wm_btn.config(state="normal" if len(self.watermarks) > 1 else "disabled")

    def _on_select_watermark(self, index: int):
        self.current_watermark_index = index
        self._sync_editor_to_current()
        self._set_type_visibility()
        self._refresh_watermark_list()
        self._redraw_preview()

    def _on_add_watermark(self):
        current = self.watermarks[self.current_watermark_index]
        new_wm = current.clone()
        new_wm.name = f"水印 {len(self.watermarks) + 1}"
        # 稍微错位，方便在预览里区分
        new_wm.pos_x_ratio = min(0.95, new_wm.pos_x_ratio + 0.02)
        new_wm.pos_y_ratio = min(0.95, new_wm.pos_y_ratio + 0.02)
        self.watermarks.append(new_wm)
        self._on_select_watermark(len(self.watermarks) - 1)

    def _on_delete_watermark(self):
        if len(self.watermarks) <= 1:
            return
        del self.watermarks[self.current_watermark_index]
        self.current_watermark_index = min(self.current_watermark_index, len(self.watermarks) - 1)
        self._sync_editor_to_current()
        self._set_type_visibility()
        self._refresh_watermark_list()
        self._redraw_preview()

    def _set_mode_visibility(self):
        is_pro = self.mode_var.get() == "pro"
        widgets = [self.pro_file_options, self.pro_position_options, self.pro_output_options, self.pro_text_options]
        for w in widgets:
            if is_pro:
                w.grid()
            else:
                w.grid_remove()
        # 小白模式只保留单水印体验
        if is_pro:
            self.watermark_list_frame.grid()
            self.anim_frame.grid()
        else:
            self.watermark_list_frame.grid_remove()
            self.anim_frame.grid_remove()
            # 小白模式强制只剩一个水印
            if len(self.watermarks) > 1:
                self.watermarks = [self.watermarks[self.current_watermark_index]]
                self.current_watermark_index = 0
                self._sync_editor_to_current()
        self._refresh_watermark_list()
        self._set_type_visibility()
        self._redraw_preview()
        self._log("当前模式：" + ("半专业调节" if is_pro else "小白推荐"))

    def _set_type_visibility(self):
        if self.editor.watermark_type.get() == "image":
            self.image_frame.grid()
            self.text_frame.grid_remove()
        else:
            self.image_frame.grid_remove()
            self.text_frame.grid()
        self._redraw_preview()

    def select_videos(self):
        files = filedialog.askopenfilenames(title="选择视频", filetypes=[("视频文件", "*.mp4 *.mov *.mkv *.avi *.flv *.wmv *.webm *.m4v"), ("所有文件", "*.*")])
        if files:
            self.video_files = list(files)
            self._refresh_file_label()
            if not self.output_dir.get():
                self.output_dir.set(str(Path(files[0]).parent / "watermarked"))

    def select_folder(self):
        folder = filedialog.askdirectory(title="选择视频文件夹")
        if not folder:
            return
        paths = []
        if self.recursive_var.get():
            for root, _, names in os.walk(folder):
                for name in names:
                    if name.lower().endswith(VIDEO_EXTS):
                        paths.append(os.path.join(root, name))
        else:
            for name in os.listdir(folder):
                p = os.path.join(folder, name)
                if os.path.isfile(p) and name.lower().endswith(VIDEO_EXTS):
                    paths.append(p)
        self.video_files = sorted(paths)
        self._refresh_file_label()
        if not self.output_dir.get():
            self.output_dir.set(str(Path(folder) / "watermarked"))

    def select_output_dir(self):
        folder = filedialog.askdirectory(title="选择输出目录")
        if folder:
            self.output_dir.set(folder)

    def select_watermark_image(self):
        path = filedialog.askopenfilename(title="选择水印图片", filetypes=[("图片文件", "*.png *.jpg *.jpeg *.webp *.bmp"), ("所有文件", "*.*")])
        if path:
            self.editor.image_path.set(path)

    def select_font(self):
        path = filedialog.askopenfilename(title="选择字体文件", filetypes=[("字体文件", "*.ttf *.ttc *.otf"), ("所有文件", "*.*")])
        if path:
            self.editor.font_path.set(path)

    def pick_color(self):
        _, color = colorchooser.askcolor(color=self.editor.font_color.get(), title="选择文字颜色")
        if color:
            self.editor.font_color.set(color)

    def _refresh_file_label(self):
        self.file_count_label.config(text=f"已选择 {len(self.video_files)} 个视频")
        self._log(f"已载入 {len(self.video_files)} 个视频。")

    def _estimate_watermark_size(self, wm: WatermarkConfig):
        if wm.watermark_type == "image":
            w = max(20, min(self.preview_w, int(wm.img_width or 220)))
            h = int(w * 0.45)
            if Image and wm.image_path and os.path.exists(wm.image_path):
                try:
                    with Image.open(wm.image_path) as img:
                        ratio = img.height / max(img.width, 1)
                    h = int(w * ratio)
                except Exception:
                    pass
            return w, max(20, min(self.preview_h, h))
        text = wm.text or "文字水印"
        fs = max(8, int(wm.font_size or 42))
        return min(self.preview_w - 20, max(80, int(len(text) * fs * 0.72))), max(28, int(fs * 1.25))

    def _update_preset_position(self):
        try:
            wm = self.watermarks[self.current_watermark_index]
            wm_w, wm_h = self._estimate_watermark_size(wm)
            mx = max(0, int(self.editor.margin_x.get()))
            my = max(0, int(self.editor.margin_y.get()))
            preset = self.editor.position_preset.get()
            if preset == "左上角":
                x, y = mx, my
            elif preset == "右上角":
                x, y = self.preview_w - wm_w - mx, my
            elif preset == "左下角":
                x, y = mx, self.preview_h - wm_h - my
            elif preset == "居中":
                x, y = (self.preview_w - wm_w) // 2, (self.preview_h - wm_h) // 2
            elif preset == "自定义":
                x, y = int(self.editor.custom_x.get()), int(self.editor.custom_y.get())
            else:
                x, y = self.preview_w - wm_w - mx, self.preview_h - wm_h - my
            x = max(0, min(self.preview_w - wm_w, x))
            y = max(0, min(self.preview_h - wm_h, y))
            wm.pos_x_ratio = x / self.preview_w
            wm.pos_y_ratio = y / self.preview_h
        except Exception:
            pass

    def _start_drag(self, event):
        # 判断是否点中了某个水印，选中它
        clicked = self.canvas.find_withtag("current")
        for i, wm in enumerate(self.watermarks):
            if self.canvas.find_withtag(f"wm_{i}") and clicked and clicked[0] in self.canvas.find_withtag(f"wm_{i}"):
                if self.current_watermark_index != i:
                    self._on_select_watermark(i)
                    return
                break
        self.dragging = True
        self._drag_watermark(event)

    def _drag_watermark(self, event):
        if not self.dragging:
            return
        try:
            wm = self.watermarks[self.current_watermark_index]
        except IndexError:
            return
        wm_w, wm_h = self._estimate_watermark_size(wm)
        x = max(0, min(self.preview_w - wm_w, event.x - wm_w // 2))
        y = max(0, min(self.preview_h - wm_h, event.y - wm_h // 2))
        wm.pos_x_ratio = x / self.preview_w
        wm.pos_y_ratio = y / self.preview_h
        if self.editor.position_preset.get() != "自定义":
            self.editor.position_preset.set("自定义")
        self.editor.custom_x.set(int(x))
        self.editor.custom_y.set(int(y))
        self._redraw_preview()

    def _end_drag(self, event):
        self.dragging = False

    def _redraw_preview(self):
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        self._preview_photos = []
        self.canvas.create_rectangle(0, 0, self.preview_w, self.preview_h, fill="#1f2937", outline="")
        self.canvas.create_text(self.preview_w // 2, self.preview_h // 2 - 15, text="视频预览区", fill="#d1d5db", font=("Microsoft YaHei UI", 26, "bold"))
        self.canvas.create_text(self.preview_w // 2, self.preview_h // 2 + 22, text="拖动高亮水印改变位置", fill="#9ca3af", font=("Microsoft YaHei UI", 12))
        try:
            self.opacity_label.config(text=f"{int(float(self.editor.opacity.get()) * 100)}%")
        except Exception:
            pass

        for i, wm in enumerate(self.watermarks):
            is_selected = (i == self.current_watermark_index)
            self._draw_watermark_on_canvas(wm, i, is_selected)

    def _draw_watermark_on_canvas(self, wm: WatermarkConfig, index: int, is_selected: bool):
        wm_w, wm_h = self._estimate_watermark_size(wm)
        x = int(wm.pos_x_ratio * self.preview_w)
        y = int(wm.pos_y_ratio * self.preview_h)
        x = max(0, min(self.preview_w - wm_w, x))
        y = max(0, min(self.preview_h - wm_h, y))
        tag = f"wm_{index}"

        if wm.watermark_type == "image":
            path = wm.image_path
            if Image and ImageTk and path and os.path.exists(path):
                try:
                    img = Image.open(path).convert("RGBA")
                    ratio = wm_w / max(img.width, 1)
                    img = img.resize((wm_w, max(1, int(img.height * ratio))))
                    alpha = img.getchannel("A").point(lambda p: int(p * wm.opacity))
                    img.putalpha(alpha)
                    photo = ImageTk.PhotoImage(img)
                    self._preview_photos.append(photo)
                    self.canvas.create_image(x, y, image=photo, anchor="nw", tags=tag)
                except Exception:
                    self.canvas.create_rectangle(x, y, x + wm_w, y + wm_h, fill="#2563eb", stipple="gray50", outline="", tags=tag)
                    self.canvas.create_text(x + wm_w // 2, y + wm_h // 2, text="图片水印", fill="white", tags=tag)
            else:
                self.canvas.create_rectangle(x, y, x + wm_w, y + wm_h, fill="#2563eb", stipple="gray50", outline="", tags=tag)
                self.canvas.create_text(x + wm_w // 2, y + wm_h // 2, text="图片水印", fill="white", tags=tag)
        else:
            color = wm.font_color or "#FFFFFF"
            fs = max(8, min(90, int(wm.font_size or 42)))
            text = wm.text or "文字水印"
            if wm.shadow:
                self.canvas.create_text(x + 2, y + 2, text=text, fill="#000000", font=("Microsoft YaHei UI", fs, "bold"), anchor="nw", tags=tag)
            self.canvas.create_text(x, y, text=text, fill=color, font=("Microsoft YaHei UI", fs, "bold"), anchor="nw", tags=tag)

        # 选择框
        outline = "#60a5fa" if is_selected else "#6b7280"
        dash = () if is_selected else (4, 3)
        width = 2 if is_selected else 1
        self.canvas.create_rectangle(x, y, x + wm_w, y + wm_h, outline=outline, dash=dash, width=width, tags=(tag, "border"))

        # 动画标记
        if wm.animated or wm.anim_flicker:
            self.canvas.create_text(x + wm_w - 4, y + 4, text="anim", fill="#fbbf24", font=("Microsoft YaHei UI", 8), anchor="ne", tags=tag)

    def validate(self) -> bool:
        if not self.video_files:
            messagebox.showerror("缺少视频", "请先选择至少一个视频文件。")
            return False
        if not self.output_dir.get():
            messagebox.showerror("缺少输出目录", "请选择输出目录。")
            return False
        if not shutil.which("ffmpeg"):
            messagebox.showerror("未找到 FFmpeg", "系统没有检测到 ffmpeg。请安装 FFmpeg 并加入 PATH 后再运行。")
            return False

        valid_count = 0
        for i, wm in enumerate(self.watermarks):
            prefix = f"[{wm.name}] "
            if wm.watermark_type == "image":
                if not wm.image_path or not os.path.exists(wm.image_path):
                    messagebox.showerror("水印配置错误", f"{prefix}请选择一个有效的水印图片。")
                    self._on_select_watermark(i)
                    return False
            else:
                if not wm.text.strip():
                    messagebox.showerror("水印配置错误", f"{prefix}请输入文字水印内容。")
                    self._on_select_watermark(i)
                    return False
                if not wm.font_path or not os.path.exists(wm.font_path):
                    # 不阻止，只提醒
                    pass
            valid_count += 1

        if valid_count == 0:
            messagebox.showerror("无有效水印", "请至少配置一个有效的水印。")
            return False

        # 字体统一提醒
        has_text = any(wm.watermark_type == "text" for wm in self.watermarks)
        default_font = resource_font_path()
        if has_text:
            for wm in self.watermarks:
                if wm.watermark_type == "text" and (not wm.font_path or not os.path.exists(wm.font_path)):
                    if default_font:
                        messagebox.showwarning("字体提醒", "某个文字水印未指定有效字体，FFmpeg 可能无法渲染中文。\n推荐字体：C:\\Windows\\Fonts\\msyh.ttc")
                    break
        return True

    def _get_video_size(self, video: str) -> Optional[Tuple[int, int]]:
        """获取视频分辨率，用于把预览像素换算成实际像素。"""
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            cmd = [ffprobe, "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", video]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=30)
                if result.returncode == 0:
                    parts = result.stdout.strip().split("x")
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        return int(parts[0]), int(parts[1])
            except Exception:
                pass
        # fallback: 解析 ffmpeg -i 输出
        try:
            result = subprocess.run(["ffmpeg", "-hide_banner", "-i", video],
                                    capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=30)
            output = result.stderr + result.stdout
            m = re.search(r"Stream #\d+:\d+.*?Video:.*?(\d{2,})x(\d{2,})", output)
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
        return None

    def _build_position_expressions(self, wm: WatermarkConfig) -> Tuple[str, str]:
        """生成 static/animated 的 x/y 表达式。"""
        if not wm.animated:
            x_expr = f"round(main_w*{wm.pos_x_ratio:.6f})"
            y_expr = f"round(main_h*{wm.pos_y_ratio:.6f})"
            return x_expr, y_expr

        speed = max(0.2, min(3.0, wm.anim_speed))
        base_x = wm.pos_x_ratio
        base_y = wm.pos_y_ratio
        mx = max(0, wm.margin_x)
        my = max(0, wm.margin_y)

        # 水印尺寸变量
        if wm.watermark_type == "image":
            wm_w_expr, wm_h_expr = "overlay_w", "overlay_h"
        else:
            wm_w_expr, wm_h_expr = "text_w", "text_h"

        movement = wm.anim_movement
        if movement == "水平往返":
            x_expr = f"round(min(main_w-{wm_w_expr},max(0,{mx}+(main_w-{mx}*2-{wm_w_expr})*(0.5+0.5*sin(2*PI*{speed}*t/10)))))"
            y_expr = f"round(main_h*{base_y:.6f})"
        elif movement == "垂直往返":
            x_expr = f"round(main_w*{base_x:.6f})"
            y_expr = f"round(min(main_h-{wm_h_expr},max(0,{my}+(main_h-{my}*2-{wm_h_expr})*(0.5+0.5*sin(2*PI*{speed}*t/10)))))"
        elif movement == "对角往返":
            x_expr = f"round(min(main_w-{wm_w_expr},max(0,{mx}+(main_w-{mx}*2-{wm_w_expr})*(0.5+0.5*sin(2*PI*{speed}*t/10)))))"
            y_expr = f"round(min(main_h-{wm_h_expr},max(0,{my}+(main_h-{my}*2-{wm_h_expr})*(0.5+0.5*sin(2*PI*{speed}*t/10+PI/4)))))"
        elif movement == "圆周":
            x_expr = f"round(min(main_w-{wm_w_expr},max(0,main_w*{base_x:.6f}+main_w*0.15*sin(2*PI*{speed}*t/10))))"
            y_expr = f"round(min(main_h-{wm_h_expr},max(0,main_h*{base_y:.6f}+main_h*0.15*cos(2*PI*{speed}*t/10))))"
        else:
            x_expr = f"round(main_w*{base_x:.6f})"
            y_expr = f"round(main_h*{base_y:.6f})"
        return x_expr, y_expr

    def _alpha_expression(self, wm: WatermarkConfig) -> str:
        """生成文字水印的透明度或透明度闪烁表达式（drawtext 的 alpha 选项）。"""
        opacity = max(0.05, min(1.0, wm.opacity))
        if not wm.anim_flicker:
            return f"{opacity:.3f}"
        speed = max(0.2, min(3.0, wm.anim_speed))
        # 在基础透明度附近波动，保证不会完全消失
        min_alpha = max(0.1, opacity - 0.25)
        max_alpha = min(1.0, opacity + 0.25)
        amplitude = max_alpha - min_alpha
        return f"'{min_alpha:.3f}+{amplitude:.3f}*abs(sin(2*PI*{speed}*t/3))'"

    def generate_ffmpeg_cmd(self, input_file: str, output_file: str) -> list[str]:
        cmd = ["ffmpeg", "-hide_banner", "-y" if self.overwrite_var.get() else "-n", "-i", input_file]

        # 获取视频实际分辨率，把预览像素换算过去
        video_w, video_h = self._get_video_size(input_file) or (self.preview_w, self.preview_h)

        # 收集图片水印输入，并记录其对应的水印索引
        image_input_map: dict[int, int] = {}
        for i, wm in enumerate(self.watermarks):
            if wm.watermark_type == "image" and wm.image_path and os.path.exists(wm.image_path):
                cmd += ["-i", wm.image_path]
                image_input_map[i] = len(image_input_map) + 1

        filter_parts = []
        current_stream = "[0:v]"

        for i, wm in enumerate(self.watermarks):
            x_expr, y_expr = self._build_position_expressions(wm)
            x_expr = f"'{x_expr}'" if "sin" in x_expr or "cos" in x_expr else x_expr
            y_expr = f"'{y_expr}'" if "sin" in y_expr or "cos" in y_expr else y_expr

            if wm.watermark_type == "image":
                input_idx = image_input_map.get(i)
                if input_idx is None:
                    continue
                width_px = max(20, int(video_w * wm.img_width / self.preview_w))

                if wm.anim_flicker:
                    # 图片闪烁用 geq（使用大写 T 表示时间）
                    speed = max(0.2, min(3.0, wm.anim_speed))
                    min_a = max(0.1, wm.opacity - 0.25)
                    max_a = min(1.0, wm.opacity + 0.25)
                    amp = max_a - min_a
                    alpha_expr = f"'alpha(X,Y)*({min_a:.3f}+{amp:.3f}*abs(sin(2*PI*{speed}*T/3)))'"
                    filter_parts.append(
                        f"[{input_idx}:v]scale={width_px}:-1:flags=lanczos,"
                        f"format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a={alpha_expr}[wm{i}]"
                    )
                else:
                    opacity = max(0.05, min(1.0, wm.opacity))
                    filter_parts.append(
                        f"[{input_idx}:v]scale={width_px}:-1:flags=lanczos,"
                        f"colorchannelmixer=aa={opacity:.3f}[wm{i}]"
                    )
                filter_parts.append(
                    f"{current_stream}[wm{i}]overlay=x={x_expr}:y={y_expr}:format=auto[tmp{i}]"
                )
                current_stream = f"[tmp{i}]"
            else:
                font = ffmpeg_escape_path(wm.font_path) if wm.font_path else ""
                text = ffmpeg_escape_drawtext(wm.text)
                font_size = max(8, int(wm.font_size))
                color = wm.font_color.lstrip("#") or "FFFFFF"
                alpha_expr = self._alpha_expression(wm)
                shadow = ":shadowcolor=black@0.55:shadowx=2:shadowy=2" if wm.shadow else ""
                font_part = f":fontfile='{font}'" if font else ""
                filter_parts.append(
                    f"{current_stream}drawtext=text='{text}'{font_part}:fontsize={font_size}:"
                    f"fontcolor=0x{color}:alpha={alpha_expr}:x={x_expr}:y={y_expr}{shadow}[tmp{i}]"
                )
                current_stream = f"[tmp{i}]"

        filter_parts.append(f"{current_stream}format=pix_fmts=yuv420p[outv]")
        cmd += ["-filter_complex", ";".join(filter_parts), "-map", "[outv]"]

        if self.copy_audio_var.get():
            cmd += ["-map", "0:a?", "-c:a", "copy"]
        else:
            cmd += ["-an"]

        encoder = self._select_video_encoder()
        cmd += ["-c:v", encoder]
        cmd += self._video_quality_args(encoder)
        cmd += ["-pix_fmt", "yuv420p"]
        cmd += [output_file]
        return cmd

    def _select_video_encoder(self) -> str:
        accel = self.accel_var.get()
        encoder_map = {
            "CPU": "libx264",
            "NVIDIA GPU": "h264_nvenc",
            "AMD GPU": "h264_amf",
            "Intel GPU": "h264_qsv",
        }
        if accel in encoder_map:
            encoder = encoder_map[accel]
            if encoder in self._get_available_encoders():
                return encoder
            self._log(f"未检测到 {encoder}，已回退 CPU 编码 libx264。")
            return "libx264"

        available = self._get_available_encoders()
        for encoder in ("h264_nvenc", "h264_qsv", "h264_amf", "libx264"):
            if encoder in available:
                return encoder
        return "libx264"

    def _get_available_encoders(self) -> set[str]:
        if hasattr(self, "_available_encoders_cache"):
            return self._available_encoders_cache
        if not shutil.which("ffmpeg"):
            self._available_encoders_cache = {"libx264"}
            return self._available_encoders_cache
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=12,
            )
            encoders = set(re.findall(r"\s([a-zA-Z0-9_]+)\s+", result.stdout))
            for name in ("h264_nvenc", "h264_qsv", "h264_amf", "libx264"):
                if name in result.stdout:
                    encoders.add(name)
            self._available_encoders_cache = encoders or {"libx264"}
        except Exception:
            self._available_encoders_cache = {"libx264"}
        return self._available_encoders_cache

    def _video_quality_args(self, encoder: str) -> list[str]:
        crf = str(int(self.crf_var.get()))
        if encoder == "libx264":
            if self.keep_quality_var.get():
                return ["-crf", crf, "-preset", self.preset_var.get()]
            return ["-preset", "veryfast"]
        if encoder.endswith("_nvenc"):
            preset = "p7" if self.keep_quality_var.get() else "p3"
            return ["-rc", "vbr", "-cq", crf, "-b:v", "0", "-preset", preset]
        if encoder.endswith("_qsv"):
            preset = "slower" if self.keep_quality_var.get() else "veryfast"
            return ["-global_quality", crf, "-preset", preset]
        if encoder.endswith("_amf"):
            quality = "quality" if self.keep_quality_var.get() else "speed"
            return ["-quality", quality, "-qp_i", crf, "-qp_p", crf, "-qp_b", crf]
        return []

    def _make_output_path(self, video: str) -> str:
        out_dir = Path(self.output_dir.get())
        suffix = safe_filename_suffix(self.suffix_var.get())
        src = Path(video)
        return str(out_dir / f"{src.stem}{suffix}{src.suffix}")

    def process_videos(self):
        if self.running:
            return
        if not self.validate():
            return
        Path(self.output_dir.get()).mkdir(parents=True, exist_ok=True)
        self.running = True
        self.stop_requested = False
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress.config(value=0, maximum=len(self.video_files))
        threading.Thread(target=self._worker_process, daemon=True).start()

    def stop_processing(self):
        self.stop_requested = True
        self._log("正在请求停止，当前视频处理结束后会停止。")

    def _worker_process(self):
        success = 0
        failed = 0
        for idx, video in enumerate(self.video_files, start=1):
            if self.stop_requested:
                break
            output_path = self._make_output_path(video)
            cmd = self.generate_ffmpeg_cmd(video, output_path)
            self._log(f"[{idx}/{len(self.video_files)}] 开始：{os.path.basename(video)}")
            self._log("命令：" + " ".join(shlex.quote(x) for x in cmd))
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
                all_lines = []
                for line in p.stdout or []:
                    if self.stop_requested and p.poll() is None:
                        p.terminate()
                    line = line.strip()
                    if line:
                        all_lines.append(line)
                        low = line.lower()
                        if "time=" in line or "error" in low or "invalid" in low or "failed" in low or "could not" in low:
                            self._log(line)
                code = p.wait()
                if code == 0:
                    success += 1
                    self._log(f"完成：{output_path}")
                else:
                    failed += 1
                    diagnoses = FFmpegErrorAnalyzer.analyze(all_lines, exit_code=code)
                    self._log(f"失败：{os.path.basename(video)}")
                    for d in diagnoses:
                        self._log(f"  [错误分析] {d.chinese_title}")
                        self._log(f"    原因：{d.chinese_reason}")
                        for sol_line in d.chinese_solution.split("\n"):
                            self._log(f"    {sol_line}")
                    if not diagnoses or all(d.category == "unknown" or d.category == "exit_code" for d in diagnoses):
                        self._log("  原始日志最后几行：")
                        for line in all_lines[-6:]:
                            self._log(f"    {line}")
            except Exception as e:
                failed += 1
                self._log(f"异常：{video} -> {e}")
            self.log_queue.put(("PROGRESS", idx))
        self.log_queue.put(("DONE", success, failed, self.stop_requested))

    def _log(self, msg: str):
        self.log_queue.put(str(msg))

    def _poll_log_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "PROGRESS":
                    self.progress.config(value=item[1])
                elif isinstance(item, tuple) and item and item[0] == "DONE":
                    _, success, failed, stopped = item
                    self.running = False
                    self.start_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    title = "已停止" if stopped else "处理完成"
                    messagebox.showinfo(title, f"成功 {success} 个，失败 {failed} 个。")
                else:
                    self.log_text.insert("end", str(item) + "\n")
                    self.log_text.see("end")
        except queue.Empty:
            pass
        self.master.after(120, self._poll_log_queue)


if __name__ == "__main__":
    root = tk.Tk()
    app = WatermarkApp(root)
    root.mainloop()
