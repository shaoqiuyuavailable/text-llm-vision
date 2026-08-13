#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""输出代理监听端口（config.json 的 port，缺失/非法回退 8787）。

给 start-proxy.bat 用：bat 无法可靠解析 JSON，fork 本脚本拿端口号。
用法：python read_port.py -> 打印端口号（如 8787）
"""
import os

import config_loader

if __name__ == "__main__":
    # 独立运行时无需 config_loader 的日志 handler，直接取值
    print(config_loader.get_port())
