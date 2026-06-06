#!/usr/bin/env python3
"""
LutScope — 3D LUT 风格评估与自然语言查询工具
=============================================
兼容所有 OpenAI API 格式的提供商

环境变量:
    OPENAI_API_KEY    — API 密钥 (必需, 或用 --api-key)
    OPENAI_BASE_URL   — API 地址 (默认 https://api.openai.com/v1)
    OPENAI_MODEL      — 模型名 (默认 gpt-4o)

用法:
    python app.py                                # 启动 TUI
    python app.py --cli -i photo.jpg             # 单图评估
    python app.py --cli -i photo1.jpg -i photo2.jpg  # 多图评估
    python app.py --cli -i test_images/          # 目录扫描
    python app.py --cli -q "德味"                 # 评估+风格查询
    python app.py --cli -I                        # 交互式查询

依赖 (运行时可缺失, 部分功能降级):
    Pillow        — 图像处理 (pip install Pillow)
    requests      — API 调用 (pip install requests)
"""

import os
import sys
import json
import time
import re
import glob
import curses
import curses.textpad
import argparse
import zipfile
import tempfile
import shutil
from typing import Optional, List, Tuple, Dict

# ============================================================
#  Zipapp / 单文件运行时支持
# ============================================================

_LUT_BINARY_CACHE = None


def find_lut_tool() -> str:
    """查找 lut_tool 二进制: 源码 / zipapp / PATH。"""
    global _LUT_BINARY_CACHE
    if _LUT_BINARY_CACHE and os.path.exists(_LUT_BINARY_CACHE):
        return _LUT_BINARY_CACHE

    # 当前目录
    if os.path.exists("./lut_tool"):
        _LUT_BINARY_CACHE = os.path.abspath("./lut_tool")
        return _LUT_BINARY_CACHE

    # 脚本同级目录
    d = os.path.dirname(os.path.abspath(__file__))
    c = os.path.join(d, "lut_tool")
    if os.path.exists(c):
        _LUT_BINARY_CACHE = c
        return _LUT_BINARY_CACHE

    # zipapp 内提取
    if (not __file__.endswith(".py") or
            (os.path.isfile(sys.argv[0]) and ".pyz" in sys.argv[0])):
        try:
            td = os.path.join(tempfile.gettempdir(), "LutScope")
            os.makedirs(td, exist_ok=True)
            lp = os.path.join(td, "lut_tool")
            if not os.path.exists(lp):
                with zipfile.ZipFile(sys.argv[0]) as z:
                    z.extract("lut_tool", td)
                os.chmod(lp, 0o755)
            _LUT_BINARY_CACHE = lp
            return lp
        except Exception:
            pass

    return "./lut_tool"


def discover_all_images(dirs: list = None) -> List[str]:
    """扫描一个或多个目录中的图片文件。"""
    if dirs is None:
        dirs = ["."]
    images = []
    seen = set()
    for d in dirs:
        if not os.path.isdir(d):
            if os.path.isfile(d):
                images.append(os.path.abspath(d))
            continue
        for ext in ["*.ppm", "*.png", "*.jpg", "*.jpeg",
                     "*.tif", "*.tiff", "*.bmp", "*.webp"]:
            for f in sorted(glob.glob(os.path.join(d, ext))):
                abspath = os.path.abspath(f)
                if abspath not in seen:
                    seen.add(abspath)
                    images.append(abspath)
    return images
    """查找 lut_tool 二进制: 源码 / zipapp / PATH。"""
    global _LUT_BINARY_CACHE
    if _LUT_BINARY_CACHE and os.path.exists(_LUT_BINARY_CACHE):
        return _LUT_BINARY_CACHE

    # 当前目录
    if os.path.exists("./lut_tool"):
        _LUT_BINARY_CACHE = os.path.abspath("./lut_tool")
        return _LUT_BINARY_CACHE

    # 脚本同级目录
    d = os.path.dirname(os.path.abspath(__file__))
    c = os.path.join(d, "lut_tool")
    if os.path.exists(c):
        _LUT_BINARY_CACHE = c
        return _LUT_BINARY_CACHE

    # zipapp 内提取
    if (not __file__.endswith(".py") or
            (os.path.isfile(sys.argv[0]) and ".pyz" in sys.argv[0])):
        try:
            td = os.path.join(tempfile.gettempdir(), "LutScope")
            os.makedirs(td, exist_ok=True)
            lp = os.path.join(td, "lut_tool")
            if not os.path.exists(lp):
                with zipfile.ZipFile(sys.argv[0]) as z:
                    z.extract("lut_tool", td)
                os.chmod(lp, 0o755)
            _LUT_BINARY_CACHE = lp
            return lp
        except Exception:
            pass

    return "./lut_tool"


# ============================================================
#  ANSI / curses 工具
# ============================================================

# ============================================================
#  导入核心引擎
# ============================================================
try:
    from engine import (
        LutEntry, EvalResult, ColorStats,
        discover_luts, extract_cube, discover_test_image,
        convert_to_ppm, add_watermark,
        run_lut_tool, extract_color_stats, stats_diff, stats_to_text,
        run_pipeline, format_result_summary, export_results_json,
        check_api_key, get_api_config, DEFAULT_MODEL, DEFAULT_API_URL,
        match_query, format_query_result,
        merge_results, format_merge_result,
    )
    HAS_ENGINE = True
except ImportError:
    HAS_ENGINE = False


# ============================================================
#  ANSI / curses 工具
# ============================================================

# 颜色对索引
C_HEADER   = 1
C_FOOTER   = 2
C_TITLE    = 3
C_OK       = 4
C_ERROR    = 5
C_HIGHLIGHT = 6
C_MUTED    = 7
C_MEDAL_1  = 8
C_MEDAL_2  = 9
C_MEDAL_3  = 10
C_BAR      = 11
C_LABEL    = 12

def cpad(text: str, width: int) -> str:
    """居中对齐 + 填充空格。"""
    if len(text) >= width:
        return text[:width]
    left = (width - len(text)) // 2
    return " " * left + text + " " * (width - left - left)

def trunc(text: str, max_w: int) -> str:
    """截断文本并加 ..."""
    if len(text) <= max_w:
        return text
    return text[:max_w - 3] + "..."

def draw_box(win, y: int, x: int, h: int, w: int, title: str = ""):
    """绘制带边框的盒子（全部 addch 加异常保护）。"""
    try:
        win.addch(y, x, curses.ACS_ULCORNER)
        win.hline(y, x + 1, curses.ACS_HLINE, w - 2)
        win.addch(y, x + w - 1, curses.ACS_URCORNER)
        for row in range(y + 1, y + h - 1):
            win.addch(row, x, curses.ACS_VLINE)
            win.addch(row, x + w - 1, curses.ACS_VLINE)
        win.addch(y + h - 1, x, curses.ACS_LLCORNER)
        win.hline(y + h - 1, x + 1, curses.ACS_HLINE, w - 2)
        win.addch(y + h - 1, x + w - 1, curses.ACS_LRCORNER)
        if title:
            win.addstr(y, x + 2, f" {title} ")
    except curses.error:
        pass


# ============================================================
#  TUI 应用
# ============================================================

class LutTUI:
    """LUT 评估 TUI 主应用 (curses)。"""

    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.screen = "main"  # main | processing | results | settings

        # 数据
        self.luts: List[LutEntry] = []
        self.lut_filter: str = ""         # LUT 正则过滤器
        self.test_images: List[str] = []  # 所有发现的测试图
        self.selected_images: set = set() # 选中的下标
        self.output_dir = "./results"
        self.lut_dir = "."
        self.lut_tool_path = find_lut_tool()
        self.results: List[EvalResult] = []
        self.best_lut = ""
        self.best_reason = ""
        self.watermarked_images: List[str] = []

        # API 配置 (可从环境变量覆盖, TUI 内也可编辑)
        self.api_key, self.api_base_url, self.api_model = get_api_config()

        # 多图结果追踪
        self.per_image_results: Dict[str, List[EvalResult]] = {}

        # 导出设置
        self.auto_export: bool = True
        self.export_path: str = ""

        # 处理状态
        self.progress_msg = ""
        self.progress_pct = 0.0
        self.processing_done = False
        self.processing_error = ""
        self.running = True

        # 终端尺寸
        self.h, self.w = 0, 0

    # ----------------------------------------------------------
    #  curses 初始化
    # ----------------------------------------------------------

    def setup_colors(self):
        """初始化颜色对。"""
        curses.start_color()
        curses.use_default_colors()

        # 颜色对: (id, fg, bg)
        pairs = [
            (C_HEADER,    curses.COLOR_WHITE,   curses.COLOR_BLUE),
            (C_FOOTER,    curses.COLOR_WHITE,   curses.COLOR_BLUE),
            (C_TITLE,     curses.COLOR_YELLOW,  -1),
            (C_OK,        curses.COLOR_GREEN,   -1),
            (C_ERROR,     curses.COLOR_RED,     -1),
            (C_HIGHLIGHT, curses.COLOR_YELLOW,  -1),
            (C_MUTED,     curses.COLOR_CYAN,    -1),
            (C_MEDAL_1,   curses.COLOR_YELLOW,  -1),
            (C_MEDAL_2,   curses.COLOR_WHITE,   -1),
            (C_MEDAL_3,   curses.COLOR_RED,     -1),
            (C_BAR,       curses.COLOR_GREEN,   -1),
            (C_LABEL,     curses.COLOR_MAGENTA, -1),
        ]
        for pid, fg, bg in pairs:
            try:
                curses.init_pair(pid, fg, bg if bg != -1 else -1)
            except Exception:
                curses.init_pair(pid, fg, curses.COLOR_BLACK)

    # ----------------------------------------------------------
    #  绘制: 通用组件
    # ----------------------------------------------------------

    def draw_header(self, title: str = "  LUT 风格评估工具  "):
        """绘制顶部标题栏。"""
        self.w, self.h = curses.COLS, curses.LINES  # 注意: curses 坐标是 (y, x)
        # 在 curses 中: COLS=宽, LINES=高
        curses.update_lines_cols()
        self.w = curses.COLS
        self.h = curses.LINES

        attr = curses.color_pair(C_HEADER) | curses.A_BOLD
        bar = " " * self.w
        try:
            self.stdscr.addstr(0, 0, bar, attr)
            pad = max(0, self.w - len(title))
            x = pad // 2
            self.stdscr.addstr(0, x, title, attr)
        except curses.error:
            pass

    def draw_footer(self, items: List[Tuple[str, str]]):
        """绘制底部菜单栏。 items: [(key, label), ...]"""
        attr = curses.color_pair(C_FOOTER) | curses.A_BOLD
        y = self.h - 1
        bar = " " * self.w
        try:
            self.stdscr.addstr(y, 0, bar, attr)
            left = 1
            for key, label in items:
                text = f" [{key}] {label} "
                self.stdscr.addstr(y, left, text, attr)
                left += len(text) + 2
        except curses.error:
            pass

    def draw_status_bar(self, text: str, y: int = None):
        """在指定行绘制状态条。"""
        if y is None:
            y = self.h - 2
        attr = curses.color_pair(C_MUTED)
        try:
            self.stdscr.addstr(y, 0, " " * self.w, attr)
            self.stdscr.addstr(y, 1, trunc(text, self.w - 3), attr)
        except curses.error:
            pass

    def draw_progress_bar(self, y: int, x: int, w: int, pct: float):
        """绘制进度条。"""
        if w < 10:
            return
        bar_w = w - 8
        filled = int(bar_w * pct / 100.0)
        empty = bar_w - filled

        try:
            self.stdscr.addstr(y, x, f"[", curses.color_pair(C_MUTED))
            self.stdscr.addstr(y, x + 1,
                               "█" * filled,
                               curses.color_pair(C_BAR) | curses.A_BOLD)
            self.stdscr.addstr(y, x + 1 + filled,
                               "░" * empty,
                               curses.color_pair(C_MUTED))
            self.stdscr.addstr(y, x + 1 + bar_w,
                               f"] {pct:.0f}%",
                               curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
        except curses.error:
            pass

    def draw_list_item(self, y: int, x: int, text: str,
                       selected: bool = False, indent: int = 2):
        """绘制列表项。"""
        prefix = "  " * indent
        attr = curses.A_NORMAL
        if selected:
            attr = curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD
        try:
            self.stdscr.addstr(y, x, f"{prefix}{trunc(text, self.w - x - 4)}", attr)
        except curses.error:
            pass

    # ----------------------------------------------------------
    #  绘制: 主屏幕
    # ----------------------------------------------------------

    def draw_main_screen(self):
        """主屏幕: LUT 列表 + 配置 + 操作按钮。"""
        self.stdscr.clear()

        # 常量区域
        HEADER_H = 1
        FOOTER_H = 1
        PADDING = 1
        y = HEADER_H + PADDING

        # -- 测试图列表 --
        try:
            count = len(self.selected_images)
            total = len(self.test_images)
            self.stdscr.addstr(y, 2,
                f"📁 测试图 ({count}/{total} 选中):", curses.A_BOLD)
        except curses.error:
            pass
        y += 1
        max_img_show = min(len(self.test_images), self.h - y - 20)
        for i in range(max_img_show):
            img = self.test_images[i]
            name = os.path.basename(img)
            checked = "☑" if i in self.selected_images else "☐"
            pair = curses.color_pair(C_OK) if i in self.selected_images else curses.A_NORMAL
            try:
                self.stdscr.addstr(y, 4, f" {checked}", pair)
                self.stdscr.addstr(y, 7, trunc(name, max(15, self.w - 30)), pair)
                # 显示所属目录
                parent = os.path.basename(os.path.dirname(img))
                if parent:
                    self.stdscr.addstr(y, self.w - 30, parent, curses.color_pair(C_MUTED))
            except curses.error:
                pass
            y += 1
        if len(self.test_images) > max_img_show:
            try:
                self.stdscr.addstr(y, 4, f"... 还有 {len(self.test_images) - max_img_show} 个",
                                   curses.color_pair(C_MUTED))
            except curses.error:
                pass
            y += 1
        y += 1

        # 图像操作提示
        try:
            self.stdscr.addstr(y, 2, "[SPACE]选图 [A]全选 [N]全不选  [H]帮助指南",
                               curses.color_pair(C_HIGHLIGHT))
        except curses.error:
            pass
        y += 1

        # -- API 状态 --
        key_ok, key_info = check_api_key()
        api_text = f"🔑 {key_info}" if key_ok else "🔑 API Key 未设置 (将使用本地分析)"
        api_attr = curses.color_pair(C_OK) if key_ok else curses.color_pair(C_ERROR)
        try:
            self.stdscr.addstr(y, 2, api_text, api_attr)
        except curses.error:
            pass
        y += 1

        # 显示 base_url 和 model
        try:
            self.stdscr.addstr(y, 4,
                               f"URL: {trunc(self.api_base_url, 45)}",
                               curses.color_pair(C_MUTED))
            self.stdscr.addstr(y, 55, f"Model: {self.api_model}",
                               curses.color_pair(C_MUTED))
        except curses.error:
            pass
        y += 2

        # -- LUT 筛选输入 --
        try:
            self.stdscr.addstr(y, 2, "🔍 筛选: ", curses.A_BOLD)
            filter_display = self.lut_filter if self.lut_filter else "(正则, 按 F 输入)"
            self.stdscr.addstr(y, 12, filter_display,
                               curses.color_pair(C_HIGHLIGHT) if self.lut_filter
                               else curses.color_pair(C_MUTED))
        except curses.error:
            pass
        y += 1

        # -- LUT 列表 (已筛选) --
        filtered = self.luts
        if self.lut_filter:
            try:
                pat = re.compile(self.lut_filter, re.IGNORECASE)
                filtered = [l for l in self.luts if pat.search(l.name)]
            except re.error:
                pass  # 正则出错时显示全部

        if self.luts:
            title = f"📦 发现的 LUT ({len(filtered)}/{len(self.luts)} 个):"
            try:
                self.stdscr.addstr(y, 2, title, curses.A_BOLD)
            except curses.error:
                pass
            y += 1

            max_display = min(len(filtered), self.h - y - 8)
            for i in range(max_display):
                lut = filtered[i]
                src = f" (来自 {os.path.basename(lut.zip_path)})" if lut.from_zip else ""
                try:
                    self.stdscr.addstr(y, 4, f" ✓  ", curses.color_pair(C_OK))
                    self.stdscr.addstr(y, 9, trunc(lut.name, 30), curses.A_BOLD)
                    self.stdscr.addstr(y, 42, src, curses.color_pair(C_MUTED))
                except curses.error:
                    pass
                y += 1

            if len(filtered) > max_display:
                try:
                    self.stdscr.addstr(y, 4,
                                       f"... 还有 {len(filtered) - max_display} 个",
                                       curses.color_pair(C_MUTED))
                except curses.error:
                    pass
                y += 1
        else:
            try:
                self.stdscr.addstr(y, 2, "📦 未发现 LUT 文件", curses.color_pair(C_ERROR))
            except curses.error:
                pass
            y += 1

        # -- 扫描按钮提示 --
        y += 1
        try:
            self.stdscr.addstr(y, 2, "[R]重扫 [F]筛选 [C]清除筛选 [1]评估",
                               curses.color_pair(C_HIGHLIGHT))
        except curses.error:
            pass

        # -- 结果区 (如果有之前的评估结果) --
        if self.results:
            y = self.h - 6
            try:
                self.stdscr.addstr(y, 2, "上次评估结果:", curses.A_BOLD)
                y += 1
                self.stdscr.addstr(y, 4,
                                   f"🏆 最佳: {self.best_lut}",
                                   curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
                best = self.results[0] if self.results else None
                if best:
                    y += 1
                    tags = ", ".join(best.style_tags)
                    self.stdscr.addstr(y, 6,
                                       f"评分: {best.score:.0f}/100  标签: {tags}",
                                       curses.color_pair(C_OK))
            except curses.error:
                pass

        # -- 底部 --
        self.draw_header()
        self.draw_footer([
            ("1", "开始评估"),
            ("2", "⚙️ 设置"),
            ("3", "导出 JSON"),
            ("H", "帮助"),
            ("R", "扫描"),
            ("Q", "退出"),
        ])
        self.stdscr.refresh()

    # ----------------------------------------------------------
    #  绘制: 设置界面
    # ----------------------------------------------------------

    def draw_settings_screen(self):
        """配置界面 — 支持在 TUI 内编辑 API 设置、路径等。"""
        self.stdscr.clear()
        self.draw_header("  ⚙️ 设置  ")
        y = 3

        # 可编辑字段列表: (标签, 变量名, 当前值, 提示)
        fields = [
            ("API Key",     self.api_key,      "输入 API Key"),
            ("API Base URL", self.api_base_url, "如 https://api.openai.com/v1"),
            ("Model",       self.api_model,    "如 gpt-4o / deepseek-chat"),
            ("LUT 目录",    self.lut_dir,      "如 ./luts"),
            ("输出目录",    self.output_dir,   "如 ./results"),
            ("自动导出",    "是" if self.auto_export else "否", "开关"),
        ]

        try:
            self.stdscr.addstr(y, 2, "按数字键编辑对应字段:", curses.A_BOLD)
        except curses.error:
            pass
        y += 2

        for i, (label, val, hint) in enumerate(fields):
            prefix = f" [{i+1}] "
            label_w = 16
            val_text = trunc(str(val), max(20, self.w - label_w - 12))
            try:
                self.stdscr.addstr(y, 2, prefix, curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
                self.stdscr.addstr(y, 7, label.ljust(label_w), curses.color_pair(C_LABEL))
                self.stdscr.addstr(y, 7 + label_w, val_text, curses.A_BOLD)
            except curses.error:
                pass
            y += 1

        # 当前 API 状态
        y += 2
        api_key_for_check = self.api_key or os.environ.get("OPENAI_API_KEY", "")
        if api_key_for_check and self.api_base_url:
            status = f"🔑 API 可用 → {trunc(self.api_base_url.replace('https://',''), 30)} | {self.api_model}"
            attr = curses.color_pair(C_OK)
        else:
            status = "⚠️  API 未配置完整 (将使用本地分析)"
            attr = curses.color_pair(C_ERROR)
        try:
            self.stdscr.addstr(y, 2, status, attr)
        except curses.error:
            pass
        y += 2

        # 操作提示
        try:
            self.stdscr.addstr(y, 2, "操作说明: 按 1-6 编辑, ENTER 确认, ESC 返回",
                               curses.color_pair(C_MUTED))
        except curses.error:
            pass

        self.draw_footer([
            ("1-6", "编辑字段"),
            ("R", "重置为环境变量"),
            ("ESC", "返回"),
            ("Q", "退出"),
        ])
        self.stdscr.refresh()

    def draw_help_screen(self):
        """帮助指南屏幕 — 所有操作说明。"""
        self.stdscr.clear()
        self.draw_header("  📖 LutScope 使用指南  ")
        y = 2

        help_lines = [
            ("", curses.A_BOLD, curses.color_pair(C_TITLE)),
            ("  🎯 风格查询（核心功能）", curses.A_BOLD, curses.color_pair(C_HIGHLIGHT)),
            ("", 0, 0),
            ("  评估完成后 → 结果屏按 S 键 → 输入风格描述", 0, 0),
            ("", 0, 0),
            ("  示例:  德味  /  复古胶片  /  黑白  /  电影感", 0, 0),
            ("         日系小清新  /  赛博朋克  /  暖调人像", 0, 0),
            ("         冷调忧郁  /  王家卫风格  /  港风", 0, 0),
            ("", 0, 0),
            ("  AI 会逐字输出匹配结果：哪个 LUT 最适合、匹配度、理由", 0, 0),
            ("", 0, 0),
            ("  📦 多图整合", curses.A_BOLD, curses.color_pair(C_HIGHLIGHT)),
            ("", 0, 0),
            ("  评估多张图后 → 结果屏按 M → AI 综合所有图片给出排名", 0, 0),
            ("", 0, 0),
            ("  ⌨️ 全部键位", curses.A_BOLD, curses.color_pair(C_HIGHLIGHT)),
            ("", 0, 0),
            ("  主屏:  1=评估  2=设置  3=导出  F=正则筛选  R=扫描", 0, 0),
            ("         SPACE=选图  A=全选  N=全不选  H=本帮助  Q=退出", 0, 0),
            ("", 0, 0),
            ("  结果屏: S=查风格  M=多图整合  E=导出  R=重评  Q=退出", 0, 0),
            ("          1-9=查看LUT详情  ESC=返回主屏", 0, 0),
            ("", 0, 0),
            ("  设置屏: 1-6=编辑字段  R=重置环境变量  ESC=返回", 0, 0),
            ("", 0, 0),
            ("  ⚙️ API 配置", curses.A_BOLD, curses.color_pair(C_HIGHLIGHT)),
            ("", 0, 0),
            ("  设置屏(主屏按2)可编辑: API Key / Base URL / Model", 0, 0),
            ("  也可用环境变量:  OPENAI_API_KEY  OPENAI_BASE_URL  OPENAI_MODEL", 0, 0),
            ("", 0, 0),
            ("  无 API Key 时自动使用本地色彩统计分析模式", 0, 0),
            ("", 0, 0),
        ]

        for text, attr, color in help_lines:
            if attr:
                try:
                    if color:
                        self.stdscr.addstr(y, 0, text, attr | color)
                    else:
                        self.stdscr.addstr(y, 0, text, attr)
                except curses.error:
                    pass
            else:
                try:
                    self.stdscr.addstr(y, 0, text)
                except curses.error:
                    pass
            y += 1
            if y >= self.h - 2:
                break

        self.draw_footer([
            ("H", "再看一次"),
            ("ESC", "返回"),
            ("Q", "退出"),
        ])
        self.stdscr.refresh()
        # 等待按键
        self.stdscr.getch()
        self.screen = "main"

    def _edit_field(self, prompt: str, current: str, field_w: int = 50) -> Optional[str]:
        """通用 TUI 文本输入。"""
        # 输入框居中
        box_w = min(field_w, self.w - 8)
        box_h = 3
        y0 = self.h // 2 - 2
        x0 = (self.w - box_w) // 2

        win = curses.newwin(box_h, box_w, y0, x0)
        win.bkgd(' ', curses.A_NORMAL)
        win.erase()

        # 边框（加异常保护）
        try:
            win.addch(0, 0, curses.ACS_ULCORNER)
            win.hline(0, 1, curses.ACS_HLINE, box_w - 2)
            win.addch(0, box_w - 1, curses.ACS_URCORNER)
            win.addch(2, 0, curses.ACS_LLCORNER)
            win.hline(2, 1, curses.ACS_HLINE, box_w - 2)
            win.addch(2, box_w - 1, curses.ACS_LRCORNER)
        except curses.error:
            pass

        if len(prompt) > box_w - 2:
            prompt_display = prompt[:box_w - 5] + "..."
        else:
            prompt_display = prompt
        win.addstr(0, 2, f" {prompt_display} ", curses.color_pair(C_HIGHLIGHT))
        win.refresh()

        curses.curs_set(1)

        # 输入
        edit_win = curses.newwin(1, box_w - 2, y0 + 1, x0 + 1)
        if current:
            edit_win.addstr(0, 0, current)
        edit_box = curses.textpad.Textbox(edit_win)
        edit_win.refresh()
        result = edit_box.edit().strip()

        curses.curs_set(0)
        self.stdscr.touchwin()
        self.stdscr.refresh()

        return result if result else None

    # ----------------------------------------------------------
    #  绘制: 处理进度
    # ----------------------------------------------------------

    def draw_processing_screen(self):
        """处理进度界面。"""
        self.stdscr.clear()
        self.draw_header("  处理中...  ")

        y = 3
        try:
            self.stdscr.addstr(y, 2, self.progress_msg or "准备中...",
                               curses.A_BOLD)
        except curses.error:
            pass
        y += 2

        # 整体进度条
        self.draw_progress_bar(y, 4, self.w - 8, self.progress_pct)

        y += 2
        if self.processing_error:
            try:
                self.stdscr.addstr(y, 2, f"错误: {self.processing_error}",
                                   curses.color_pair(C_ERROR))
            except curses.error:
                pass
        elif self.processing_done:
            try:
                self.stdscr.addstr(y, 2, "✅ 处理完成! 按任意键查看结果...",
                                   curses.color_pair(C_OK) | curses.A_BOLD)
            except curses.error:
                pass
        else:
            try:
                self.stdscr.addstr(y, 2, "⏳ 正在处理 LUT... 按 [Q] 取消",
                                   curses.color_pair(C_MUTED))
            except curses.error:
                pass

        self.draw_footer([
            ("Q", "取消 / 返回"),
        ])
        self.stdscr.refresh()

    # ----------------------------------------------------------
    #  绘制: 结果界面
    # ----------------------------------------------------------

    def draw_results_screen(self):
        """评估结果界面。"""
        self.stdscr.clear()
        self.draw_header("  评估结果  ")

        y = 2
        if not self.results:
            try:
                self.stdscr.addstr(y, 2, "暂无评估结果。", curses.color_pair(C_ERROR))
            except curses.error:
                pass
            self.draw_footer([
                ("ESC", "返回"),
                ("Q", "退出"),
            ])
            self.stdscr.refresh()
            return

        # 最佳 LUT
        try:
            self.stdscr.addstr(y, 2, f"🏆  最佳 LUT: {self.best_lut}",
                               curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
        except curses.error:
            pass
        y += 1
        if self.best_reason:
            try:
                self.stdscr.addstr(y, 4, trunc(self.best_reason, self.w - 10),
                                   curses.color_pair(C_OK))
            except curses.error:
                pass
            y += 1
        y += 1

        # 排名表
        try:
            # 表头
            self.stdscr.addstr(y, 2, "排名  LUT", curses.A_UNDERLINE | curses.A_BOLD)
            self.stdscr.addstr(y, 30, "评分", curses.A_UNDERLINE | curses.A_BOLD)
            self.stdscr.addstr(y, 36, "风格标签", curses.A_UNDERLINE | curses.A_BOLD)
        except curses.error:
            pass
        y += 1

        medals = ["🥇", "🥈", "🥉"]
        medal_colors = [C_MEDAL_1, C_MEDAL_2, C_MEDAL_3]

        max_display = min(len(self.results), self.h - y - 5)
        for i in range(max_display):
            r = self.results[i]
            medal = medals[i] if i < 3 else f"{i+1:2d}"
            mc = medal_colors[i] if i < 3 else curses.A_NORMAL
            tags = ", ".join(r.style_tags[:3])
            try:
                self.stdscr.addstr(y, 2, f" {medal}  ", curses.color_pair(mc) | curses.A_BOLD)
                self.stdscr.addstr(y, 9, trunc(r.name, 20), curses.A_BOLD)
                score_attr = curses.color_pair(C_OK) if r.score >= 70 else \
                             curses.color_pair(C_HIGHLIGHT) if r.score >= 50 else \
                             curses.color_pair(C_ERROR)
                self.stdscr.addstr(y, 30, f"{r.score:3.0f}", score_attr)
                self.stdscr.addstr(y, 36, trunc(tags, self.w - 38))
            except curses.error:
                pass
            y += 1

        # 选中某个 LUT 查看详情
        if max_display > 0:
            y = max(y, self.h - 6)
            try:
                self.stdscr.addstr(y, 2, "按 [1-9] 查看对应 LUT 详情",
                                   curses.color_pair(C_MUTED))
            except curses.error:
                pass

        # 导出提示
        try:
            self.stdscr.addstr(self.h - 4, 2,
                               "水印图已保存至: " + trunc(self.output_dir, self.w - 22),
                               curses.color_pair(C_MUTED))
        except curses.error:
            pass

        # 整合按钮（多图时显示）
        merge_btn = "M" if len(self.per_image_results) > 1 else ""
        items = [
            ("ESC", "返回主屏"),
            ("S", "查风格"),
            ("E", "导出 JSON"),
        ]
        if merge_btn:
            items.append(("M", "多图整合"))
        items += [("R", "重新评估"), ("Q", "退出")]
        self.draw_footer(items)
        self.stdscr.refresh()

    # ----------------------------------------------------------
    #  绘制: LUT 详情弹窗
    # ----------------------------------------------------------

    def show_lut_detail(self, idx: int):
        """在结果界面显示某个 LUT 的详细分析。"""
        if idx < 0 or idx >= len(self.results):
            return
        r = self.results[idx]

        h, w = 14, 60
        y0 = (self.h - h) // 2
        x0 = (self.w - w) // 2
        win = curses.newwin(h, w, y0, x0)
        win.bkgd(' ', curses.color_pair(C_HEADER))
        win.erase()
        draw_box(win, 0, 0, h, w, f" {r.name} 详情 ")

        try:
            # 评分
            win.addstr(2, 2, f"评分: {r.score:.0f}/100",
                       curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
            # 标签
            tags = ", ".join(r.style_tags)
            win.addstr(3, 2, f"风格: {tags}", curses.color_pair(C_OK))
            # 描述
            win.addstr(4, 2, "描述:", curses.A_BOLD)
            desc = r.description
            for i in range(0, len(desc), w - 6):
                win.addstr(5 + i // (w - 6), 4,
                           trunc(desc[i:i + w - 6], w - 6))
            # 分析
            analysis_y = 7 if len(desc) <= w - 6 else 6
            win.addstr(analysis_y, 2, "分析:", curses.A_BOLD)
            ana = r.analysis
            max_lines = h - analysis_y - 3
            for i in range(max_lines):
                start = i * (w - 6)
                if start >= len(ana):
                    break
                win.addstr(analysis_y + 1 + i, 4,
                           trunc(ana[start:start + w - 6], w - 6))

            # 底部提示
            win.addstr(h - 1, 2, "按任意键关闭",
                       curses.color_pair(C_MUTED))

        except curses.error:
            pass

        win.refresh()
        win.getch()
        del win
        self.stdscr.touchwin()
        self.stdscr.refresh()

    # ----------------------------------------------------------
    #  处理逻辑 (非阻塞)
    # ----------------------------------------------------------

    def run_scan(self):
        """扫描 LUT 和测试图。"""
        self.luts = discover_luts(self.lut_dir)
        self.test_images = discover_all_images([self.lut_dir, "."])
        # 默认全选
        self.selected_images = set(range(len(self.test_images)))

    def run_full_pipeline(self, luts_to_process=None, image_indices=None):
        """完整处理管线 — 支持多图。"""
        target_luts = luts_to_process if luts_to_process else self.luts
        target_images = [self.test_images[i] for i in (image_indices or [])]

        if not target_luts or not target_images:
            self.processing_error = "LUT 或测试图未就绪"
            self.screen = "processing"
            return

        api_key = self.api_key or os.environ.get("OPENAI_API_KEY", "")
        total_images = len(target_images)
        total_luts = len(target_luts)
        all_results = []
        all_best_lut = ""
        all_best_reason = ""
        all_images = []

        self.progress_msg = "准备中..."
        self.progress_pct = 0.0
        self.processing_error = ""
        self.processing_done = False

        for img_idx, img_path in enumerate(target_images):
            img_name = os.path.splitext(os.path.basename(img_path))[0]
            img_output = os.path.join(self.output_dir, img_name)
            os.makedirs(img_output, exist_ok=True)

            def progress_cb(msg: str, pct: float):
                # 将多图进度映射到整体进度
                base = (img_idx / total_images) * 100.0
                self.progress_msg = f"[{img_idx+1}/{total_images}] {img_name}: {msg}"
                self.progress_pct = base + (pct / total_images)
                self.draw_processing_screen()

            results, best_lut, best_reason, images = run_pipeline(
                lut_tool_path=self.lut_tool_path,
                test_image_path=img_path,
                output_dir=img_output,
                luts=target_luts,
                api_key=api_key,
                model=self.api_model,
                base_url=self.api_base_url,
                progress_cb=progress_cb,
            )

            self.per_image_results[img_name] = results
            all_results.append((img_name, results, best_lut))
            all_images.extend(images)

            # 自动导出单图结果
            if self.auto_export and results:
                export_results_json(
                    results, best_lut, best_reason,
                    os.path.join(img_output, "eval_result.json"))

            # 取最好的作为整体代表
            if results and (not all_best_lut or results[0].score > all_results[0][1][0].score):
                all_best_lut = best_lut
                all_best_reason = best_reason

        # 合并所有结果 (展平)
        merged = []
        for img_name, results, _ in all_results:
            for r in results:
                r.name = f"{r.name} ({img_name})"
                merged.append(r)
        merged.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(merged):
            r.rank = i + 1

        self.results = merged
        self.best_lut = all_best_lut
        self.best_reason = all_best_reason
        self.watermarked_images = all_images
        self.processing_done = True
        self.processing_error = ""

        if not merged:
            self.processing_error = "处理失败: 请检查日志"

    # ----------------------------------------------------------
    #  事件循环
    # ----------------------------------------------------------

    def handle_main_key(self, key):
        """主屏幕按键处理。"""
        if key == ord('q') or key == ord('Q'):
            self.running = False
        elif key == ord('1'):
            luts_to_process = self._get_filtered_luts()
            selected = list(self.selected_images)
            if luts_to_process and selected:
                self.screen = "processing"
                self.run_full_pipeline(luts_to_process, selected)
                if self.processing_done:
                    if self.results:
                        self.screen = "results"
                    else:
                        self.screen = "main"
                else:
                    self.screen = "main"
            else:
                if not selected:
                    self.run_scan()
        elif key == ord('2'):
            self.screen = "settings"
        elif key == ord('3'):
            self.export_json()
        elif key == ord('r') or key == ord('R'):
            self.run_scan()
        elif key == ord('f') or key == ord('F'):
            new_filter = self._edit_field("正则筛选 (如: sepia|blue|mono)", self.lut_filter, 50)
            if new_filter is not None:
                self.lut_filter = new_filter
        elif key == ord('c') or key == ord('C'):
            self.lut_filter = ""
        elif key == ord('h') or key == ord('H'):
            self.draw_help_screen()
        elif key == ord(' '):  # SPACE — 切换当前图像选中
            # 找到主屏幕上的第一个未选中图像来切换
            for i in range(len(self.test_images)):
                if i in self.selected_images and len(self.selected_images) > 1:
                    self.selected_images.discard(i)
                    break
                elif i not in self.selected_images:
                    self.selected_images.add(i)
                    break
        elif key == ord('a') or key == ord('A'):
            self.selected_images = set(range(len(self.test_images)))
        elif key == ord('n') or key == ord('N'):
            self.selected_images = set()

    def _get_filtered_luts(self) -> List:
        """返回当前筛选后的 LUT 列表。"""
        if not self.lut_filter:
            return self.luts
        try:
            pat = re.compile(self.lut_filter, re.IGNORECASE)
            return [l for l in self.luts if pat.search(l.name)]
        except re.error:
            return self.luts

    def handle_settings_key(self, key):
        """设置屏幕按键处理。"""
        if key == 27:  # ESC
            self.screen = "main"
        elif key == ord('q') or key == ord('Q'):
            self.running = False
        elif key == ord('x') or key == ord('X'):
            self.running = False
        elif key == ord('r') or key == ord('R'):
            # 重置为环境变量
            self.api_key, self.api_base_url, self.api_model = get_api_config()
        elif ord('1') <= key <= ord('6'):
            idx = key - ord('1')
            fields = [
                ("API Key", self.api_key),
                ("API Base URL", self.api_base_url),
                ("Model", self.api_model),
                ("LUT 目录", self.lut_dir),
                ("输出目录", self.output_dir),
                ("自动导出", "y" if self.auto_export else "n"),
            ]
            if idx < len(fields):
                label, val = fields[idx]
                if idx == 5:  # 自动导出 — 开关
                    self.auto_export = not self.auto_export
                else:
                    hints = ["sk-...", "https://...", "model name", "./luts", "./results", ""]
                    new_val = self._edit_field(label, str(val), 50)
                    if new_val is not None:
                        if idx == 0:
                            self.api_key = new_val
                        elif idx == 1:
                            self.api_base_url = new_val
                        elif idx == 2:
                            self.api_model = new_val
                        elif idx == 3:
                            self.lut_dir = new_val
                        elif idx == 4:
                            self.output_dir = new_val

    def handle_processing_key(self, key):
        """处理屏幕按键处理。"""
        if key == ord('q') or key == ord('Q'):
            if not self.processing_done:
                # 无法真正取消，标记后返回
                pass
            self.screen = "main"
        if key == 27:  # ESC
            self.screen = "main"

    def handle_results_key(self, key):
        """结果屏幕按键处理。"""
        if key == 27:
            self.screen = "main"
        elif key == ord('s') or key == ord('S'):
            self.screen = "query"
        elif key == ord('e') or key == ord('E'):
            self.export_json()
        elif key == ord('m') or key == ord('M'):
            if len(self.per_image_results) > 1:
                self.draw_merge_screen()
        elif key == ord('r') or key == ord('R'):
            if self.luts and len(self.selected_images):
                self.processing_done = False
                self.screen = "processing"
                luts = self._get_filtered_luts()
                self.run_full_pipeline(luts, list(self.selected_images))
                if self.processing_done:
                    self.screen = "results" if self.results else "main"
                else:
                    self.screen = "main"
        elif ord('1') <= key <= ord('9'):
            idx = key - ord('1')
            if idx < len(self.results):
                self.show_lut_detail(idx)

    def draw_query_screen(self):
        """自然语言查询界面 — 支持 AI 实时流式输出。"""
        self.stdscr.clear()
        self.draw_header("  💬 风格查询  ")

        y = 3
        try:
            self.stdscr.addstr(y, 2, "输入风格描述，AI 将匹配最合适的 LUT:",
                               curses.A_BOLD)
            y += 2
            self.stdscr.addstr(y, 4, "例如: 德味 / 复古胶片 / 黑白 / 电影感 / 日系小清新",
                               curses.color_pair(C_MUTED))
            y += 2

            # 输入框
            box_h, box_w = 3, min(60, self.w - 8)
            box_x = (self.w - box_w) // 2
            try:
                self.stdscr.addstr(y, box_x, "┌" + "─" * (box_w - 2) + "┐")
                y += 1
                self.stdscr.addstr(y, box_x, "│" + " " * (box_w - 2) + "│")
                y += 1
                self.stdscr.addstr(y, box_x, "└" + "─" * (box_w - 2) + "┘")
            except curses.error:
                try:
                    self.stdscr.addstr(y, box_x, "+" + "-" * (box_w - 2) + "+")
                    y += 1
                    self.stdscr.addstr(y, box_x, "|" + " " * (box_w - 2) + "|")
                    y += 1
                    self.stdscr.addstr(y, box_x, "+" + "-" * (box_w - 2) + "+")
                except curses.error:
                    y += 2
            input_y = y - 2
            input_x = box_x + 1

            curses.curs_set(1)
            self.stdscr.refresh()

            import curses.textpad
            edit_win = curses.newwin(1, box_w - 2, input_y, input_x)
            edit_box = curses.textpad.Textbox(edit_win)
            edit_win.refresh()
            query = edit_box.edit().strip()

            curses.curs_set(0)

            if query:
                # 创建流式输出窗口（优先用 TUI 内配置）
                api_key = self.api_key or os.environ.get("OPENAI_API_KEY", "")
                base_url = self.api_base_url
                model = self.api_model
                stream_h = self.h - y - 6
                stream_w = self.w - 6
                stream_y = y + 2
                stream_x = 2
                stream_win = curses.newwin(stream_h, stream_w,
                                           stream_y, stream_x)
                stream_win.bkgd(' ', curses.A_NORMAL)
                stream_win.erase()
                stream_win.box()
                stream_win.addstr(0, 2, " 🤖 AI 实时响应 ",
                                  curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
                stream_win.refresh()

                # 流式缓冲区
                buf = ""
                display_buf = ""

                def on_chunk(chunk: str):
                    nonlocal buf, display_buf
                    buf += chunk
                    display_buf += chunk
                    # 每收到一块就刷新一次窗口
                    try:
                        stream_win.erase()
                        stream_win.box()
                        stream_win.addstr(0, 2, " 🤖 AI 实时响应 ",
                                          curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
                        lines = display_buf.split("\n")
                        max_rows = stream_h - 2
                        # 只显示最后 max_rows 行
                        for i, line in enumerate(lines[-max_rows:]):
                            if i + 1 < stream_h:
                                stream_win.addstr(i + 1, 2,
                                                  line[:stream_w - 4])
                        stream_win.refresh()
                    except curses.error:
                        pass

                # 执行流式查询
                matches = match_query(
                    self.results, query, api_key, model, base_url,
                    stream_callback=on_chunk)

                # 查询完成 → 展示最终结果
                self.stdscr.clear()
                self.draw_header("  💬 查询结果  ")

                result_y = 3
                if not matches:
                    try:
                        self.stdscr.addstr(result_y, 2, "未找到匹配的 LUT",
                                           curses.color_pair(C_ERROR))
                    except curses.error:
                        pass
                else:
                    medals = ["🥇", "🥈", "🥉"]
                    for i, m in enumerate(matches[:5]):
                        medal = medals[i] if i < 3 else f"  {i+1}."
                        name = m.get("name", "?")
                        rel = m.get("relevance", 0)
                        reason = m.get("reason", "")
                        bar = "█" * int(rel / 10) + "░" * (10 - int(rel / 10))
                        try:
                            self.stdscr.addstr(result_y, 2,
                                               f" {medal} [{name}] {rel:.0f}% {bar}",
                                               curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
                        except curses.error:
                            pass
                        result_y += 1
                        if reason:
                            for line in [reason[i:i+self.w-8]
                                         for i in range(0, len(reason), self.w-8)]:
                                try:
                                    self.stdscr.addstr(result_y, 6, line,
                                                       curses.color_pair(C_OK))
                                except curses.error:
                                    pass
                                result_y += 1
                        result_y += 1

                self.draw_footer([
                    ("R", "再查一次"),
                    ("ESC", "返回结果"),
                    ("Q", "退出"),
                ])
                self.stdscr.refresh()

                while True:
                    k = self.stdscr.getch()
                    if k == ord('r') or k == ord('R'):
                        self.screen = "query"
                        return
                    elif k == 27:
                        self.screen = "results"
                        return
                    elif k == ord('q') or k == ord('Q'):
                        self.running = False
                        return
            else:
                self.screen = "results"

        except curses.error:
            curses.curs_set(0)
            self.screen = "results"

    def handle_query_key(self, key):
        """查询屏幕按键处理。"""
        if key == 27:  # ESC
            self.screen = "results"
        elif key == ord('x') or key == ord('X'):
            self.running = False

    def draw_merge_screen(self):
        """AI 多图整合分析界面 — 实时流式输出。"""
        if len(self.per_image_results) < 2:
            self.screen = "results"
            return

        self.stdscr.clear()
        self.draw_header("  🔗 多图整合分析  ")
        y = 2

        images_info = list(self.per_image_results.items())
        try:
            self.stdscr.addstr(y, 2,
                f"整合 {len(images_info)} 张图 × {len(images_info[0][1])} LUT 的结果...",
                curses.A_BOLD)
            y += 1
            for img_name, results in images_info:
                best = results[0].name if results else "N/A"
                try:
                    self.stdscr.addstr(y, 4, f"{img_name}: {len(results)} LUT "
                                             f"最佳={best}",
                                       curses.color_pair(C_MUTED))
                except curses.error:
                    pass
                y += 1
        except curses.error:
            pass
        y += 1

        api_key = self.api_key or os.environ.get("OPENAI_API_KEY", "")
        base_url = self.api_base_url
        model = self.api_model
        win_h = min(self.h - y - 4, 8)
        win_w = self.w - 6
        win_y = y
        win_x = 2

        win = curses.newwin(max(win_h, 3), win_w, win_y, win_x)
        win.bkgd(' ', curses.A_NORMAL)
        win.erase()
        try:
            win.box()
            win.addstr(0, 2, " 🤖 AI 整合分析中... ",
                       curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
        except curses.error:
            pass
        win.refresh()

        display_buf = ""
        def on_chunk(chunk):
            nonlocal display_buf
            display_buf += chunk
            try:
                win.erase()
                win.box()
                win.addstr(0, 2, " 🤖 AI 整合分析中... ",
                           curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
                lines = display_buf.split("\n")
                for i, line in enumerate(lines[-(win_h - 2):]):
                    win.addstr(i + 1, 2, line[:win_w - 4])
                win.refresh()
            except curses.error:
                pass

        result = merge_results(
            self.per_image_results, api_key, model, base_url,
            stream_callback=on_chunk)

        # 最终结果
        self.stdscr.clear()
        self.draw_header("  🔗 整合分析报告  ")
        ry = 3
        try:
            best = result.get("overall_best_lut", "")
            reason = result.get("overall_best_reason", "")
            if best:
                self.stdscr.addstr(ry, 2, f"🏆 综合最佳: {best}",
                                   curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
                ry += 1
                if reason:
                    for line in [reason[j:j+self.w-8]
                                 for j in range(0, len(reason), self.w-8)]:
                        self.stdscr.addstr(ry, 4, line, curses.color_pair(C_OK))
                        ry += 1
            ry += 1

            rankings = result.get("rankings", [])
            if rankings:
                self.stdscr.addstr(ry, 2, "综合排名:", curses.A_BOLD)
                ry += 1
                medals = ["🥇", "🥈", "🥉"]
                for i, r in enumerate(rankings[:8]):
                    medal = medals[i] if i < 3 else f"  {i+1}."
                    name = r.get("name", "?")
                    score = r.get("score", 0)
                    self.stdscr.addstr(ry, 4, f"{medal} [{name}] {score:.0f}/100")
                    ry += 1
            ry += 1

            summary = result.get("style_summary", "")
            if summary and not summary.startswith("本地"):
                self.stdscr.addstr(ry, 2, "风格总结:", curses.A_BOLD)
                ry += 1
                for line in [summary[j:j+self.w-8]
                             for j in range(0, len(summary), self.w-8)]:
                    self.stdscr.addstr(ry, 4, line, curses.color_pair(C_MUTED))
                    ry += 1
        except curses.error:
            pass

        self.draw_footer([
            ("E", "导出 JSON"),
            ("ESC", "返回结果"),
        ])
        self.stdscr.refresh()

        while True:
            k = self.stdscr.getch()
            if k == 27:
                self.screen = "results"
                return
            elif k == ord('e') or k == ord('E'):
                out = os.path.join(self.output_dir, "merge_result.json")
                with open(out, 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)

    def handle_key(self, key):
        """主分发。"""
        if self.screen == "main":
            self.handle_main_key(key)
        elif self.screen == "settings":
            self.handle_settings_key(key)
        elif self.screen == "processing":
            self.handle_processing_key(key)
        elif self.screen == "results":
            self.handle_results_key(key)
        elif self.screen == "query":
            self.handle_query_key(key)

    # ----------------------------------------------------------
    #  导出
    # ----------------------------------------------------------

    def export_json(self, custom_path=None):
        """导出结果为 JSON。若已自动导出且用户选择手动，可自定义路径。"""
        if not self.results:
            return
        out_path = custom_path or self.export_path or \
                   os.path.join(self.output_dir, "eval_result.json")
        export_results_json(self.results, self.best_lut,
                            self.best_reason, out_path)
        self.export_path = out_path

    # ----------------------------------------------------------
    #  主循环
    # ----------------------------------------------------------

    def run(self):
        """主事件循环。"""
        self.setup_colors()
        curses.curs_set(0)   # 隐藏光标
        self.stdscr.nodelay(0)
        self.stdscr.keypad(1)

        # 首次扫描
        self.run_scan()

        while self.running:
            curses.update_lines_cols()
            self.w = curses.COLS
            self.h = curses.LINES

            if self.screen == "main":
                self.draw_main_screen()
            elif self.screen == "settings":
                self.draw_settings_screen()
            elif self.screen == "processing":
                self.draw_processing_screen()
            elif self.screen == "results":
                self.draw_results_screen()
            elif self.screen == "query":
                self.draw_query_screen()

            key = self.stdscr.getch()
            self.handle_key(key)

        return self.results


# ============================================================
#  CLI 模式
# ============================================================

def run_cli(args: argparse.Namespace) -> int:
    """CLI 模式入口 — 支持多图。"""
    if not HAS_ENGINE:
        print("错误: engine.py 未找到")
        return 1

    output_dir = args.output or "./results"
    os.makedirs(output_dir, exist_ok=True)

    # LUT
    luts = discover_luts(args.lut_dir or ".")
    if not luts:
        print("未发现 .cube 文件")
        return 1
    print(f"发现 {len(luts)} 个 LUT")

    # 测试图 — 多图支持
    test_images = []
    if args.image:
        for img in args.image:
            if os.path.isdir(img):
                test_images.extend(discover_all_images([img]))
            else:
                test_images.append(os.path.abspath(img))
    if not test_images:
        test_images = discover_all_images(["."])
    if not test_images:
        print("未发现测试图，请用 --image 指定")
        return 1
    print(f"测试图: {len(test_images)} 张")
    for img in test_images:
        print(f"  · {os.path.basename(img)}")

    # API
    api_key, base_url, model = get_api_config()
    if args.api_key:
        api_key = args.api_key
    if args.model:
        model = args.model
    if args.base_url:
        base_url = args.base_url

    # 逐图处理
    all_merged = []
    for img_idx, img_path in enumerate(test_images):
        img_name = os.path.splitext(os.path.basename(img_path))[0]
        img_output = os.path.join(output_dir, img_name)
        os.makedirs(img_output, exist_ok=True)
        print(f"\n--- [{img_idx+1}/{len(test_images)}] {img_name} ---")

        results, best_lut, best_reason, images = run_pipeline(
            lut_tool_path=args.lut_tool or "./lut_tool",
            test_image_path=img_path,
            output_dir=img_output,
            luts=luts,
            api_key=api_key,
            model=model,
            base_url=base_url,
        )

        print(format_result_summary(results, best_lut, best_reason))
        if args.json:
            export_results_json(results, best_lut, best_reason,
                               os.path.join(img_output, "eval_result.json"))
            print(f"  已导出: {img_output}/eval_result.json")

        # 合并
        for r in results:
            r.name = f"{r.name} ({img_name})"
            all_merged.append(r)

        # 对每一张图分别支持查询
        if (args.query or args.interactive) and results:
            q = args.query
            if q:
                print(format_query_result(
                    match_query(results, q, api_key, model, base_url), q))

    # 交互式查询（最后一张图的结果）
    if args.interactive and all_merged:
        print("\n" + "=" * 60)
        print("  💬 自然语言风格查询模式 (🔄 实时流式输出)")
        print("  输入空行或 q 退出")
        print("=" * 60)
        while True:
            try:
                q = input("\n  ▶ ").strip()
                if not q or q.lower() == 'q':
                    break
                print()
                print("  AI 思考中...", end="", flush=True)

                collected = []
                def on_chunk(chunk):
                    collected.append(chunk)
                    print(chunk, end="", flush=True)

                matches = match_query(
                    results, q, api_key, model, base_url,
                    stream_callback=on_chunk)

                print("\n")
                if matches:
                    print(format_query_result(matches, q))
            except (EOFError, KeyboardInterrupt):
                break

    return 0


# ============================================================
#  主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="LUT 风格评估工具 — 兼容所有 OpenAI API 格式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""环境变量:
  OPENAI_API_KEY    API 密钥
  OPENAI_BASE_URL   API 地址 (默认 https://api.openai.com/v1)
  OPENAI_MODEL      模型名 (默认 gpt-4o)

示例:
  python app.py                                           # TUI 模式
  python app.py --cli -i photo.jpg                        # 单图评估
  python app.py --cli -i a.jpg -i b.jpg                   # 多图评估
  python app.py --cli -i test_images/ -q "德味"           # 目录+查询
  python app.py --cli -i test_images/ -I                  # 目录+交互查询
  OPENAI_API_KEY=sk-xxx python app.py                     # 传 API Key
""")
    parser.add_argument("--cli", action="store_true",
                       help="CLI 模式 (默认 TUI)")
    parser.add_argument("--image", "-i", action="append", default=None,
                       help="测试图路径/目录（可多次使用）")
    parser.add_argument("--lut-dir", "-d", default=".",
                       help="LUT 目录")
    parser.add_argument("--output", "-o", default="./results",
                       help="输出目录")
    parser.add_argument("--api-key", "-k", default=None,
                       help="API Key (默认取 OPENAI_API_KEY)")
    parser.add_argument("--base-url", default=None,
                       help="API 地址 (默认取 OPENAI_BASE_URL)")
    parser.add_argument("--model", "-m", default=None,
                       help="模型名 (默认取 OPENAI_MODEL)")
    parser.add_argument("--lut-tool", default="./lut_tool",
                       help="C 工具路径")
    parser.add_argument("--json", action="store_true",
                       help="导出 JSON")
    parser.add_argument("--query", "-q", default=None,
                       help="自然语言风格查询，如: '德味 复古' / '黑白电影感'")
    parser.add_argument("--interactive", "-I", action="store_true",
                       help="评估后进入交互式风格查询模式")
    args = parser.parse_args()

    # CLI 模式
    if args.cli or not sys.stdout.isatty():
        return run_cli(args)

    # TUI 模式 (curses)
    try:
        results = curses.wrapper(lambda stdscr: LutTUI(stdscr).run())
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        print(f"TUI 错误: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
