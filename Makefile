CC = gcc
CFLAGS = -Wall -Wextra -ansi -pedantic -O2
LDFLAGS = -lm

.PHONY: all clean dist run

all: dist

# C 引擎 — 独立编译目标
lut_tool: lut_tool.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

# 单文件可执行程序 (Python zipapp)
dist: lut_tool build.py
	python3 build.py

# 一键运行（自动构建后启动 TUI）
run: dist
	./LutScope

# 清理
clean:
	rm -f lut_tool LutScope
	rm -rf build/ dist/
	rm -rf __pycache__ */__pycache__
	rm -rf .eggs *.egg-info
