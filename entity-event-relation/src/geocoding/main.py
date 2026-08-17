# -*- coding: utf-8 -*-
"""
地理编码主程序
协调整个地理编码流程

使用方法:
    cd f:\python\python_space\china-history-war-kg\entity-event-relation\src

    # 完整流程（推荐）
    python -m geocoding.main pipeline

    # 跳过审核的完整流程
    python -m geocoding.main pipeline --mode auto-approve

    # 统计待编码地点（不导出）
    python -m geocoding.main stats

    # 分步运行
    python -m geocoding.main export                    # 步骤1: 导出待编码地点
    python -m geocoding.main geocode <input_file>      # 步骤2: 调用高德API编码
    python -m geocoding.main review <input_file>       # 步骤3: 人工审核
    python -m geocoding.main import <input_file>       # 步骤4: 导入数据库

    #什么时候需要编辑？
    坐标偏差 - 高德返回的坐标不够精确
    古今差异 - 历史城址与现代城市中心不同
    冷僻地名 - API无法识别，需要手动查找

    # 跳过审核的分步运行
    python -m geocoding.main export
    python -m geocoding.main geocode <input_file>
    python -m geocoding.main review <input_file> --mode auto-approve
    python -m geocoding.main import <input_file>

配置:
    在 entity-event-relation/src/geocoding/.env 文件中设置高德 API Key:
    AMAP_API_KEY=your_api_key_here

参数说明:
    pipeline       - 运行完整流程（导出→编码→审核→导入）
    stats          - 统计待编码地点（不导出）
    export         - 仅导出待编码地点到 JSON 文件
    geocode        - 仅调用高德API进行地理编码
    review         - 仅审核编码结果
    import         - 仅将审核通过的坐标导入数据库

审核模式:
    interactive    - 交互式审核（逐个确认，默认）
    batch          - 批量审核（基于置信度自动审核）
    auto-approve   - 跳过审核（全部通过）
"""

import os
import sys
import argparse
from typing import Optional

from .export_unmapped_places import export_unmapped_places, get_statistics
from .geocode_amap import AmapGeocoder, load_and_geocode, load_env_file
from .review_geocoding import GeocodingReviewer, review_results
from .import_coordinates import import_coordinates, CoordinateImporter


def run_full_pipeline(api_key: str = None, db_path: str = None, mode: str = 'interactive'):
    """
    运行完整的地理编码流程

    Args:
        api_key: 高德 API Key，如果为 None 则从 .env 文件读取
        db_path: 数据库路径
        mode: 审核模式 'interactive'、'batch' 或 'auto-approve'
    """
    # 加载 .env 文件
    load_env_file()

    print("\n" + "="*60)
    print("地理编码流程")
    print("="*60)

    # 1. 显示当前统计
    print("\n[1/5] 当前坐标统计:")
    stats = get_statistics(db_path)
    print(f"  总地点数: {stats['total_places']}")
    print(f"  已有坐标: {stats['with_coordinates']}")
    print(f"  待编码: {stats['without_coordinates']}")
    print(f"  完成率: {stats['completion_rate']}")

    if stats['without_coordinates'] == 0:
        print("\n所有地点已有坐标，无需编码")
        return

    # 2. 导出待编码地点
    print("\n[2/5] 导出待编码地点:")
    output_file = export_unmapped_places(db_path)

    # 3. 调用高德 API 编码
    print("\n[3/5] 调用高德 API 编码:")
    geocoder = AmapGeocoder(api_key)

    # 读取待编码地点
    import json
    with open(output_file, 'r', encoding='utf-8') as f:
        places = json.load(f)

    # 批量编码
    results = geocoder.batch_geocode(places)

    if not results:
        print("\n编码失败，无结果")
        return

    # 保存编码结果
    geocoded_file = geocoder.save_results(results)

    # 4. 审核编码结果
    print("\n[4/5] 审核编码结果:")
    reviewer = GeocodingReviewer(results_file=geocoded_file)

    if mode == 'auto-approve':
        reviewer.review_auto_approve()
    elif mode == 'batch':
        reviewer.review_batch()
    else:
        reviewer.review_interactive()

    if not reviewer.approved:
        print("\n没有通过审核的结果")
        return

    # 保存审核结果
    approved_file = reviewer.save_approved()

    # 5. 导入数据库
    print("\n[5/5] 导入数据库:")
    result = import_coordinates(approved_file, db_path)

    # 显示最终统计
    print("\n" + "="*60)
    print("流程完成!")
    print("="*60)
    final_stats = get_statistics(db_path)
    print(f"\n最终统计:")
    print(f"  总地点数: {final_stats['total_places']}")
    print(f"  已有坐标: {final_stats['with_coordinates']}")
    print(f"  待编码: {final_stats['without_coordinates']}")
    print(f"  完成率: {final_stats['completion_rate']}")


def run_stats_only(db_path: str = None):
    """仅统计待编码地点（不导出）"""
    print("\n坐标统计:")
    stats = get_statistics(db_path)
    print(f"  总地点数: {stats['total_places']}")
    print(f"  已有坐标: {stats['with_coordinates']}")
    print(f"  待编码:   {stats['without_coordinates']}")
    print(f"  完成率:   {stats['completion_rate']}")

    if stats.get('by_source'):
        print(f"\n按来源统计:")
        for source, count in stats['by_source'].items():
            print(f"  {source}: {count}")

    if stats.get('by_confidence'):
        print(f"\n按置信度统计:")
        for confidence, count in stats['by_confidence'].items():
            print(f"  {confidence}: {count}")

    return stats


def run_export_only(db_path: str = None):
    """仅导出待编码地点"""
    print("\n导出待编码地点:")
    output_file = export_unmapped_places(db_path)

    stats = get_statistics(db_path)
    print(f"\n当前统计:")
    print(f"  总地点数: {stats['total_places']}")
    print(f"  已有坐标: {stats['with_coordinates']}")
    print(f"  待编码: {stats['without_coordinates']}")
    print(f"  完成率: {stats['completion_rate']}")

    return output_file


def run_geocode_only(input_file: str, api_key: str):
    """仅执行地理编码"""
    print("\n执行地理编码:")
    results = load_and_geocode(input_file, api_key)
    return results


def run_review_only(input_file: str, mode: str = 'interactive'):
    """仅执行审核"""
    print("\n审核编码结果:")
    results = review_results(input_file, mode)
    return results


def run_import_only(input_file: str, db_path: str = None):
    """仅执行导入"""
    print("\n导入坐标到数据库:")
    result = import_coordinates(input_file, db_path)
    return result


def print_usage():
    """打印详细使用说明"""
    usage = """
╔══════════════════════════════════════════════════════════════╗
║                    地理编码工具使用说明                        ║
╚══════════════════════════════════════════════════════════════╝

配置:
    在 entity-event-relation/src/geocoding/.env 文件中设置高德 API Key:
    AMAP_API_KEY=your_api_key_here

获取高德 API Key:
    1. 访问 https://console.amap.com/
    2. 注册/登录账号
    3. 创建应用 → 添加Key → 选择"Web服务"
    4. 复制生成的 Key 到 .env 文件

使用方法:

    【完整流程】（推荐）
    python -m geocoding.main pipeline

    【跳过审核的完整流程】
    python -m geocoding.main pipeline --mode auto-approve

    【分步运行】

    步骤1: 导出待编码地点
    python -m geocoding.main export
    输出: unmapped_places_YYYYMMDD_HHMMSS.json

    步骤2: 调用高德API编码
    python -m geocoding.main geocode unmapped_places_*.json
    输出: geocoded_results_YYYYMMDD_HHMMSS.json

    步骤3: 人工审核
    python -m geocoding.main review geocoded_results_*.json
    输出: approved_coordinates_YYYYMMDD_HHMMSS.json

    步骤3（跳过审核）:
    python -m geocoding.main review geocoded_results_*.json --mode auto-approve

    步骤4: 导入数据库
    python -m geocoding.main import approved_coordinates_*.json

    完成后:
    1. 重启后端服务
    2. 刷新历史地图页面
    3. 行军路线就能显示了

参数说明:
    pipeline       - 运行完整流程（导出→编码→审核→导入）
    stats          - 统计待编码地点（不导出）
    export         - 仅导出待编码地点到 JSON 文件
    geocode        - 仅调用高德API进行地理编码
    review         - 仅审核编码结果
    import         - 仅将审核通过的坐标导入数据库

审核模式:
    interactive    - 交互式审核（逐个确认，默认）
    batch          - 批量审核（基于置信度自动审核）
    auto-approve   - 跳过审核（全部通过）

可选参数:
    --api-key  - 高德 API Key（可选，默认从 .env 文件读取）
    --db-path  - 数据库路径（可选，默认为 backend/database）
    --mode     - 审核模式: interactive、batch 或 auto-approve
"""
    print(usage)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='地理编码工具 - 将历史地名转换为现代坐标',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python -m geocoding.main pipeline                    # 运行完整流程
    python -m geocoding.main stats                      # 统计待编码地点
    python -m geocoding.main export                     # 导出待编码地点
    python -m geocoding.main geocode input.json         # 执行编码
    python -m geocoding.main review input.json          # 审核结果
    python -m geocoding.main import input.json          # 导入数据库

配置文件: entity-event-relation/src/geocoding/.env
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # 完整流程命令
    pipeline_parser = subparsers.add_parser('pipeline', help='运行完整流程（推荐）')
    pipeline_parser.add_argument('--api-key', help='高德 API Key（可选，默认从 .env 文件读取）')
    pipeline_parser.add_argument('--db-path', help='数据库路径（默认: backend/database）')
    pipeline_parser.add_argument('--mode', choices=['interactive', 'batch', 'auto-approve'],
                                 default='interactive', help='审核模式（默认: interactive）')

    # 导出命令
    export_parser = subparsers.add_parser('export', help='导出待编码地点到JSON文件')
    export_parser.add_argument('--db-path', help='数据库路径（默认: backend/database）')

    # 统计命令
    stats_parser = subparsers.add_parser('stats', help='统计待编码地点（不导出）')
    stats_parser.add_argument('--db-path', help='数据库路径（默认: backend/database）')

    # 编码命令
    geocode_parser = subparsers.add_parser('geocode', help='调用高德API进行地理编码')
    geocode_parser.add_argument('input_file', help='输入文件路径（export命令生成的JSON文件）')
    geocode_parser.add_argument('--api-key', help='高德 API Key（可选，默认从 .env 文件读取）')

    # 审核命令
    review_parser = subparsers.add_parser('review', help='审核编码结果')
    review_parser.add_argument('input_file', help='输入文件路径（geocode命令生成的JSON文件）')
    review_parser.add_argument('--mode', choices=['interactive', 'batch', 'auto-approve'],
                               default='interactive', help='审核模式（默认: interactive）')

    # 导入命令
    import_parser = subparsers.add_parser('import', help='将审核通过的坐标导入数据库')
    import_parser.add_argument('input_file', help='输入文件路径（review命令生成的JSON文件）')
    import_parser.add_argument('--db-path', help='数据库路径（默认: backend/database）')

    args = parser.parse_args()

    if args.command == 'pipeline':
        run_full_pipeline(args.api_key, args.db_path, args.mode)
    elif args.command == 'stats':
        run_stats_only(args.db_path)
    elif args.command == 'export':
        run_export_only(args.db_path)
    elif args.command == 'geocode':
        run_geocode_only(args.input_file, args.api_key)
    elif args.command == 'review':
        run_review_only(args.input_file, args.mode)
    elif args.command == 'import':
        run_import_only(args.input_file, args.db_path)
    else:
        print_usage()


if __name__ == '__main__':
    main()
