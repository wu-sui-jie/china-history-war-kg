# -*- coding: utf-8 -*-
"""
高德地图地理编码
使用高德地图 Web 服务 API 进行地理编码
"""

import requests
import json
import time
import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from .historical_places_mapping import HISTORICAL_MAPPING


def load_env_file(env_file: str = None):
    """
    加载 .env 文件

    Args:
        env_file: .env 文件路径，默认为当前目录下的 .env
    """
    if env_file is None:
        env_file = os.path.join(os.path.dirname(__file__), '.env')

    if not os.path.exists(env_file):
        return

    with open(env_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # 跳过注释和空行
            if not line or line.startswith('#'):
                continue
            # 解析 KEY=VALUE
            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                # 去除引号
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                # 设置到环境变量（不覆盖已存在的）
                if key not in os.environ:
                    os.environ[key] = value


class AmapGeocoder:
    """高德地图地理编码器"""

    def __init__(self, api_key: str = None, env_file: str = None):
        """
        初始化编码器

        Args:
            api_key: 高德 API Key，如果为 None 则从 .env 文件或环境变量读取
            env_file: .env 文件路径
        """
        # 先加载 .env 文件
        load_env_file(env_file)

        # 优先使用传入的 api_key，其次从环境变量读取
        self.api_key = api_key or os.environ.get('AMAP_API_KEY', '')

        if not self.api_key or self.api_key == 'your_api_key_here':
            raise ValueError(
                "请提供高德 API Key:\n"
                "1. 修改 entity-event-relation/src/geocoding/.env 文件\n"
                "2. 或设置环境变量 AMAP_API_KEY\n"
                "3. 或在初始化时传入 api_key 参数\n"
                "获取地址: https://console.amap.com/"
            )

        self.base_url = "https://restapi.amap.com/v3/geocode/geo"
        self.session = requests.Session()
        self.request_count = 0
        self.success_count = 0
        self.fail_count = 0

    def get_search_name(self, place: Dict) -> Tuple[str, str]:
        """
        获取用于搜索的地名

        Args:
            place: 地点信息字典

        Returns:
            (搜索地名, 地名来源)
        """
        # 1. 优先使用 modern_name 字段
        if place.get('modern_name'):
            return place['modern_name'], 'modern_name'

        # 2. 检查历史地名映射表
        historical_name = place.get('name', '')
        if historical_name in HISTORICAL_MAPPING:
            return HISTORICAL_MAPPING[historical_name], 'mapping'

        # 3. 使用原始地名
        return historical_name, 'original'

    def build_address(self, place: Dict, search_name: str) -> str:
        """
        构建完整地址

        Args:
            place: 地点信息
            search_name: 搜索地名

        Returns:
            完整地址字符串
        """
        address_parts = []

        # 添加省份
        if place.get('province'):
            address_parts.append(place['province'])

        # 添加城市
        if place.get('city'):
            address_parts.append(place['city'])

        # 添加区县
        if place.get('district'):
            address_parts.append(place['district'])

        # 添加搜索地名
        address_parts.append(search_name)

        return ''.join(address_parts)

    def geocode_single(self, place: Dict) -> Optional[Dict]:
        """
        单个地点地理编码

        Args:
            place: 地点信息字典，需包含 id, name, modern_name 等字段

        Returns:
            编码结果字典，失败返回 None
        """
        # 获取搜索地名
        search_name, name_source = self.get_search_name(place)
        if not search_name:
            print(f"  ✗ {place.get('name', '未知')}: 无法确定搜索地名")
            return None

        # 构建完整地址
        address = self.build_address(place, search_name)

        # 调用 API
        params = {
            'key': self.api_key,
            'address': address,
            'output': 'JSON'
        }

        # 如果有城市信息，添加 city 参数提高精度
        if place.get('city'):
            params['city'] = place['city']

        try:
            response = self.session.get(self.base_url, params=params, timeout=10)
            data = response.json()

            self.request_count += 1

            if data['status'] == '1' and data.get('geocodes'):
                geocode = data['geocodes'][0]
                location = geocode['location'].split(',')

                result = {
                    'place_id': place['id'],
                    'original_name': place['name'],
                    'longitude': float(location[0]),
                    'latitude': float(location[1]),
                    'search_name': search_name,
                    'name_source': name_source,
                    'formatted_address': geocode.get('formatted_address', ''),
                    'confidence': geocode.get('level', ''),
                    'source': 'amap'
                }

                self.success_count += 1
                return result
            else:
                self.fail_count += 1
                info_code = data.get('infocode', 'unknown')
                print(f"  ✗ {place['name']}: API返回错误 ({info_code})")
                return None

        except Exception as e:
            self.fail_count += 1
            print(f"  ✗ {place['name']}: {str(e)}")
            return None

    def batch_geocode(self, places: List[Dict], delay: float = 0.05) -> List[Dict]:
        """
        批量地理编码

        Args:
            places: 地点列表
            delay: 请求间隔（秒），默认 0.05 秒

        Returns:
            编码结果列表
        """
        results = []
        total = len(places)

        print(f"\n开始批量编码: {total} 个地点")
        print("=" * 60)

        for i, place in enumerate(places, 1):
            print(f"[{i}/{total}] {place['name']}", end="")

            result = self.geocode_single(place)

            if result:
                results.append(result)
                print(f" -> ({result['longitude']:.4f}, {result['latitude']:.4f})")
            else:
                print(" -> 失败")

            # 控制请求频率
            if delay > 0 and i < total:
                time.sleep(delay)

        # 打印统计
        print("\n" + "=" * 60)
        print(f"编码完成:")
        print(f"  总请求数: {self.request_count}")
        print(f"  成功: {self.success_count}")
        print(f"  失败: {self.fail_count}")
        print(f"  成功率: {(self.success_count / total * 100):.1f}%")

        return results

    def save_results(self, results: List[Dict], output_dir: str = None) -> str:
        """
        保存编码结果

        Args:
            results: 编码结果列表
            output_dir: 输出目录

        Returns:
            输出文件路径
        """
        if output_dir is None:
            output_dir = os.path.dirname(__file__)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = os.path.join(output_dir, f'geocoded_results_{timestamp}.json')

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        print(f"\n结果已保存: {output_file}")
        return output_file


def load_and_geocode(input_file: str, api_key: str = None) -> List[Dict]:
    """
    加载待编码地点并进行编码

    Args:
        input_file: 输入文件路径（JSON 格式）
        api_key: 高德 API Key

    Returns:
        编码结果列表
    """
    # 读取输入文件
    with open(input_file, 'r', encoding='utf-8') as f:
        places = json.load(f)

    print(f"加载 {len(places)} 个待编码地点")

    # 创建编码器
    geocoder = AmapGeocoder(api_key)

    # 批量编码
    results = geocoder.batch_geocode(places)

    # 保存结果
    if results:
        geocoder.save_results(results)

    return results


if __name__ == '__main__':
    import sys

    # 从命令行参数获取输入文件和 API Key
    if len(sys.argv) < 2:
        print("用法: python geocode_amap.py <input_file> [api_key]")
        print("示例: python geocode_amap.py unmapped_places.json YOUR_API_KEY")
        sys.exit(1)

    input_file = sys.argv[1]
    api_key = sys.argv[2] if len(sys.argv) > 2 else None

    # 执行编码
    results = load_and_geocode(input_file, api_key)
