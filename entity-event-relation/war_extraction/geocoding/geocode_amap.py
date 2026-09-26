from __future__ import annotations

# -*- coding: utf-8 -*-
"""
高德地图地理编码
使用高德地图 Web 服务 API 进行地理编码
"""

import requests
import json
import glob
import time
import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from .historical_places_mapping import HISTORICAL_MAPPING

#: batch_geocode 的 progress_path 默认值哨兵：表示"用模块目录下的默认进度文件"，
#: 与显式传 None（关闭进度落盘）区分开。
_DEFAULT_PROGRESS = object()

#: 逐条进度文件名模板（放模块目录下，不入库——见 .gitignore）。
#: 文件名带 run_id，**一次跑批一个文件**：共用一个文件、靠记录里的 run_id 区分的话，
#: 文件只增不减；分文件后中断了能一眼看出是哪一批，用完也可整文件丢弃。
PROGRESS_FILENAME_TEMPLATE = 'geocoding_progress_{run_id}.jsonl'


#: 本进程已经发出去过的 run_id。见 new_run_id：同一毫秒内连开两批时靠它去重。
_HANDED_OUT_RUN_IDS = set()


def new_run_id(now: datetime = None) -> str:
    """
    批次标识：本地时间精确到毫秒；同一毫秒内重复调用则补一个序号。

    文件名要靠它保证"一批一个文件"，所以**必须唯一**：只到秒的话同一秒内的两批会撞名，
    又退回成多批共用一个文件；只到毫秒的话测试与脚本里连开两批仍可能撞在同一毫秒。
    """
    now = now or datetime.now()
    base = f"{now.strftime('%Y%m%d_%H%M%S')}_{now.microsecond // 1000:03d}"
    candidate = base
    seq = 1
    while candidate in _HANDED_OUT_RUN_IDS:
        seq += 1
        candidate = f"{base}_{seq:02d}"
    _HANDED_OUT_RUN_IDS.add(candidate)
    return candidate


def default_progress_path(run_id: str = None) -> str:
    """默认的逐条进度文件路径：模块目录下的 ``geocoding_progress_<run_id>.jsonl``。"""
    return os.path.join(os.path.dirname(__file__),
                        PROGRESS_FILENAME_TEMPLATE.format(run_id=run_id or new_run_id()))


def prune_progress_files(keep: int = 20, progress_dir: str = None) -> List[str]:
    """
    进度文件清理口径：按修改时间从新到旧保留 ``keep`` 份，更旧的删掉。

    **不自动调用**——删文件有副作用，留成显式动作：
        python -c "from war_extraction.geocoding.geocode_amap import prune_progress_files; print(prune_progress_files())"

    Returns:
        被删除的文件路径列表
    """
    directory = progress_dir or os.path.dirname(__file__)
    pattern = os.path.join(directory, PROGRESS_FILENAME_TEMPLATE.format(run_id='*'))
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    removed = []
    for path in files[max(0, int(keep)):]:
        try:
            os.remove(path)
            removed.append(path)
        except OSError as e:
            # 被别的进程占着（Windows 上常见）就跳过，下次再清
            print(f"  ! 进度文件删除失败（{path}）: {e}")
    return removed


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

    #: 配额 / 频率受限的错误码。**这些不重试**——高德已经明确说"你超了"，
    #: 退避重试只会白烧额度、把日志刷满。
    #:   10003 访问已超出日访问量   10044 个人日访问量超限（日配额，当天不会再恢复）
    #:   10004 单位时间内访问过于频繁  10021 并发/QPS 超限（限频，稍后可恢复）
    QUOTA_INFOCODES = {'10003', '10004', '10021', '10044'}
    #: 日配额类：当天不会再成功，遇到就终止整批（继续跑只是把剩余地点全部刷失败）
    DAILY_QUOTA_INFOCODES = {'10003', '10044'}
    #: 值得退避重试的瞬时 HTTP 状态（网关抖动、限流）
    TRANSIENT_HTTP_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, api_key: str = None, env_file: str = None,
                 max_retries: int = 3, backoff_base: float = 1.0, sleep_func=None):
        """
        初始化编码器

        Args:
            api_key: 高德 API Key，如果为 None 则从 .env 文件或环境变量读取
            env_file: .env 文件路径
            max_retries: 单条地点最多请求几次（含首次）。只对**瞬时**故障生效
            backoff_base: 退避基数（秒），第 n 次重试前等待 backoff_base * 2^(n-1)
            sleep_func: 等待函数，默认 time.sleep；测试注入用它避免真等
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
        #: 因瞬时故障实际重试过多少次（诊断用）
        self.retry_count = 0
        self.quota_exhausted = False
        #: 本批是否被**日配额**提前截断（而不是单条失败）。
        #: 调用方据此判断拿到的 results 是"全部地点"还是"一部分"。
        self.batch_aborted_by_quota = False
        self.last_failure_reason = ''
        self.last_failure_code = ''
        self.max_retries = max(1, int(max_retries))
        self.backoff_base = max(0.0, float(backoff_base))
        self.sleep_func = sleep_func or time.sleep

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

    def _request_once(self, params: Dict):
        """
        发一次请求，返回 (判定, 载荷)。

        判定取值（把"值得重试"与"重试也没用"分开）：
          - ``'ok'``：拿到结果，载荷是 ``geocodes[0]`` 字典；
          - ``'transient'``：网络异常 / 5xx / 429 / 响应不是 JSON——退避重试可能成功，载荷是原因；
          - ``'quota'``：高德返回配额或频率受限的错误码——重试只会白烧额度，载荷是 infocode；
          - ``'deterministic'``：其它 API 错误（key 无效、地址查不到等）——重试永远不会成功。
        """
        self.request_count += 1
        try:
            response = self.session.get(self.base_url, params=params, timeout=10)
        except requests.RequestException as e:
            return 'transient', f"{type(e).__name__}: {e}"

        if response.status_code in self.TRANSIENT_HTTP_STATUS:
            return 'transient', f"HTTP {response.status_code}"
        if response.status_code != 200:
            return 'deterministic', f"HTTP {response.status_code}"

        try:
            data = response.json()
        except ValueError as e:
            # 200 但body 不是 JSON：多半是中间代理返回了错误页，值得再试一次
            return 'transient', f"响应不是合法 JSON: {e}"

        if data.get('status') == '1' and data.get('geocodes'):
            return 'ok', data['geocodes'][0]

        info_code = str(data.get('infocode', 'unknown'))
        if info_code in self.QUOTA_INFOCODES:
            return 'quota', info_code
        return 'deterministic', info_code

    def _build_result(self, place: Dict, search_name: str, name_source: str, geocode: Dict) -> Dict:
        """把高德返回的 geocode 条目整理成落盘用的结果字典（字段名对外保持不变）。"""
        location = geocode['location'].split(',')
        return {
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

    def geocode_single(self, place: Dict) -> Optional[Dict]:
        """
        单个地点地理编码（含瞬时故障的指数退避重试）

        Args:
            place: 地点信息字典，需包含 id, name, modern_name 等字段

        Returns:
            编码结果字典，失败返回 None。失败原因可从 ``self.last_failure_reason`` 取
        """
        self.last_failure_reason = ''
        self.last_failure_code = ''
        # 获取搜索地名
        search_name, name_source = self.get_search_name(place)
        if not search_name:
            self.last_failure_reason = '无法确定搜索地名'
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

        for attempt in range(1, self.max_retries + 1):
            outcome, payload = self._request_once(params)

            if outcome == 'ok':
                self.success_count += 1
                return self._build_result(place, search_name, name_source, payload)
            if outcome == 'quota':
                self.quota_exhausted = True
                self.fail_count += 1
                self.last_failure_code = str(payload)
                self.last_failure_reason = f"配额/频率受限 (infocode={payload})"
                print(f"  ✗ {place['name']}: 高德配额/频率受限 (infocode={payload})，不重试")
                return None
            if outcome == 'deterministic':
                self.fail_count += 1
                self.last_failure_code = str(payload)
                self.last_failure_reason = f"API 错误 (infocode={payload})"
                print(f"  ✗ {place['name']}: API返回错误 ({payload})")
                return None

            # outcome == 'transient'：只有这一支才值得退避重试
            if attempt < self.max_retries:
                wait = self.backoff_base * (2 ** (attempt - 1))
                self.retry_count += 1
                print(f"  ! {place['name']}: {payload}，{wait:.1f}s 后重试（第 {attempt}/{self.max_retries} 次）")
                self.sleep_func(wait)
                continue

            self.fail_count += 1
            self.last_failure_reason = f"重试 {self.max_retries} 次仍失败 ({payload})"
            print(f"  ✗ {place['name']}: 重试 {self.max_retries} 次仍失败（{payload}）")
            return None

        return None

    def _append_progress(self, progress_path: str, record: Dict):
        """
        追加一条进度记录（JSONL，一行一条）并 fsync。

        不能只在批次末尾 `save_results` 一次性落盘：批次跑到一半被杀（Ctrl-C、断网、
        配额耗尽）就把**整批**已花掉额度的结果丢了。逐条落盘后中途失败最多丢当前这一条。
        用追加 + fsync：写入本身足够原子（一条记录远小于一个块，且崩了最多留一行残行，
        解析时跳过即可）。
        """
        if not progress_path:
            return
        try:
            with open(progress_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            # 进度文件写不进去不该让整批编码失败，提示后继续
            print(f"  ! 进度文件写入失败（{progress_path}）: {e}")

    def batch_geocode(self, places: List[Dict], delay: float = 0.05,
                      progress_path: str = _DEFAULT_PROGRESS, run_id: str = None) -> List[Dict]:
        """
        批量地理编码

        Args:
            places: 地点列表
            delay: 请求间隔（秒），默认 0.05 秒
            progress_path: 逐条进度文件（JSONL）。默认写到本模块目录下的
                geocoding_progress_<run_id>.jsonl；显式传 None 可关闭
            run_id: 本次运行的标识。默认按本地时间到毫秒生成，同时用于**进度文件名**；
                显式传入时若 progress_path 用的是默认值，文件也会带上这个 run_id

        Returns:
            编码结果列表（已完成的部分；被配额中断时是截断的）
        """
        results = []
        total = len(places)

        # 先定 run_id，再据此推导默认进度文件名（一批一个文件）
        if run_id is None:
            run_id = new_run_id()
        if progress_path is _DEFAULT_PROGRESS:
            progress_path = default_progress_path(run_id)

        print(f"\n开始批量编码: {total} 个地点")
        if progress_path:
            print(f"逐条进度: {progress_path}")
        print("=" * 60)

        done = 0
        aborted_by_quota = False

        for i, place in enumerate(places, 1):
            print(f"[{i}/{total}] {place['name']}", end="")

            result = self.geocode_single(place)

            if result:
                results.append(result)
                print(f" -> ({result['longitude']:.4f}, {result['latitude']:.4f})")
            else:
                print(" -> 失败")

            done = i
            record = {'run_id': run_id, 'index': i, 'name': place.get('name')}
            if result:
                record.update(result)
                record['status'] = 'ok'
            else:
                record['status'] = 'failed'
                record['reason'] = self.last_failure_reason or '未知'
            self._append_progress(progress_path, record)

            # 日配额耗尽：当天不会再成功，继续跑只是把剩余地点全部刷成失败
            if self.last_failure_code in self.DAILY_QUOTA_INFOCODES:
                aborted_by_quota = True
                self.batch_aborted_by_quota = True
                print(f"\n! 高德日配额已耗尽（infocode={self.last_failure_code}），提前结束本批"
                      f"（剩余 {total - done} 个未请求）")
                break

            # 控制请求频率
            if delay > 0 and i < total:
                self.sleep_func(delay)

        # 打印统计
        print("\n" + "=" * 60)
        print(f"编码完成:")
        print(f"  总请求数: {self.request_count}（含重试 {self.retry_count} 次）")
        print(f"  成功: {self.success_count}")
        print(f"  失败: {self.fail_count}")
        print(f"  成功率: {(self.success_count / total * 100):.1f}%")
        if done < total:
            print(f"  未处理: {total - done} 个"
                  + ("（日配额耗尽提前结束）" if aborted_by_quota else ""))
        if progress_path:
            print(f"  逐条进度已落盘: {progress_path}")

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


def warn_if_quota_truncated(geocoder: 'AmapGeocoder') -> bool:
    """
    本批被日配额截断时打醒目提示，返回是否被截断。

    编码层刻意保留"日配额耗尽即停整批"的行为（当天不会再成功，继续跑只是把剩余地点
    全刷成失败），但**调用方**看到的是一份部分结果——审核与导入照常进行的话，
    人会以为"这批跑完了"。所以把提示做成显式动作，由调用方决定要不要继续往下走。
    """
    if not geocoder.batch_aborted_by_quota:
        return False
    print("!" * 70)
    print("! 本批被高德日配额截断：接下来审核 / 导入的是**部分结果**")
    print(f"! 配额错误码: {geocoder.last_failure_code}；已成功 {geocoder.success_count} 条，"
          f"剩余地点今日拿不到坐标")
    print("! 补齐办法：明天重跑（已成功的坐标可从编码结果文件复用，不必重复花钱）")
    print("!" * 70)
    return True


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

    # 截断时在结果保存前先说清楚（本步只编码，不停下来）
    warn_if_quota_truncated(geocoder)

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
