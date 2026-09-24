# -*- coding: utf-8 -*-
"""
地理编码模块
用于处理历史地名到现代坐标映射

使用方法:
    from geocoding import export_unmapped_places, AmapGeocoder, review_results, import_coordinates

    # 完整流程
    from geocoding.main import run_full_pipeline
    run_full_pipeline()  # 从 .env 文件读取 API Key

配置:
    在 entity-event-relation/src/geocoding/.env 文件中设置:
    AMAP_API_KEY=your_api_key_here
"""

from .historical_places_mapping import HISTORICAL_MAPPING, get_modern_name, search_by_keyword
from .export_unmapped_places import export_unmapped_places, get_unmapped_places_from_db, get_statistics
from .geocode_amap import AmapGeocoder, load_and_geocode, load_env_file
from .review_geocoding import GeocodingReviewer, review_results
from .import_coordinates import CoordinateImporter, import_coordinates, import_from_approved_json
from .main import main as run_main

__all__ = [
    # 历史地名映射
    'HISTORICAL_MAPPING',
    'get_modern_name',
    'search_by_keyword',

    # 导出待编码地点
    'export_unmapped_places',
    'get_unmapped_places_from_db',
    'get_statistics',

    # 高德API编码
    'AmapGeocoder',
    'load_and_geocode',
    'load_env_file',

    # 审核
    'GeocodingReviewer',
    'review_results',

    # 导入数据库
    'CoordinateImporter',
    'import_coordinates',
    'import_from_approved_json',

    # 主程序
    'run_main',
]
