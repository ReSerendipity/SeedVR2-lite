"""
roadmap.py 批量状态更新脚本
将验证通过的 FRAMEWORK_DONE 项更新为 COMPLETED
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
ROADMAP_PATH = PROJECT_ROOT / "app" / "integrated_app" / "optimization" / "roadmap.py"

# 明确保持 FRAMEWORK_DONE 的项（确认未实现，需后续开发）
KEEP_FRAMEWORK_DONE = {
    ("P2", 2, "Selective Block Offloading"),
    ("P2", 3, "TeaCache 时间步跳过"),
}

# 需要修正来源标注的项
SOURCE_FIXES = {
    ("P0", 5): "torchao/CogVideo（HunyuanVideo纯PyTorch方案未采用）",
    ("P0", 3): "Upscale-A-Video/RVRT/DiffVSR",
}


def main():
    with open(ROADMAP_PATH, encoding="utf-8") as f:
        content = f.read()

    # 统计原始状态
    orig_completed = content.count("ImplementationStatus.COMPLETED")
    orig_framework = content.count("ImplementationStatus.FRAMEWORK_DONE")
    print(f"原始状态: COMPLETED={orig_completed}, FRAMEWORK_DONE={orig_framework}")

    # 策略：对于没有显式 status 参数的 FeatureItem（使用默认值 FRAMEWORK_DONE），
    # 添加 , ImplementationStatus.COMPLETED
    # 对于有显式 ImplementationStatus.FRAMEWORK_DONE 的，替换为 COMPLETED

    # 先处理有显式 status 的情况
    # 匹配 FeatureItem(..., Priority.XXX, ImplementationStatus.FRAMEWORK_DONE)
    # 但需要排除 KEEP_FRAMEWORK_DONE 中的项

    # 更安全的方式：逐行处理，找到每个 FeatureItem 定义，检查是否在 KEEP 列表中
    lines = content.split("\n")
    new_lines = []
    updated_count = 0
    skip_count = 0
    i = 0

    while i < len(lines):
        line = lines[i]
        # 检测 FeatureItem( 开始
        if "FeatureItem(" in line:
            # 收集完整的 FeatureItem 定义（可能跨行）
            item_lines = [line]
            j = i + 1
            paren_depth = line.count("(") - line.count(")")
            while paren_depth > 0 and j < len(lines):
                item_lines.append(lines[j])
                paren_depth += lines[j].count("(") - lines[j].count(")")
                j += 1

            item_text = "\n".join(item_lines)

            # 提取 id 和 priority
            id_match = re.search(r"FeatureItem\(\s*(\d+)", item_text)
            pri_match = re.search(r"Priority\.(\w+)", item_text)
            sug_match = re.search(r'"([^"]+)"', item_text)

            if id_match and pri_match and sug_match:
                fid = int(id_match.group(1))
                priority = pri_match.group(1)
                sug_match.group(1)

                # 检查是否在 KEEP 列表中
                is_keep = any(k[0] == priority and k[1] == fid for k in KEEP_FRAMEWORK_DONE)

                # 检查是否已经是 COMPLETED
                already_completed = "ImplementationStatus.COMPLETED" in item_text

                if not already_completed and not is_keep:
                    # 更新为 COMPLETED
                    if "ImplementationStatus.FRAMEWORK_DONE" in item_text:
                        item_text = item_text.replace(
                            "ImplementationStatus.FRAMEWORK_DONE", "ImplementationStatus.COMPLETED"
                        )
                    else:
                        # 没有显式 status，使用默认值 FRAMEWORK_DONE
                        # 需要在最后一个参数后添加 , ImplementationStatus.COMPLETED
                        # 找到最后一个 ) 之前的位置
                        item_text = re.sub(
                            r"(\s*)\)(\s*)$", r", ImplementationStatus.COMPLETED\1)\2", item_text, count=1
                        )
                    updated_count += 1
                elif is_keep:
                    skip_count += 1

                # 修正来源标注
                key = (priority, fid)
                if key in SOURCE_FIXES:
                    re.search(r'"([^"]+)"', item_text.split(",")[2] if "," in item_text else "")
                    # 简单替换：找到第二个引号字符串（source）
                    parts = item_text.split('"')
                    if len(parts) >= 5:
                        parts[3] = SOURCE_FIXES[key]
                        item_text = '"'.join(parts)

            new_lines.extend(item_text.split("\n"))
            i = j
        else:
            new_lines.append(line)
            i += 1

    new_content = "\n".join(new_lines)

    # 统计新状态
    new_completed = new_content.count("ImplementationStatus.COMPLETED")
    new_framework = new_content.count("ImplementationStatus.FRAMEWORK_DONE")
    print(f"更新后状态: COMPLETED={new_completed}, FRAMEWORK_DONE={new_framework}")
    print(f"更新了 {updated_count} 项，跳过 {skip_count} 项（KEEP列表）")

    # 写回
    with open(ROADMAP_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"\n已更新 {ROADMAP_PATH}")


if __name__ == "__main__":
    main()
