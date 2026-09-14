#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HerPulse 看板渲染器（M4 最后一步）

把 dashboard_data.json 内嵌进看板 HTML 模板（替换 __DATA_JSON__ 占位符），
生成可离线打开、无 CORS 限制的最终看板文件。

用法：
  python src/build_dashboard.py --data data/dashboard_data.json \
      --template ../herpulse-dashboard-live.html --out ../herpulse-dashboard-live.html
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description="HerPulse 看板渲染器")
    p.add_argument("--data", default="data/dashboard_data.json")
    p.add_argument("--template", default="templates/dashboard.html")
    p.add_argument("--out", default="../herpulse-dashboard-live.html")
    args = p.parse_args()

    base = Path(__file__).resolve().parent.parent  # herpulse/ 根
    data_path = (base / args.data).resolve()
    tpl_path = (base / args.template).resolve()
    out_path = (base / args.out).resolve()

    payload = json.load(open(data_path, encoding="utf-8"))
    # 转成 JS 安全对象字面量：中文保留，转义 </script> 防注入破坏
    js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    tpl = open(tpl_path, encoding="utf-8").read()
    if "__DATA_JSON__" not in tpl:
        raise SystemExit("模板中未找到 __DATA_JSON__ 占位符，请确认模板路径正确。")

    html = tpl.replace("__DATA_JSON__", js)
    out_path.write_text(html, encoding="utf-8")

    print(f"看板已生成：{out_path}")
    print(f"  内嵌数据：{len(payload['tropes'])} 个设定点、{len(payload['recipes'])} 个配方、{len(payload['new_words'])} 个新词")


if __name__ == "__main__":
    main()
