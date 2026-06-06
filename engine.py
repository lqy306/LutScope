"""
engine.py — LUT 评估核心引擎
================================
提供: LUT 发现、图像处理、色彩统计、AI 评估

兼容所有 OpenAI API 格式的提供商 (OpenAI / DeepSeek / Groq / Together 等)

环境变量:
    OPENAI_API_KEY    — API 密钥 (必需)
    OPENAI_BASE_URL   — API 地址 (默认 https://api.openai.com/v1)
    OPENAI_MODEL      — 模型名 (默认 gpt-4o)
"""

import os
import re
import sys
import glob
import json
import math
import zipfile
import base64
import struct
import tempfile
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any, Callable
from dataclasses import dataclass, asdict, field

# ============================================================
#  可选依赖
# ============================================================
try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ============================================================
#  数据结构
# ============================================================

@dataclass
class LutEntry:
    """单个 LUT 条目"""
    name: str            # 显示名称（不含路径和扩展名）
    source: str          # 源标识
    cube_path: str       # 实际 .cube 文件路径
    from_zip: bool = False
    zip_path: str = ""

@dataclass
class EvalResult:
    """AI 评估结果"""
    name: str
    score: float
    style_tags: List[str]
    description: str
    analysis: str
    rank: int = 0

@dataclass
class ColorStats:
    """图像色彩统计"""
    name: str                    # LUT 名称
    avg_r: float                 # 平均 R
    avg_g: float
    avg_b: float
    avg_h: float                 # 平均色相 (0-360)
    avg_s: float                 # 平均饱和度 (0-100)
    avg_v: float                 # 平均明度 (0-100)
    std_s: float                 # 饱和度标准差
    std_v: float                 # 明度标准差
    contrast: float              # 对比度 (亮度标准差)
    hist_r: List[int]            # R 通道直方图 (8 bins)
    hist_g: List[int]
    hist_b: List[int]
    dominant_colors: List[str]   # 主色调 (#RRGGBB)
    warm_cool_bias: float        # >0 偏暖, <0 偏冷

# ============================================================
#  配置 — OpenAI 兼容 API
# ============================================================

# 默认使用 OpenAI，用户可通过 OPENAI_BASE_URL 切换到其他提供商
DEFAULT_API_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o"

def get_api_config() -> Tuple[str, str, str]:
    """从环境变量读取 API 配置。
    返回: (api_key, base_url, model)
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", DEFAULT_API_URL).strip()
    model = os.environ.get("OPENAI_MODEL", DEFAULT_MODEL).strip()

    # 兼容旧的 DEEPSEEK_API_KEY
    if not api_key:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()

    # 若 BASE_URL 未设置但用了 DEEPSEEK_API_KEY，自动切换
    if "DEEPSEEK" in os.environ.get("DEEPSEEK_API_KEY", "") or \
       (api_key and base_url == DEFAULT_API_URL and
        os.environ.get("DEEPSEEK_API_KEY", "")):
        # 不改 base_url 了，用户需要显式设置
        pass

    return api_key, base_url.rstrip("/"), model

# ============================================================
#  LUT 发现
# ============================================================

def discover_luts(lut_dir: str = ".") -> List[LutEntry]:
    """
    扫描目录和 zip 压缩包，发现所有 .cube 文件。
    返回 LutEntry 列表。
    """
    entries: List[LutEntry] = []
    seen: set = set()

    def _add(cube_path: str, display_name: str,
             from_zip: bool = False, zip_path: str = ""):
        key = cube_path if not from_zip else f"{zip_path}::{display_name}"
        if key in seen:
            return
        seen.add(key)
        entries.append(LutEntry(
            name=display_name,
            source=key,
            cube_path=cube_path,
            from_zip=from_zip,
            zip_path=zip_path,
        ))

    # 扫描 .cube 文件（递归搜索子目录）
    for f in sorted(glob.glob(os.path.join(lut_dir, "**", "*.cube"), recursive=True)):
        name = os.path.splitext(os.path.basename(f))[0]
        _add(f, name)

    # 扫描 .zip 内的 .cube（递归搜索子目录）
    for zf in sorted(glob.glob(os.path.join(lut_dir, "**", "*.zip"), recursive=True)):
        try:
            with zipfile.ZipFile(zf, 'r') as z:
                for info in z.infolist():
                    if info.filename.endswith('.cube') and \
                       not info.filename.startswith('__') and \
                       not info.filename.startswith('.'):
                        name = os.path.splitext(os.path.basename(info.filename))[0]
                        _add(f"{zf}::{info.filename}", name,
                             from_zip=True, zip_path=zf)
        except zipfile.BadZipFile:
            pass

    return entries

def extract_cube(entry: LutEntry, dest_dir: str) -> Optional[str]:
    """提取 .cube 到临时目录，返回实际路径。"""
    if not entry.from_zip:
        return entry.cube_path

    try:
        with zipfile.ZipFile(entry.zip_path, 'r') as z:
            inner = entry.cube_path.split("::", 1)[1]
            data = z.read(inner)
            out = os.path.join(dest_dir, f"{entry.name}.cube")
            with open(out, 'wb') as f:
                f.write(data)
            return out
    except Exception:
        return None

# ============================================================
#  测试图发现与转换
# ============================================================

def discover_test_image(image_dir: str = ".") -> Optional[str]:
    """在目录中查找测试图。"""
    for ext in ["*.ppm", "*.png", "*.jpg", "*.jpeg", "*.tif", "*.tiff", "*.bmp"]:
        files = sorted(glob.glob(os.path.join(image_dir, ext)))
        if files:
            return files[0]
    return None

def convert_to_ppm(image_path: str, output_dir: str,
                   max_dim: int = 2048,
                   progress_cb: Optional[Callable] = None) -> Optional[str]:
    """用 PIL 将任意图片转为 PPM。"""
    if not HAS_PIL:
        return None
    try:
        img = Image.open(image_path).convert("RGB")
        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        out = os.path.join(output_dir, "_test_image.ppm")
        img.save(out, "PPM")
        if progress_cb:
            progress_cb(f"图像已转换: {img.size[0]}x{img.size[1]}")
        return out
    except Exception as e:
        if progress_cb:
            progress_cb(f"图像转换失败: {e}")
        return None

# ============================================================
#  运行 C 工具
# ============================================================

def run_lut_tool(lut_tool_path: str,
                 input_ppm: str,
                 cube_path: str,
                 output_ppm: str,
                 timeout: int = 120) -> Tuple[bool, str]:
    """运行 C lut_tool，返回 (成功?, 消息)。"""
    try:
        ret = subprocess.run(
            [lut_tool_path, input_ppm, cube_path, output_ppm],
            capture_output=True, text=True, timeout=timeout)
        if ret.returncode == 0:
            return True, ret.stdout.strip()
        else:
            return False, ret.stderr.strip()
    except FileNotFoundError:
        return False, f"找不到 '{lut_tool_path}'，请先 make 编译"
    except subprocess.TimeoutExpired:
        return False, "处理超时"
    except Exception as e:
        return False, str(e)

# ============================================================
#  水印绘制 (PIL)
# ============================================================

def add_watermark(ppm_path: str, lut_name: str, output_png_path: str) -> bool:
    """给 PPM 添加水印文字，输出 PNG。"""
    if not HAS_PIL:
        return False

    try:
        img = Image.open(ppm_path).convert("RGB")
        draw = ImageDraw.Draw(img)
        w, h = img.size

        # 字体
        font_size = max(14, min(32, w // 45))
        font = _find_font(font_size)
        if font is None:
            font = ImageFont.load_default()

        text = f"LUT: {lut_name}"

        # 估算文本尺寸
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0] + 16
            th = bbox[3] - bbox[1] + 10
        except Exception:
            tw = font_size * len(text) + 16
            th = font_size + 10

        margin = 12

        # 半透明底色条 + 彩色顶条
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        bx0, by0 = margin, margin
        bx1, by1 = bx0 + tw, by0 + th
        odraw.rectangle([bx0, by0, bx1, by1], fill=(0, 0, 0, 160))
        odraw.rectangle([bx0, by0, bx1, by0 + 5], fill=(255, 190, 50, 220))
        img = Image.alpha_composite(img.convert("RGBA"), overlay)
        draw = ImageDraw.Draw(img)
        draw.text((bx0 + 8, by0 + 6), text, fill=(255, 255, 255), font=font)

        # 右下角小标签
        try:
            sub = "AI Evaluation"
            sb = draw.textbbox((0, 0), sub, font=font)
            sw = sb[2] - sb[0]
            sh = sb[3] - sb[1]
            sx, sy = w - margin - sw - 6, h - margin - sh - 4
            odraw2 = ImageDraw.Draw(Image.new("RGBA", img.size, (0, 0, 0, 0)))
            odraw2.rectangle([sx - 4, sy - 2, sx + sw + 8, sy + sh + 4],
                             fill=(0, 0, 0, 128))
            img = Image.alpha_composite(img, odraw2)
            draw = ImageDraw.Draw(img)
            draw.text((sx + 2, sy), sub, fill=(200, 200, 200), font=font)
        except Exception:
            pass

        img = img.convert("RGB")
        img.save(output_png_path, "PNG")
        return True
    except Exception:
        return False

def _find_font(size: int) -> Optional[Any]:
    """尝试查找系统字体。"""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return None

# ============================================================
#  色彩统计提取
# ============================================================

def extract_color_stats(image_path: str, name: str = "") -> Optional[ColorStats]:
    """
    从图像中提取全面的色彩统计数据。
    不依赖 AI 视觉，纯数学统计。
    """
    if not HAS_PIL:
        return None

    try:
        img = Image.open(image_path).convert("RGB")
        pixels = list(img.getdata())
        n = len(pixels)
        if n == 0:
            return None

        r_vals = [p[0] for p in pixels]
        g_vals = [p[1] for p in pixels]
        b_vals = [p[2] for p in pixels]

        avg_r = sum(r_vals) / n
        avg_g = sum(g_vals) / n
        avg_b = sum(b_vals) / n

        # HSV 并计算饱和度/明度的方差
        h_sum = s_sum = v_sum = 0.0
        s2_sum = v2_sum = 0.0
        for pr, pg, pb in pixels:
            mx = max(pr, pg, pb)
            mn = min(pr, pg, pb)
            v = mx / 255.0 * 100.0
            s = ((mx - mn) / mx * 100.0) if mx > 0 else 0.0

            h = 0.0
            if mx != mn:
                if mx == pr:
                    h = 60.0 * ((pg - pb) / (mx - mn))
                elif mx == pg:
                    h = 60.0 * (2.0 + (pb - pr) / (mx - mn))
                else:
                    h = 60.0 * (4.0 + (pr - pg) / (mx - mn))
            if h < 0:
                h += 360.0

            h_sum += h
            s_sum += s
            v_sum += v
            s2_sum += s * s
            v2_sum += v * v

        avg_h = h_sum / n
        avg_s = s_sum / n
        avg_v = v_sum / n
        std_s = math.sqrt(max(0, s2_sum / n - avg_s * avg_s))
        std_v = math.sqrt(max(0, v2_sum / n - avg_v * avg_v))

        # 亮度标准差作为对比度指标
        lum_vals = [0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in pixels]
        avg_lum = sum(lum_vals) / n
        contrast = math.sqrt(sum((x - avg_lum) ** 2 for x in lum_vals) / n)

        # 直方图 (8 bins 每通道)
        bins = 8
        hist_r = [0] * bins
        hist_g = [0] * bins
        hist_b = [0] * bins
        step = 256 // bins
        for pr, pg, pb in pixels:
            hist_r[min(pr // step, bins - 1)] += 1
            hist_g[min(pg // step, bins - 1)] += 1
            hist_b[min(pb // step, bins - 1)] += 1

        # 主色调 (简单量化取 top-5)
        color_counts: Dict[int, int] = {}
        for pr, pg, pb in pixels:
            # 量化到 4x4x4 共 64 色
            qr, qg, qb = pr // 64, pg // 64, pb // 64
            key = (qr << 8) | (qg << 4) | qb
            color_counts[key] = color_counts.get(key, 0) + 1

        top_colors = sorted(color_counts.items(), key=lambda x: -x[1])[:5]
        dominant_colors = []
        for key, _ in top_colors:
            qr = (key >> 8) & 0xF
            qg = (key >> 4) & 0xF
            qb = key & 0xF
            r = qr * 64 + 32
            g = qg * 64 + 32
            b = qb * 64 + 32
            dominant_colors.append(f"#{r:02X}{g:02X}{b:02X}")

        # 暖冷偏差: R 与 B 的差值, 加上饱和度权重
        warm_cool_bias = (avg_r - avg_b) * (1.0 + avg_s / 200.0)

        return ColorStats(
            name=name,
            avg_r=avg_r, avg_g=avg_g, avg_b=avg_b,
            avg_h=avg_h, avg_s=avg_s, avg_v=avg_v,
            std_s=std_s, std_v=std_v,
            contrast=contrast,
            hist_r=hist_r, hist_g=hist_g, hist_b=hist_b,
            dominant_colors=dominant_colors,
            warm_cool_bias=warm_cool_bias,
        )
    except Exception:
        return None

def stats_diff(base: ColorStats, stats: ColorStats) -> Dict[str, Any]:
    """计算 LUT 应用后的色彩变化量。"""
    return {
        "delta_r": stats.avg_r - base.avg_r,
        "delta_g": stats.avg_g - base.avg_g,
        "delta_b": stats.avg_b - base.avg_b,
        "delta_h": stats.avg_h - base.avg_h,
        "delta_s": stats.avg_s - base.avg_s,
        "delta_v": stats.avg_v - base.avg_v,
        "delta_contrast": stats.contrast - base.contrast,
        "warm_cool_bias": stats.warm_cool_bias,
    }

def stats_to_text(stats: ColorStats, diff: Optional[Dict] = None) -> str:
    """将色彩统计格式化为文本描述。"""
    lines = []
    lines.append(f"  平均颜色: RGB({stats.avg_r:.0f}, {stats.avg_g:.0f}, {stats.avg_b:.0f})")
    lines.append(f"  色相: {stats.avg_h:.1f}°, 饱和度: {stats.avg_s:.1f}%, 明度: {stats.avg_v:.1f}%")
    lines.append(f"  饱和度方差: {stats.std_s:.1f}, 明度方差: {stats.std_v:.1f}")
    lines.append(f"  对比度: {stats.contrast:.1f}")
    lines.append(f"  暖冷偏差: {stats.warm_cool_bias:.1f} " +
                 ("(偏暖)" if stats.warm_cool_bias > 5 else
                  "(偏冷)" if stats.warm_cool_bias < -5 else "(中性)"))
    lines.append(f"  主色调: {' '.join(stats.dominant_colors[:3])}")
    lines.append(f"  直方图 R: {_hist_str(stats.hist_r)}")
    lines.append(f"  直方图 G: {_hist_str(stats.hist_g)}")
    lines.append(f"  直方图 B: {_hist_str(stats.hist_b)}")

    if diff:
        dr, dg, db = diff["delta_r"], diff["delta_g"], diff["delta_b"]
        ds = diff["delta_s"]
        lines.append(f"  相较原图变化: R{dr:+.0f} G{dg:+.0f} B{db:+.0f}, 饱和度{ds:+.1f}%")

    return "\n".join(lines)

def _hist_str(h: List[int]) -> str:
    total = sum(h) or 1
    return " ".join(f"{'█' * int(c / total * 20):20s}" for c in h)

# ============================================================
#  DeepSeek API 调用
# ============================================================

def check_api_key() -> Tuple[bool, str]:
    """检查 API Key 是否可用。"""
    key, base_url, model = get_api_config()
    if not key:
        return False, "OPENAI_API_KEY 未设置"
    display_url = base_url.replace("https://", "").replace("/v1", "")
    return True, f"{key[:8]}... (→ {display_url}, model: {model})"

def call_llm_api(
    api_key: str,
    messages: List[Dict],
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_API_URL,
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> Optional[str]:
    """调用 OpenAI 兼容的 Chat API。

    支持: OpenAI / DeepSeek / Groq / Together / vLLM / Ollama 等
    """
    if not HAS_REQUESTS:
        return None

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        if resp.status_code == 200:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        else:
            return None
    except Exception:
        return None


def stream_llm_api(
    api_key: str,
    messages: List[Dict],
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_API_URL,
    max_tokens: int = 4096,
    temperature: float = 0.3,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """
    流式调用 OpenAI 兼容的 Chat API。

    通过 on_chunk 回调实时返回每一段增量内容。
    最终返回完整文本。
    """
    if not HAS_REQUESTS:
        return None

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }

    full_text = ""
    try:
        resp = requests.post(
            url, headers=headers, json=payload,
            stream=True, timeout=120)
        if resp.status_code != 200:
            return None

        for line in resp.iter_lines():
            if not line:
                continue
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw.startswith("data: "):
                continue
            data_str = raw[6:]
            if data_str == "[DONE]":
                break
            try:
                obj = json.loads(data_str)
                delta = obj.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    full_text += content
                    if on_chunk:
                        on_chunk(content)
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

        return full_text
    except Exception:
        return None


# ============================================================
#  AI 评估 — 基于色彩统计 (无需视觉)
# ============================================================

def build_eval_prompt(lut_stats_list: List[Tuple[str, str]]) -> str:
    """
    构建评估提示词。
    lut_stats_list: [(LUT名称, 色彩统计文本), ...]
    """
    items = []
    for i, (name, stats_text) in enumerate(lut_stats_list, 1):
        items.append(f"[LUT {i}]\n名称: {name}\n{stats_text}")

    lut_list_text = "\n\n".join(items)

    return f"""你是一位专业影视调色师和色彩分析专家。下面列出了多个 LUT (Look-Up Table) 应用于同一张测试图后的色彩统计数据。

请根据这些色彩数据，分析每张 LUT 的风格特点并给出排名。

分析维度:
1. 色彩风格 — 暖/冷/中性调, 饱和度高低, 色彩倾向
2. 对比度 — 明暗层次, 柔和/强烈
3. 整体氛围 — 复古/现代/电影感/清新/暗黑等
4. 适配度 — 针对该图的适用性

---

{lut_list_text}

---

请严格按以下 JSON 格式输出（不要包含额外说明）：

```json
{{
  "rankings": [
    {{
      "name": "LUT名称",
      "rank": 1,
      "score": 92,
      "style_tags": ["暖调", "复古", "电影感"],
      "description": "一句概括此 LUT 的风格",
      "analysis": "约 100-200 字的详细专业分析，基于色彩数据解读"
    }}
  ],
  "best_lut": "最优LUT名称",
  "best_reason": "为什么这个 LUT 最适合该测试图"
}}
```

要求:
- ranking 按排名从高到低排列 (rank 1 为最佳)
- score 范围 0-100
- style_tags 给出 3-6 个中文标签
- 输出严格 JSON，不要包含 JSON 之外的文字"""

def parse_eval_json(text: str) -> Dict:
    """从 LLM 返回中解析 JSON。"""
    if not text:
        return {"rankings": [], "best_lut": "", "best_reason": ""}

    # 尝试 ```json ... ```
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 找第一个大括号
    m = re.search(r'(\{.*\})', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    return {"rankings": [], "best_lut": "", "best_reason": ""}

# ============================================================
#  本地评估 (无 API 回退)
# ============================================================

def local_evaluate(
    base_stats: ColorStats,
    stats_list: List[Tuple[str, ColorStats, str]],
) -> List[EvalResult]:
    """
    无 AI API 时，基于色彩统计做本地基础评估。
    stats_list: [(LUT名称, ColorStats, 水印图路径), ...]
    """
    results = []
    for name, stats, _ in stats_list:
        diff = stats_diff(base_stats, stats)

        # 风格强度综合分
        intensity = (
            abs(diff["delta_s"]) * 0.3 +
            abs(diff["delta_contrast"]) * 0.25 +
            abs(diff["warm_cool_bias"]) * 0.2 +
            (abs(diff["delta_r"]) + abs(diff["delta_g"]) + abs(diff["delta_b"])) * 0.25
        )
        score = min(100, max(30, 50 + intensity))

        # 标签推导
        tags = []
        if stats.warm_cool_bias > 10:
            tags.append("暖调")
        elif stats.warm_cool_bias < -10:
            tags.append("冷调")
        else:
            tags.append("中性")

        if stats.avg_s > 45:
            tags.append("高饱和")
        elif stats.avg_s < 25:
            tags.append("低饱和")

        if stats.contrast > 60:
            tags.append("高对比")
        elif stats.contrast < 35:
            tags.append("柔和")

        if abs(diff["delta_h"]) > 20:
            tags.append("色调偏移")
        if intensity > 30:
            tags.append("强风格")

        if not tags:
            tags.append("自然风格")

        desc = f"平均色({stats.avg_r:.0f},{stats.avg_g:.0f},{stats.avg_b:.0f}), " \
               f"饱和度{stats.avg_s:.0f}%, 对比度{stats.contrast:.1f}"

        results.append(EvalResult(
            name=name, score=score,
            style_tags=tags, description=desc,
            analysis=f"本地分析 — 饱和度变化: {diff['delta_s']:+.1f}%, "
                     f"对比度变化: {diff['delta_contrast']:+.1f}, "
                     f"暖冷偏差: {diff['warm_cool_bias']:.1f}",
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    for i, r in enumerate(results):
        r.rank = i + 1
    return results

# ============================================================
#  AI 评估入口
# ============================================================

def evaluate_with_api(
    api_key: str,
    stats_list: List[Tuple[str, ColorStats]],
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_API_URL,
) -> Tuple[List[EvalResult], str, str]:
    """
    使用 DeepSeek API 评估所有 LUT 效果。
    返回: (EvalResult列表, best_lut, best_reason)
    """
    # 分批发送 (每批最多 6 个)
    all_results: List[EvalResult] = []
    batch_size = 6
    best_lut = ""
    best_reason = ""

    for i in range(0, len(stats_list), batch_size):
        batch = stats_list[i:i+batch_size]
        items = []
        for name, st in batch:
            items.append((name, stats_to_text(st)))

        prompt = build_eval_prompt(items)
        messages = [
            {"role": "system",
             "content": "你是一位专业影视调色师和色彩分析专家。请根据色彩统计数据分析 LUT 风格。"},
            {"role": "user", "content": prompt},
        ]

        response = call_llm_api(api_key, messages, model, base_url)
        parsed = parse_eval_json(response)

        if parsed.get("rankings"):
            for r in parsed["rankings"]:
                all_results.append(EvalResult(
                    name=r.get("name", ""),
                    score=r.get("score", 50),
                    style_tags=r.get("style_tags", []),
                    description=r.get("description", ""),
                    analysis=r.get("analysis", ""),
                    rank=r.get("rank", 99),
                ))
            if not best_lut and parsed.get("best_lut"):
                best_lut = parsed["best_lut"]
                best_reason = parsed.get("best_reason", "")

    if not all_results:
        return [], "", ""

    # 合并排序
    all_results.sort(key=lambda r: r.rank)
    for i, r in enumerate(all_results):
        r.rank = i + 1
    return all_results, best_lut, best_reason

# ============================================================
#  完整处理管线
# ============================================================

def run_pipeline(
    lut_tool_path: str,
    test_image_path: str,
    output_dir: str,
    luts: List[LutEntry],
    api_key: str = "",
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_API_URL,
    progress_cb: Optional[Callable[[str, float], None]] = None,
) -> Tuple[List[EvalResult], str, str, List[str]]:
    """
    完整处理管线:
    1. 转换测试图为 PPM
    2. 为每个 LUT: 运行 C 工具 + 加水印 + 提取色彩统计
    3. AI 评估
    返回: (results, best_lut, best_reason, watermarked_images)
    """
    temp_dir = tempfile.mkdtemp(prefix="lut_")
    watermarked_images: List[str] = []
    stats_list: List[Tuple[str, ColorStats]] = []
    base_stats: Optional[ColorStats] = None

    try:
        # 1. 转换测试图
        if progress_cb:
            progress_cb("转换测试图...", 0.0)
        ppm_path = convert_to_ppm(test_image_path, output_dir,
                                  progress_cb=lambda m: None)
        if not ppm_path:
            raise RuntimeError("测试图转换失败")

        # 获取原图色彩统计
        base_stats = extract_color_stats(ppm_path, "__original__")

        total = len(luts)
        for idx, lut in enumerate(luts):
            pct = (idx / total) * 80.0  # 0-80% for processing
            if progress_cb:
                progress_cb(f"[{idx+1}/{total}] {lut.name} — 处理中...", pct)

            # 提取 .cube
            cube_path = extract_cube(lut, temp_dir)
            if not cube_path:
                if progress_cb:
                    progress_cb(f"[{idx+1}/{total}] {lut.name} — 提取失败", pct)
                continue

            # 运行 C 工具
            result_ppm = os.path.join(temp_dir, f"{lut.name}_result.ppm")
            ok, msg = run_lut_tool(lut_tool_path, ppm_path, cube_path, result_ppm)
            if not ok:
                if progress_cb:
                    progress_cb(f"[{idx+1}/{total}] {lut.name} — {msg}", pct)
                continue

            if progress_cb:
                progress_cb(f"[{idx+1}/{total}] {lut.name} — 添加水印...",
                            pct + 40.0 / total)

            # 加水印
            watermark_png = os.path.join(output_dir, f"_{lut.name}_watermarked.png")
            if add_watermark(result_ppm, lut.name, watermark_png):
                watermarked_images.append(watermark_png)

                # 提取色彩统计
                stats = extract_color_stats(watermark_png, lut.name)
                if stats:
                    stats_list.append((lut.name, stats))

            if progress_cb:
                progress_cb(f"[{idx+1}/{total}] {lut.name} — 完成 ✅",
                            (idx + 1) / total * 80.0)

        if not stats_list:
            raise RuntimeError("所有 LUT 处理均失败")

        # 3. AI 评估
        if progress_cb:
            progress_cb("AI 评估中...", 85.0)

        has_valid_key, key_or_err = check_api_key()
        if has_valid_key and api_key:
            results, best_lut, best_reason = evaluate_with_api(
                api_key, stats_list, model, base_url)
        else:
            # 本地回退
            if progress_cb:
                progress_cb("API 不可用，使用本地分析...", 85.0)
            base = base_stats or stats_list[0][1]
            local_list = [(name, st, "") for name, st in stats_list]
            results = local_evaluate(base, local_list)
            best_lut = results[0].name if results else ""
            best_reason = "本地统计分析结果"

        if progress_cb:
            progress_cb("评估完成 ✅", 100.0)

        return results, best_lut, best_reason, watermarked_images

    except Exception as e:
        if progress_cb:
            progress_cb(f"错误: {e}", 0)
        return [], "", "", watermarked_images
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

# ============================================================
#  结果格式化
# ============================================================

def format_result_summary(results: List[EvalResult],
                          best_lut: str, best_reason: str) -> str:
    """将结果格式化为可读文本。"""
    lines = []
    lines.append("=" * 60)
    lines.append("  📊 LUT 评估结果")
    lines.append("=" * 60)

    if not results:
        lines.append("  无评估结果")
        return "\n".join(lines)

    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(results):
        medal = medals[i] if i < 3 else f"  {i+1}."
        tags = ", ".join(r.style_tags)
        lines.append(f"\n  {medal} [{r.name}] 评分: {r.score:.0f}/100")
        lines.append(f"     标签: {tags}")
        lines.append(f"     描述: {r.description}")
        if r.analysis:
            short = r.analysis[:120] + ("..." if len(r.analysis) > 120 else "")
            lines.append(f"     分析: {short}")

    lines.append("\n" + "-" * 60)
    lines.append(f"  🏆 最佳 LUT: {best_lut}")
    if best_reason:
        lines.append(f"     原因: {best_reason[:200]}")
    lines.append("=" * 60)
    return "\n".join(lines)

def export_results_json(results: List[EvalResult],
                        best_lut: str, best_reason: str,
                        output_path: str):
    """导出结果为 JSON。"""
    data = {
        "best_lut": best_lut,
        "best_reason": best_reason,
        "rankings": [asdict(r) for r in results],
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
#  多结果 AI 整合
# ============================================================

def build_merge_prompt(
    per_image_results: Dict[str, List[EvalResult]],
) -> str:
    """构建多图整合分析的提示词。"""
    sections = []
    for img_name, results in per_image_results.items():
        items = []
        for r in results[:5]:
            tags = ", ".join(r.style_tags)
            items.append(
                f"  [{r.name}] 评分:{r.score:.0f}  标签:{tags}\n"
                f"    描述: {r.description}\n"
                f"    分析: {r.analysis[:100]}...")
        sections.append(
            f"【图片: {img_name}】\n" + "\n".join(items))

    return f"""你是一位专业的影视调色师和 LUT 专家。下面是对同一组 LUT 在多张不同测试图上的评估结果：

{"\n\n".join(sections)}

请综合分析上述数据，按严格 JSON 格式输出（不要包含额外文字）：

```json
{{
  "overall_best_lut": "综合最佳 LUT 名称",
  "overall_best_reason": "为什么这个 LUT 整体表现最好（100-200字）",
  "per_image_recommendation": {{
    "图片名": ["推荐 LUT1", "推荐 LUT2"]
  }},
  "style_summary": "所有 LUT 的风格总体概括（100-200字）",
  "cross_image_analysis": "哪些 LUT 在不同图片上表现稳定，哪些有特异性",
  "rankings": [
    {{"name": "LUT名称", "score": 综合评分, "reason": "综合理由"}}
  ]
}}
```

要求:
- rankings 按综合表现从高到低排列，评分 0-100
- 基于各图数据做客观综合判断，不要臆测"""


def merge_results_ai(
    per_image_results: Dict[str, List[EvalResult]],
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_API_URL,
    stream_callback: Optional[Callable[[str], None]] = None,
) -> Dict:
    """用 AI 整合多图评估结果。"""
    if not per_image_results:
        return {"rankings": [], "overall_best_lut": ""}

    prompt = build_merge_prompt(per_image_results)
    messages = [
        {"role": "system",
         "content": "你是一名专业的 LUT 调色分析专家。"},
        {"role": "user", "content": prompt},
    ]

    if stream_callback:
        response = stream_llm_api(
            api_key, messages, model, base_url,
            on_chunk=stream_callback)
    else:
        response = call_llm_api(api_key, messages, model, base_url)

    return parse_eval_json(response)


def merge_results_local(
    per_image_results: Dict[str, List[EvalResult]],
) -> Dict:
    """无 API 时本地合并——简单平均评分。"""
    score_map: Dict[str, List[float]] = {}
    for img_name, results in per_image_results.items():
        for r in results:
            if r.name not in score_map:
                score_map[r.name] = []
            score_map[r.name].append(r.score)

    if not score_map:
        return {"rankings": [], "overall_best_lut": ""}

    avg_scores = [(name, sum(scores) / len(scores))
                  for name, scores in score_map.items()]
    avg_scores.sort(key=lambda x: -x[1])

    rankings = []
    for i, (name, score) in enumerate(avg_scores):
        rankings.append({
            "name": name,
            "score": round(score, 1),
            "reason": f"综合 {len(score_map[name])} 张图评分: {score:.1f}",
        })

    return {
        "overall_best_lut": avg_scores[0][0] if avg_scores else "",
        "overall_best_reason": f"综合评分最高: {avg_scores[0][1]:.1f}",
        "rankings": rankings,
        "style_summary": "本地合并模式 — 基于各图评分的简单平均",
        "cross_image_analysis": "",
    }


def merge_results(
    per_image_results: Dict[str, List[EvalResult]],
    api_key: str = "",
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_API_URL,
    stream_callback: Optional[Callable[[str], None]] = None,
) -> Dict:
    """
    多结果整合入口。

    per_image_results: {"图片名": [EvalResult, ...], ...}
    返回: {
        "overall_best_lut": str,
        "overall_best_reason": str,
        "rankings": [{"name": str, "score": float, "reason": str}, ...],
        "style_summary": str,
        "cross_image_analysis": str,
        "per_image_recommendation": dict,
    }
    """
    if not per_image_results:
        return {}

    if api_key and HAS_REQUESTS:
        return merge_results_ai(
            per_image_results, api_key, model, base_url, stream_callback)
    else:
        return merge_results_local(per_image_results)


def format_merge_result(data: Dict) -> str:
    """格式化整合结果。"""
    lines = []
    lines.append("=" * 60)
    lines.append("  🔗 多图整合分析报告")
    lines.append("=" * 60)

    best = data.get("overall_best_lut", "")
    reason = data.get("overall_best_reason", "")
    if best:
        lines.append(f"\n  🏆 综合最佳 LUT: {best}")
        if reason:
            lines.append(f"     理由: {reason[:200]}")

    # 按图片推荐
    per_img = data.get("per_image_recommendation", {})
    if per_img:
        lines.append(f"\n  📋 按图片推荐:")
        for img, recs in per_img.items():
            lines.append(f"    · {img}: {', '.join(recs[:3])}")

    # 风格总结
    summary = data.get("style_summary", "")
    if summary and not summary.startswith("本地"):
        lines.append(f"\n  🎨 风格总结:")
        lines.append(f"     {summary[:200]}")

    # 交叉分析
    cross = data.get("cross_image_analysis", "")
    if cross:
        lines.append(f"\n  🔄 交叉分析:")
        lines.append(f"     {cross[:200]}")

    # 综合排名
    rankings = data.get("rankings", [])
    if rankings:
        lines.append(f"\n  📊 综合排名:")
        medals = ["🥇", "🥈", "🥉"]
        for i, r in enumerate(rankings[:8]):
            medal = medals[i] if i < 3 else f"  {i+1}."
            name = r.get("name", "?")
            score = r.get("score", 0)
            reason = r.get("reason", "")
            lines.append(f"    {medal} [{name}] {score:.0f}/100")
            if reason and i < 3:
                lines.append(f"        {reason[:100]}")

    lines.append("=" * 60)
    return "\n".join(lines)


# ============================================================
#  自然语言查询匹配
# ============================================================

def build_query_prompt(results: List[EvalResult], query: str) -> str:
    """构建自然语言查询的提示词。"""
    items = []
    for r in results:
        tags = ", ".join(r.style_tags)
        items.append(
            f"[{r.name}]\n"
            f"  评分: {r.score:.0f}/100\n"
            f"  风格标签: {tags}\n"
            f"  描述: {r.description}\n"
            f"  分析: {r.analysis}"
        )

    return f"""你是一位专业的影视调色师和 LUT 推荐专家。

现有 {len(results)} 个已评估的 LUT（Look-Up Table），每个都有详细的风格描述和色彩分析数据：

{"\n\n".join(items)}

用户想要: "{query}"

请根据以上 LUT 的风格特征，推荐最符合用户需求的 LUT。

请按严格的 JSON 格式输出（不要包含额外文字）：
```json
{{
  "matches": [
    {{
      "name": "LUT名称",
      "relevance": 95,
      "reason": "为什么这个 LUT 匹配用户的描述（50-100字）"
    }}
  ],
  "total": 3
}}
```

要求:
- matches 按相关度从高到低排列，最多 5 个
- relevance 范围 0-100
- 如果用户描述是中文，用中文回复；英文则用英文
- 如果你认为没有 LUT 能匹配，返回 total: 0 和空数组"""


def match_query_ai(
    results: List[EvalResult],
    query: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_API_URL,
    stream_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[List[Dict], str]:
    """用 AI 根据自然语言查询匹配最佳 LUT。支持流式输出。"""
    if not results or not query:
        return [], ""

    prompt = build_query_prompt(results, query)
    messages = [
        {"role": "system",
         "content": "你是一名专业的 LUT 推荐专家。请根据风格描述匹配用户需求。"},
        {"role": "user", "content": prompt},
    ]

    if stream_callback:
        response = stream_llm_api(
            api_key, messages, model, base_url,
            on_chunk=stream_callback)
    else:
        response = call_llm_api(api_key, messages, model, base_url)

    parsed = parse_eval_json(response)

    matches = parsed.get("matches", [])
    if matches:
        return matches, query

    return [], query


def match_query_local(results: List[EvalResult], query: str) -> List[Dict]:
    """本地关键词匹配（无 API 回退）。"""
    if not results or not query:
        return []

    keywords = _extract_keywords(query)
    if not keywords:
        return []

    scored = []
    for r in results:
        score = 0
        text = (
            r.name.lower() + " " +
            r.description.lower() + " " +
            " ".join(r.style_tags).lower()
        )
        for kw, weight in keywords:
            if kw in text:
                score += weight
        if score > 0:
            scored.append((score, r))

    scored.sort(key=lambda x: -x[0])
    return [
        {"name": r.name, "relevance": min(100, s),
         "reason": f"关键词匹配度 {s}"}
        for s, r in scored[:5]
    ]


def _extract_keywords(query: str) -> List[Tuple[str, int]]:
    """从自然语言中提取关键词及权重。"""
    kw_map = {
        # 中文风格关键词
        "德味": (["德", "德国", "leica", "徕卡"], 30),
        "复古": (["复古", "vintage", "retro", "怀旧", "old"], 25),
        "黑白": (["黑白", "黑", "白", "黑白", "单色", "bw", "b&w", "mono", "灰度"], 20),
        "电影": (["电影", "cinematic", "film", "cinema", "胶片"], 25),
        "温暖": (["暖", "温暖", "warm", "阳光", "golden"], 20),
        "冷": (["冷", "cool", "blue", "忧郁", "清冷", "冷静"], 20),
        "高对比": (["高对比", "contrast", "强烈", "锐利", "sharp"], 20),
        "柔和": (["柔和", "soft", "柔和", "梦幻", "朦胧", "温柔"], 20),
        "清新": (["清新", "fresh", "干净", "clear", "通透"], 20),
        "暗黑": (["暗", "黑", "dark", "暗黑", "压抑", "深沉"], 20),
        "日系": (["日系", "日式", "japan", "小清新", "清新"], 20),
        "港风": (["港", "香港", "hk", "港风", "霓虹"], 20),
        "赛博": (["赛博", "cyber", "科幻", "未来", "霓虹", "夜"], 20),
        "自然": (["自然", "natural", "真实", "写实", "纪实"], 15),
        "人像": (["人像", "portrait", "皮肤", "人物", "face"], 15),
        "风光": (["风景", "风光", "landscape", "自然"], 15),
        "胶片": (["胶片", "film", "菲林", "胶卷"], 25),
    }

    q_lower = query.lower()
    results = []
    for category, (kws, weight) in kw_map.items():
        for kw in kws:
            if kw in q_lower:
                results.append((kw, weight))
                break
    return results


def match_query(
    results: List[EvalResult],
    query: str,
    api_key: str = "",
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_API_URL,
    stream_callback: Optional[Callable[[str], None]] = None,
) -> List[Dict]:
    """
    自然语言查询匹配入口。
    有 API 走 AI 匹配（支持流式），否则走本地关键词匹配。
    返回: [{"name": str, "relevance": float, "reason": str}, ...]
    """
    if not results or not query:
        return []

    if api_key and HAS_REQUESTS:
        matches, _ = match_query_ai(
            results, query, api_key, model, base_url,
            stream_callback=stream_callback)
        return matches
    else:
        return match_query_local(results, query)


def format_query_result(matches: List[Dict], query: str) -> str:
    """将查询结果格式化为文本。"""
    lines = []
    lines.append("=" * 60)
    lines.append(f"  🔍 风格查询: \"{query}\"")
    lines.append("=" * 60)

    if not matches:
        lines.append("  未找到匹配的 LUT")
        return "\n".join(lines)

    medals = ["🥇", "🥈", "🥉"]
    for i, m in enumerate(matches[:5]):
        medal = medals[i] if i < 3 else f"  {i+1}."
        name = m.get("name", "?")
        rel = m.get("relevance", 0)
        reason = m.get("reason", "")
        bar = "█" * int(rel / 10) + "░" * (10 - int(rel / 10))
        lines.append(f"\n  {medal} [{name}] 匹配度: {rel:.0f}% {bar}")
        if reason:
            lines.append(f"     理由: {reason[:150]}")

    lines.append("=" * 60)
    return "\n".join(lines)
