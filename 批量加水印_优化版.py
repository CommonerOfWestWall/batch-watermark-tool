import os
import re
import sys
import shlex
import queue
import shutil
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".flv", ".wmv", ".webm", ".m4v")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def get_ffmpeg_path() -> str:
    """Return ffmpeg path: exe同目录优先 -> PATH -> 空字符串。"""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    for name in ('ffmpeg.exe', 'ffmpeg'):
        candidate = os.path.join(base, name)
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which('ffmpeg')
    return found if found else ''


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
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)


def ffmpeg_escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg drawtext filter."""
    # drawtext treats backslash, colon, apostrophe, percent and newlines specially.
    return (
        text.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace("\n", r"\n")
    )


def ffmpeg_escape_path(path: str) -> str:
    # For drawtext fontfile. Windows drive colon must be escaped.
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


class WatermarkApp:
    def __init__(self, master: tk.Tk):
        self.master = master
        self.master.title("批量水印处理工具 Pro")
        self.master.geometry("1180x760")
        self.master.minsize(1040, 680)

        self.video_files: list[str] = []
        self.watermark_image_path = tk.StringVar(value="")
        self.output_dir = tk.StringVar(value="")
        self.font_path = tk.StringVar(value=resource_font_path())
        self.running = False
        self.stop_requested = False
        self.log_queue: queue.Queue[str] = queue.Queue()

        self.preview_w = 640
        self.preview_h = 360
        self.watermark_pos = [0.78, 0.78]  # normalized x/y of top-left corner
        self.dragging = False
        self.preview_photo = None
        self.watermark_photo = None

        self._build_vars()
        self._build_ui()
        self._bind_traces()
        self._set_mode_visibility()
        self._update_preset_position()
        self._redraw_preview()
        self._poll_log_queue()

        if not get_ffmpeg_path():
            self._log("⚠ 未检测到 ffmpeg。请把 ffmpeg.exe 放到本程序同一文件夹，或加入系统 PATH。")

    def _build_vars(self):
        self.mode_var = tk.StringVar(value="beginner")
        self.watermark_type = tk.StringVar(value="image")
        self.recursive_var = tk.BooleanVar(value=False)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.copy_audio_var = tk.BooleanVar(value=True)
        self.keep_quality_var = tk.BooleanVar(value=True)

        self.opacity_var = tk.DoubleVar(value=0.85)
        self.img_width_var = tk.IntVar(value=220)
        self.text_var = tk.StringVar(value="示例水印")
        self.font_size_var = tk.IntVar(value=42)
        self.font_color_var = tk.StringVar(value="#FFFFFF")
        self.shadow_var = tk.BooleanVar(value=True)

        self.position_preset = tk.StringVar(value="右下角")
        self.margin_x_var = tk.IntVar(value=36)
        self.margin_y_var = tk.IntVar(value=36)
        self.custom_x_var = tk.IntVar(value=500)
        self.custom_y_var = tk.IntVar(value=280)
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

        root = ttk.Frame(self.master, padding=14)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=0, minsize=380)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="批量加水印", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="小白模式简洁安全，半专业模式展开完整参数", foreground="#666").grid(row=1, column=0, sticky="w")
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
        box.columnconfigure(1, weight=1)

        ttk.Radiobutton(box, text="图片水印", variable=self.watermark_type, value="image", command=self._set_type_visibility).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        ttk.Radiobutton(box, text="文字水印", variable=self.watermark_type, value="text", command=self._set_type_visibility).grid(row=0, column=1, padx=8, pady=8, sticky="w")

        self.image_frame = ttk.Frame(box, padding=(8, 0, 8, 8))
        self.image_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.image_frame.columnconfigure(1, weight=1)
        ttk.Button(self.image_frame, text="选择水印图片", command=self.select_watermark_image).grid(row=0, column=0, sticky="w")
        ttk.Label(self.image_frame, textvariable=self.watermark_image_path, foreground="#666", wraplength=250).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(self.image_frame, text="宽度").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.img_width_spin = ttk.Spinbox(self.image_frame, from_=20, to=2000, textvariable=self.img_width_var, width=8, command=self._redraw_preview)
        self.img_width_spin.grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Label(self.image_frame, text="px").grid(row=1, column=1, sticky="w", padx=(70, 0), pady=(8, 0))

        self.text_frame = ttk.Frame(box, padding=(8, 0, 8, 8))
        self.text_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.text_frame.columnconfigure(1, weight=1)
        ttk.Label(self.text_frame, text="文字").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.text_frame, textvariable=self.text_var).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(self.text_frame, text="字号").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Spinbox(self.text_frame, from_=8, to=300, textvariable=self.font_size_var, width=8, command=self._redraw_preview).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        self.pro_text_options = ttk.Frame(self.text_frame)
        self.pro_text_options.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(self.pro_text_options, text="字体文件", command=self.select_font).pack(side="left")
        ttk.Button(self.pro_text_options, text="颜色", command=self.pick_color).pack(side="left", padx=6)
        ttk.Checkbutton(self.pro_text_options, text="阴影", variable=self.shadow_var, command=self._redraw_preview).pack(side="left", padx=6)

        common = ttk.Frame(box, padding=(8, 0, 8, 8))
        common.grid(row=3, column=0, columnspan=2, sticky="ew")
        common.columnconfigure(1, weight=1)
        ttk.Label(common, text="透明度").grid(row=0, column=0, sticky="w")
        ttk.Scale(common, from_=0.05, to=1.0, variable=self.opacity_var, command=lambda e: self._redraw_preview()).grid(row=0, column=1, sticky="ew", padx=8)
        self.opacity_label = ttk.Label(common, width=5)
        self.opacity_label.grid(row=0, column=2, sticky="e")
        self._set_type_visibility()

    def _build_position_section(self):
        box = ttk.LabelFrame(self.left, text="3. 位置", style="Section.TLabelframe")
        box.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        box.columnconfigure(1, weight=1)
        presets = ["左上角", "右上角", "左下角", "右下角", "居中", "自定义"]
        ttk.Label(box, text="预设").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        ttk.Combobox(box, values=presets, textvariable=self.position_preset, state="readonly", width=12).grid(row=0, column=1, padx=8, pady=8, sticky="w")

        self.pro_position_options = ttk.Frame(box, padding=(8, 0, 8, 8))
        self.pro_position_options.grid(row=1, column=0, columnspan=2, sticky="ew")
        for i, (label, var) in enumerate((("横向边距", self.margin_x_var), ("纵向边距", self.margin_y_var), ("自定义 X", self.custom_x_var), ("自定义 Y", self.custom_y_var))):
            ttk.Label(self.pro_position_options, text=label).grid(row=i // 2, column=(i % 2) * 2, sticky="w", pady=3)
            ttk.Spinbox(self.pro_position_options, from_=0, to=9999, textvariable=var, width=7, command=self._update_preset_position).grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(5, 12), pady=3)

    def _build_output_section(self):
        box = ttk.LabelFrame(self.left, text="4. 输出", style="Section.TLabelframe")
        box.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        box.columnconfigure(1, weight=1)
        ttk.Button(box, text="选择输出目录", command=self.select_output_dir).grid(row=0, column=0, padx=8, pady=8, sticky="w")
        ttk.Label(box, textvariable=self.output_dir, foreground="#666", wraplength=250).grid(row=0, column=1, padx=8, pady=8, sticky="ew")

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
        ttk.Label(preview_tab, text="拖动画布上的水印可快速设置位置。预览使用 16:9 示意画面，实际会按视频分辨率换算。", foreground="#666").grid(row=0, column=0, sticky="w", pady=(0, 8))
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
        for var in [self.text_var, self.font_color_var, self.opacity_var, self.img_width_var, self.font_size_var]:
            var.trace_add("write", lambda *_: self._redraw_preview())
        for var in [self.position_preset, self.margin_x_var, self.margin_y_var, self.custom_x_var, self.custom_y_var]:
            var.trace_add("write", lambda *_: self._update_preset_position())

    def _set_mode_visibility(self):
        is_pro = self.mode_var.get() == "pro"
        widgets = [self.pro_file_options, self.pro_position_options, self.pro_output_options, self.pro_text_options]
        for w in widgets:
            if is_pro:
                w.grid()
            else:
                w.grid_remove()
        self._log("当前模式：" + ("半专业调节" if is_pro else "小白推荐"))

    def _set_type_visibility(self):
        if self.watermark_type.get() == "image":
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
            self.watermark_image_path.set(path)
            self._redraw_preview()

    def select_font(self):
        path = filedialog.askopenfilename(title="选择字体文件", filetypes=[("字体文件", "*.ttf *.ttc *.otf"), ("所有文件", "*.*")])
        if path:
            self.font_path.set(path)

    def pick_color(self):
        _, color = colorchooser.askcolor(color=self.font_color_var.get(), title="选择文字颜色")
        if color:
            self.font_color_var.set(color)

    def _refresh_file_label(self):
        self.file_count_label.config(text=f"已选择 {len(self.video_files)} 个视频")
        self._log(f"已载入 {len(self.video_files)} 个视频。")

    def _estimate_watermark_size(self):
        if self.watermark_type.get() == "image":
            w = max(20, min(self.preview_w, int(self.img_width_var.get() or 220)))
            h = int(w * 0.45)
            if Image and self.watermark_image_path.get() and os.path.exists(self.watermark_image_path.get()):
                try:
                    with Image.open(self.watermark_image_path.get()) as img:
                        ratio = img.height / max(img.width, 1)
                    h = int(w * ratio)
                except Exception:
                    pass
            return w, max(20, min(self.preview_h, h))
        text = self.text_var.get() or "文字水印"
        fs = int(self.font_size_var.get() or 42)
        return min(self.preview_w - 20, max(80, int(len(text) * fs * 0.72))), max(28, int(fs * 1.25))

    def _update_preset_position(self):
        try:
            wm_w, wm_h = self._estimate_watermark_size()
            mx = max(0, int(self.margin_x_var.get()))
            my = max(0, int(self.margin_y_var.get()))
            preset = self.position_preset.get()
            if preset == "左上角":
                x, y = mx, my
            elif preset == "右上角":
                x, y = self.preview_w - wm_w - mx, my
            elif preset == "左下角":
                x, y = mx, self.preview_h - wm_h - my
            elif preset == "居中":
                x, y = (self.preview_w - wm_w) // 2, (self.preview_h - wm_h) // 2
            elif preset == "自定义":
                x, y = int(self.custom_x_var.get()), int(self.custom_y_var.get())
            else:
                x, y = self.preview_w - wm_w - mx, self.preview_h - wm_h - my
            x = max(0, min(self.preview_w - wm_w, x))
            y = max(0, min(self.preview_h - wm_h, y))
            self.watermark_pos = [x / self.preview_w, y / self.preview_h]
        except Exception:
            pass
        self._redraw_preview()

    def _start_drag(self, event):
        self.dragging = True
        self._drag_watermark(event)

    def _drag_watermark(self, event):
        wm_w, wm_h = self._estimate_watermark_size()
        x = max(0, min(self.preview_w - wm_w, event.x - wm_w // 2))
        y = max(0, min(self.preview_h - wm_h, event.y - wm_h // 2))
        self.watermark_pos = [x / self.preview_w, y / self.preview_h]
        if self.position_preset.get() != "自定义":
            self.position_preset.set("自定义")
        self.custom_x_var.set(int(x))
        self.custom_y_var.set(int(y))
        self._redraw_preview()

    def _end_drag(self, event):
        self.dragging = False

    def _redraw_preview(self):
        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        self.canvas.create_rectangle(0, 0, self.preview_w, self.preview_h, fill="#1f2937", outline="")
        self.canvas.create_text(self.preview_w // 2, self.preview_h // 2 - 15, text="视频预览区", fill="#d1d5db", font=("Microsoft YaHei UI", 26, "bold"))
        self.canvas.create_text(self.preview_w // 2, self.preview_h // 2 + 22, text="拖动水印改变位置", fill="#9ca3af", font=("Microsoft YaHei UI", 12))
        try:
            self.opacity_label.config(text=f"{int(float(self.opacity_var.get()) * 100)}%")
        except Exception:
            pass

        wm_w, wm_h = self._estimate_watermark_size()
        x = int(self.watermark_pos[0] * self.preview_w)
        y = int(self.watermark_pos[1] * self.preview_h)
        x = max(0, min(self.preview_w - wm_w, x))
        y = max(0, min(self.preview_h - wm_h, y))

        if self.watermark_type.get() == "image":
            path = self.watermark_image_path.get()
            if Image and ImageTk and path and os.path.exists(path):
                try:
                    img = Image.open(path).convert("RGBA")
                    ratio = wm_w / max(img.width, 1)
                    img = img.resize((wm_w, max(1, int(img.height * ratio))))
                    alpha = img.getchannel("A").point(lambda p: int(p * float(self.opacity_var.get())))
                    img.putalpha(alpha)
                    self.watermark_photo = ImageTk.PhotoImage(img)
                    self.canvas.create_image(x, y, image=self.watermark_photo, anchor="nw", tags="watermark")
                    self.canvas.create_rectangle(x, y, x + img.width, y + img.height, outline="#60a5fa", dash=(4, 3))
                    return
                except Exception:
                    pass
            self.canvas.create_rectangle(x, y, x + wm_w, y + wm_h, fill="#2563eb", stipple="gray50", outline="#60a5fa", tags="watermark")
            self.canvas.create_text(x + wm_w // 2, y + wm_h // 2, text="图片水印", fill="white", tags="watermark")
        else:
            color = self.font_color_var.get() or "#FFFFFF"
            fs = max(8, min(90, int(self.font_size_var.get() or 42)))
            text = self.text_var.get() or "文字水印"
            if self.shadow_var.get():
                self.canvas.create_text(x + 2, y + 2, text=text, fill="#000000", font=("Microsoft YaHei UI", fs, "bold"), anchor="nw", tags="watermark")
            self.canvas.create_text(x, y, text=text, fill=color, font=("Microsoft YaHei UI", fs, "bold"), anchor="nw", tags="watermark")
            self.canvas.create_rectangle(x, y, x + wm_w, y + wm_h, outline="#60a5fa", dash=(4, 3))

    def validate(self) -> bool:
        if not self.video_files:
            messagebox.showerror("缺少视频", "请先选择至少一个视频文件。")
            return False
        if not self.output_dir.get():
            messagebox.showerror("缺少输出目录", "请选择输出目录。")
            return False
        if self.watermark_type.get() == "image":
            path = self.watermark_image_path.get()
            if not path or not os.path.exists(path):
                messagebox.showerror("缺少水印图片", "请选择一张有效的水印图片。")
                return False
        else:
            if not self.text_var.get().strip():
                messagebox.showerror("缺少文字", "请输入文字水印内容。")
                return False
            if not self.font_path.get() or not os.path.exists(self.font_path.get()):
                messagebox.showwarning("字体提醒", "未找到可用字体文件，FFmpeg 可能无法渲染中文。建议在半专业模式中选择字体。")
        if not get_ffmpeg_path():
            messagebox.showerror("未找到 FFmpeg", "请把 ffmpeg.exe 放到本程序同一文件夹，或确保 FFmpeg 已加入系统 PATH。")
            return False
        return True

    def _scale_expr(self):
        # Convert preview x/y to actual video coordinate using main_w/main_h.
        x_ratio, y_ratio = self.watermark_pos
        return f"round(main_w*{x_ratio:.6f})", f"round(main_h*{y_ratio:.6f})"

    def generate_ffmpeg_cmd(self, input_file: str, output_file: str) -> list[str]:
        cmd = [get_ffmpeg_path(), "-hide_banner", "-y" if self.overwrite_var.get() else "-n", "-i", input_file]
        x_expr, y_expr = self._scale_expr()
        opacity = max(0.05, min(1.0, float(self.opacity_var.get())))

        if self.watermark_type.get() == "image":
            width = max(20, int(self.img_width_var.get()))
            cmd += ["-i", self.watermark_image_path.get()]
            # Scale watermark width relative to video width according to preview ratio.
            # User-entered width is treated as width on a 640px preview canvas.
            ratio = width / self.preview_w
            filter_str = (
                f"[1:v]scale=round(main_w*{ratio:.6f}):-1,colorchannelmixer=aa={opacity:.3f}[wm];"
                f"[0:v][wm]overlay=x={x_expr}:y={y_expr}:format=auto"
            )
            cmd += ["-filter_complex", filter_str]
        else:
            font = ffmpeg_escape_path(self.font_path.get()) if self.font_path.get() else ""
            text = ffmpeg_escape_drawtext(self.text_var.get())
            font_size = max(8, int(self.font_size_var.get()))
            color = self.font_color_var.get().lstrip("#") or "FFFFFF"
            shadow = ":shadowcolor=black@0.55:shadowx=2:shadowy=2" if self.shadow_var.get() else ""
            font_part = f":fontfile='{font}'" if font else ""
            filter_str = (
                f"drawtext=text='{text}'{font_part}:fontsize={font_size}:"
                f"fontcolor=0x{color}@{opacity:.3f}:x={x_expr}:y={y_expr}{shadow}"
            )
            cmd += ["-vf", filter_str]

        if self.copy_audio_var.get():
            cmd += ["-c:a", "copy"]
        if self.keep_quality_var.get():
            cmd += ["-c:v", "libx264", "-crf", str(int(self.crf_var.get())), "-preset", self.preset_var.get(), "-pix_fmt", "yuv420p"]
        cmd += [output_file]
        return cmd

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
                last_lines = []
                for line in p.stdout or []:
                    if self.stop_requested and p.poll() is None:
                        p.terminate()
                    line = line.strip()
                    if line:
                        last_lines.append(line)
                        last_lines = last_lines[-4:]
                        if "time=" in line or "Error" in line or "Invalid" in line:
                            self._log(line)
                code = p.wait()
                if code == 0:
                    success += 1
                    self._log(f"完成：{output_path}")
                else:
                    failed += 1
                    self._log("失败：" + os.path.basename(video) + "\n" + "\n".join(last_lines))
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
