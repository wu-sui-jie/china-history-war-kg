from __future__ import annotations

# -*- coding: utf-8 -*-
"""
地理编码结果审核
用于人工审核地理编码结果
"""

import json
import os
from typing import List, Dict, Optional
from datetime import datetime


class GeocodingReviewer:
    """地理编码结果审核器"""

    def __init__(self, results_file: str = None, results: List[Dict] = None):
        """
        初始化审核器

        Args:
            results_file: 结果文件路径
            results: 结果列表（直接传入）
        """
        if results_file:
            with open(results_file, 'r', encoding='utf-8') as f:
                self.results = json.load(f)
        elif results:
            self.results = results
        else:
            raise ValueError("请提供 results_file 或 results")

        self.approved = []
        self.rejected = []
        self.skipped = []
        self.edited = []

    def review_interactive(self):
        """交互式审核"""
        total = len(self.results)

        print(f"\n{'='*60}")
        print(f"地理编码结果审核")
        print(f"共 {total} 个结果待审核")
        print(f"{'='*60}")
        print(f"操作说明:")
        print(f"  y - 通过")
        print(f"  n - 拒绝")
        print(f"  s - 跳过")
        print(f"  e - 编辑坐标")
        print(f"  q - 退出审核")
        print(f"{'='*60}\n")

        for i, result in enumerate(self.results, 1):
            print(f"[{i}/{total}]")
            print(f"  原始地名: {result.get('original_name', 'N/A')}")
            print(f"  搜索地名: {result.get('search_name', 'N/A')}")
            print(f"  地名来源: {result.get('name_source', 'N/A')}")
            print(f"  编码坐标: {result.get('longitude', 'N/A')}, {result.get('latitude', 'N/A')}")
            print(f"  格式地址: {result.get('formatted_address', 'N/A')}")
            print(f"  置信度:   {result.get('confidence', 'N/A')}")

            while True:
                action = input("\n  操作 (y/n/s/e/q): ").lower().strip()

                if action == 'y':
                    result['approved'] = True
                    result['review_time'] = datetime.now().isoformat()
                    self.approved.append(result)
                    print("  ✓ 已通过\n")
                    break

                elif action == 'n':
                    result['approved'] = False
                    result['review_time'] = datetime.now().isoformat()
                    self.rejected.append(result)
                    print("  ✗ 已拒绝\n")
                    break

                elif action == 's':
                    self.skipped.append(result)
                    print("  → 已跳过\n")
                    break

                elif action == 'e':
                    try:
                        print("  请输入新坐标:")
                        lng = float(input("    经度: "))
                        lat = float(input("    纬度: "))

                        result['longitude'] = lng
                        result['latitude'] = lat
                        result['manual_edit'] = True
                        result['approved'] = True
                        result['review_time'] = datetime.now().isoformat()
                        result['edit_note'] = input("    备注 (可选): ")

                        self.approved.append(result)
                        self.edited.append(result)
                        print("  ✓ 已编辑并通过\n")
                        break
                    except ValueError:
                        print("  输入无效，请重试")

                elif action == 'q':
                    print("\n退出审核")
                    return

                else:
                    print("  无效操作，请重试")

        # 显示审核统计
        self.print_statistics()

    def review_batch(self, auto_approve_threshold: float = 0.8) -> List[Dict]:
        """
        批量审核（基于置信度自动审核）

        Args:
            auto_approve_threshold: 自动通过的置信度阈值

        Returns:
            通过审核的结果列表
        """
        # 置信度等级映射
        confidence_levels = {
            'high': 1.0,
            'medium': 0.7,
            'low': 0.4,
            '': 0.0
        }

        for result in self.results:
            confidence = result.get('confidence', '')
            confidence_score = confidence_levels.get(confidence, 0.0)

            if confidence_score >= auto_approve_threshold:
                result['approved'] = True
                result['auto_approved'] = True
                result['review_time'] = datetime.now().isoformat()
                self.approved.append(result)
            else:
                result['approved'] = False
                result['needs_review'] = True
                self.rejected.append(result)

        self.print_statistics()
        return self.approved

    def review_auto_approve(self) -> List[Dict]:
        """
        自动通过所有结果（跳过审核）

        Returns:
            所有结果列表（全部标记为已通过）
        """
        print(f"\n{'='*60}")
        print(f"自动通过模式")
        print(f"共 {len(self.results)} 个结果将全部通过")
        print(f"{'='*60}")

        for result in self.results:
            result['approved'] = True
            result['auto_approved'] = True
            result['review_time'] = datetime.now().isoformat()
            self.approved.append(result)

        self.print_statistics()
        return self.approved

    def print_statistics(self):
        """打印审核统计"""
        print(f"\n{'='*60}")
        print(f"审核统计:")
        print(f"  通过: {len(self.approved)}")
        print(f"  拒绝: {len(self.rejected)}")
        print(f"  跳过: {len(self.skipped)}")
        print(f"  编辑: {len(self.edited)}")
        print(f"  总计: {len(self.results)}")
        print(f"{'='*60}")

    def save_approved(self, output_dir: str = None) -> str:
        """
        保存审核通过的结果

        Args:
            output_dir: 输出目录

        Returns:
            输出文件路径
        """
        if not self.approved:
            print("没有通过审核的结果")
            return None

        if output_dir is None:
            output_dir = os.path.dirname(__file__)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = os.path.join(output_dir, f'approved_coordinates_{timestamp}.json')

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.approved, f, ensure_ascii=False, indent=2)

        print(f"\n已保存 {len(self.approved)} 个通过审核的结果")
        print(f"输出文件: {output_file}")

        return output_file

    def save_all(self, output_dir: str = None) -> str:
        """
        保存所有审核结果（包括拒绝的）

        Args:
            output_dir: 输出目录

        Returns:
            输出文件路径
        """
        if output_dir is None:
            output_dir = os.path.dirname(__file__)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = os.path.join(output_dir, f'review_results_{timestamp}.json')

        all_results = {
            'approved': self.approved,
            'rejected': self.rejected,
            'skipped': self.skipped,
            'statistics': {
                'total': len(self.results),
                'approved': len(self.approved),
                'rejected': len(self.rejected),
                'skipped': len(self.skipped),
                'edited': len(self.edited)
            }
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)

        print(f"\n审核结果已保存: {output_file}")
        return output_file


def review_results(input_file: str, mode: str = 'interactive') -> List[Dict]:
    """
    审核地理编码结果

    Args:
        input_file: 输入文件路径
        mode: 审核模式 'interactive'、'batch' 或 'auto-approve'

    Returns:
        通过审核的结果列表
    """
    reviewer = GeocodingReviewer(results_file=input_file)

    if mode == 'auto-approve':
        reviewer.review_auto_approve()
    elif mode == 'batch':
        reviewer.review_batch()
    else:
        reviewer.review_interactive()

    # 保存结果
    if reviewer.approved:
        reviewer.save_approved()

    return reviewer.approved


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("用法: python review_geocoding.py <input_file> [mode]")
        print("示例: python review_geocoding.py geocoded_results.json interactive")
        print("模式: interactive (交互式)、batch (批量) 或 auto-approve (跳过审核)")
        sys.exit(1)

    input_file = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else 'interactive'

    review_results(input_file, mode)
