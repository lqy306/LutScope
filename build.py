#!/usr/bin/env python3
"""
LutScope 构建脚本 — 将 C 引擎 + Python 打包为单文件可执行程序

用法:
    python3 build.py              # 完整构建
    python3 build.py --dist-dir /path  # 指定输出目录
"""

import os
import sys
import shutil
import subprocess
import stat
import argparse

PROJECT = "LutScope"
REQUIRED_FILES = ["lut_tool.c", "engine.py", "app.py"]
BUILD_DIR = "build"
DIST_DIR = "dist"


def find_python():
    """找 Python 3 可执行路径。"""
    for name in ["python3", "python"]:
        try:
            result = subprocess.run([name, "--version"],
                                    capture_output=True, text=True)
            if result.returncode == 0 and "Python 3" in result.stdout:
                return name
        except FileNotFoundError:
            continue
    return sys.executable


def compile_c(python: str) -> bool:
    """编译 C 引擎。"""
    if os.path.exists("lut_tool"):
        os.remove("lut_tool")

    print("  [1/3] 编译 C 引擎...")
    result = subprocess.run(
        ["gcc", "-Wall", "-Wextra", "-ansi", "-pedantic",
         "-O2", "-o", "lut_tool", "lut_tool.c", "-lm"],
        capture_output=True, text=True)
    if result.returncode != 0:
        print(f"    ❌ 编译失败: {result.stderr}")
        return False
    print(f"    ✅ lut_tool ({os.path.getsize('lut_tool')} bytes)")
    return True


def create_zipapp(python: str, dist_file: str) -> bool:
    """创建 Python zipapp 单文件。"""
    app_dir = os.path.abspath(os.path.join(BUILD_DIR, f"{PROJECT}.app"))
    orig_cwd = os.getcwd()

    # 清理旧构建
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)
    os.makedirs(app_dir, exist_ok=True)

    # 复制所需文件
    shutil.copy("lut_tool", os.path.join(app_dir, "lut_tool"))
    shutil.copy("engine.py", os.path.join(app_dir, "engine.py"))
    shutil.copy("app.py", os.path.join(app_dir, "__main__.py"))

    os.chdir(app_dir)
    result = subprocess.run(
        [python, "-m", "zipapp",
         "-p", "/usr/bin/env python3",
         "-o", dist_file, "."],
        capture_output=True, text=True)
    os.chdir(orig_cwd)

    if result.returncode != 0:
        print(f"    ❌ zipapp 打包失败: {result.stderr}")
        return False

    # 添加执行权限
    st = os.stat(dist_file)
    os.chmod(dist_file, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"    ✅ {os.path.relpath(dist_file)} ({os.path.getsize(dist_file)} bytes)")
    return True


def main():
    parser = argparse.ArgumentParser(description=f"构建 {PROJECT}")
    parser.add_argument("--dist-dir", default=DIST_DIR,
                        help=f"输出目录 (默认 {DIST_DIR})")
    parser.add_argument("--skip-compile", action="store_true",
                        help="跳过 C 编译（使用已有的 lut_tool）")
    args = parser.parse_args()

    os.makedirs(args.dist_dir, exist_ok=True)
    python = find_python()

    print(f"🔨 构建 {PROJECT}")
    print(f"   Python: {python}")
    print(f"   输出:   {os.path.abspath(args.dist_dir)}")
    print()

    # 检查源文件
    for f in REQUIRED_FILES:
        if not os.path.exists(f):
            print(f"   错误: 找不到 {f}")
            sys.exit(1)

    # 编译 C
    if not args.skip_compile:
        if not compile_c(python):
            sys.exit(1)
    elif not os.path.exists("lut_tool"):
        print("   错误: lut_tool 不存在，需要先编译或去掉 --skip-compile")
        sys.exit(1)

    # 打包单文件
    dist_file = os.path.join(os.path.abspath(args.dist_dir), PROJECT)
    print("  [2/3] 创建单文件可执行程序...")

    # 先清理旧的 dist/PROJECT 目录
    old_dir = os.path.join(args.dist_dir, PROJECT)
    if os.path.isdir(old_dir) and not os.path.isfile(old_dir):
        shutil.rmtree(old_dir)

    if not create_zipapp(python, dist_file):
        sys.exit(1)

    # 符号链接到项目根目录
    print("  [3/3] 创建符号链接...")
    link_name = PROJECT
    if os.path.exists(link_name) or os.path.islink(link_name):
        os.remove(link_name)
    os.symlink(os.path.relpath(dist_file, "."), link_name)
    print(f"    ✅ {link_name} → {os.path.relpath(dist_file, '.')}")

    # 清理构建目录
    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)

    print()
    print(f"🎉 构建完成! 运行:")
    print(f"   ./{link_name}")
    print()
    print(f"   或拷贝单文件到任何位置运行:")
    print(f"   cp {os.path.relpath(dist_file, '.')} /usr/local/bin/{PROJECT}")


if __name__ == "__main__":
    main()
