"""EER-12：高德地理编码的重试退避、配额区分、逐条落盘。

原来的 `geocode_single` 是一次性请求：网络抖一下就整条失败（`except Exception` 一网打尽、
不重试），而且只有批次末尾的 `save_results` 才落盘——批次跑一半被杀就把**整批**已花掉额度的
结果全丢了。改法是三件事：

1. 只对**瞬时**故障退避重试（网络异常 / 5xx / 429 / 响应不是 JSON），指数退避 max_retries 次；
2. 配额与频率类错误码（10003/10004/10021/10044）**不重试**——高德已经说"你超了"，
   退避重试只会白烧额度；日配额类（10003/10044）还会提前结束整批；
3. 每处理完一个地点就把这一条追加进 JSONL 进度文件并 fsync，中途失败最多丢当前一条。

用例全部用假 session 注入故障，因此不联网、不消耗额度、不真等待（sleep_func 注入）。
"""

import json
from pathlib import Path

import pytest
import requests

from war_extraction.geocoding import geocode_amap
from war_extraction.geocoding.geocode_amap import (
    AmapGeocoder,
    default_progress_path,
)


class _FakeResponse:
    def __init__(self, payload=None, status_code=200, raw_text=None):
        self.status_code = status_code
        self._payload = payload
        self._raw_text = raw_text

    def json(self):
        if self._raw_text is not None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


def _ok_payload(lng="116.4", lat="39.9"):
    return {
        'status': '1',
        'infocode': '10000',
        'geocodes': [{
            'location': f'{lng},{lat}',
            'formatted_address': '测试地址',
            'level': '城市',
        }],
    }


def _err_payload(infocode):
    return {'status': '0', 'info': 'ERROR', 'infocode': str(infocode)}


class _ScriptedSession:
    """按脚本依次返回响应或抛异常。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def sleeps():
    """记录退避等待时长，避免用例真的睡。"""
    return []


@pytest.fixture
def make_geocoder(sleeps):
    def _make(script, **kwargs):
        geocoder = AmapGeocoder(api_key='test-key', sleep_func=sleeps.append, **kwargs)
        geocoder.session = _ScriptedSession(script)
        return geocoder
    return _make


def _place(pid, name='牧野'):
    return {'id': pid, 'name': name, 'modern_name': name}


def test_transient_failure_is_retried_with_exponential_backoff(make_geocoder, sleeps):
    """网络超时属于瞬时故障：退避重试后成功，等待时长按 1s、2s 递增。"""
    geocoder = make_geocoder([
        requests.Timeout("连接超时"),
        requests.ConnectionError("连接被重置"),
        _FakeResponse(_ok_payload()),
    ])

    result = geocoder.geocode_single(_place(1))

    assert result is not None
    assert result['longitude'] == 116.4 and result['latitude'] == 39.9
    assert geocoder.request_count == 3, "三次请求：首次 + 两次重试"
    assert geocoder.retry_count == 2
    assert sleeps == [1.0, 2.0], f"退避应为 1s、2s，实际 {sleeps}"


def test_transient_5xx_is_retried(make_geocoder, sleeps):
    """5xx 也按瞬时故障处理。"""
    geocoder = make_geocoder([
        _FakeResponse(status_code=503),
        _FakeResponse(_ok_payload()),
    ])

    assert geocoder.geocode_single(_place(1)) is not None
    assert geocoder.request_count == 2
    assert sleeps == [1.0]


def test_gives_up_after_max_retries(make_geocoder, sleeps):
    """瞬时故障一直不好：请求 max_retries 次后放弃，等待次数是 max_retries-1。"""
    geocoder = make_geocoder([requests.Timeout("一直超时")], max_retries=3)

    assert geocoder.geocode_single(_place(1)) is None
    assert geocoder.request_count == 3
    assert geocoder.retry_count == 2
    assert len(sleeps) == 2
    assert '重试' in geocoder.last_failure_reason


def test_quota_error_is_not_retried(make_geocoder, sleeps):
    """日配额耗尽（10044）：一次都不重试，并标记配额耗尽。"""
    geocoder = make_geocoder([_FakeResponse(_err_payload(10044))])

    assert geocoder.geocode_single(_place(1)) is None
    assert geocoder.request_count == 1, "配额错误重试只会白烧额度"
    assert geocoder.retry_count == 0
    assert sleeps == []
    assert geocoder.quota_exhausted is True
    assert '10044' in geocoder.last_failure_reason


def test_rate_limit_error_is_quota_class(make_geocoder):
    """频率受限（10004）同属配额类：不重试（它会在稍后恢复，但不是靠立刻重试）。"""
    geocoder = make_geocoder([_FakeResponse(_err_payload(10004))])

    assert geocoder.geocode_single(_place(1)) is None
    assert geocoder.request_count == 1
    assert geocoder.quota_exhausted is True


def test_deterministic_error_is_not_retried(make_geocoder, sleeps):
    """key 无效（10001）是确定性失败：重试永远不会成功，所以不重试。"""
    geocoder = make_geocoder([_FakeResponse(_err_payload(10001))])

    assert geocoder.geocode_single(_place(1)) is None
    assert geocoder.request_count == 1
    assert geocoder.retry_count == 0
    assert sleeps == []
    assert geocoder.quota_exhausted is False


def test_batch_writes_progress_per_item(make_geocoder, tmp_path):
    """每处理完一条就落盘一条，且字段与最终结果一致。"""
    progress = tmp_path / "progress.jsonl"
    geocoder = make_geocoder([_FakeResponse(_ok_payload())])
    geocoder._request_once = lambda params: ('ok', _ok_payload()['geocodes'][0])

    results = geocoder.batch_geocode(
        [_place(1, '牧野'), _place(2, '涿鹿')], delay=0, progress_path=str(progress), run_id='run-A')

    assert len(results) == 2
    lines = [json.loads(line) for line in progress.read_text(encoding='utf-8').splitlines()]
    assert [line['status'] for line in lines] == ['ok', 'ok']
    assert [line['place_id'] for line in lines] == [1, 2]
    assert all(line['run_id'] == 'run-A' for line in lines)
    assert lines[0]['longitude'] == 116.4


def test_batch_keeps_partial_results_when_daily_quota_aborts(make_geocoder, tmp_path):
    """日配额耗尽时提前结束：已成功的那几条必须留在返回值和进度文件里。"""
    progress = tmp_path / "progress.jsonl"
    geocoder = make_geocoder([_FakeResponse(_ok_payload())])

    calls = {'n': 0}

    def scripted_request(params):
        calls['n'] += 1
        if calls['n'] == 1:
            return 'ok', _ok_payload()['geocodes'][0]
        return 'quota', '10003'

    geocoder._request_once = scripted_request

    places = [_place(1, '牧原'), _place(2, '牧野'), _place(3, '牧野2')]
    results = geocoder.batch_geocode(places, delay=0, progress_path=str(progress), run_id='run-B')

    assert len(results) == 1, "日配额耗尽前成功的那一条不能丢"
    assert results[0]['place_id'] == 1
    assert calls['n'] == 2, "配额耗尽后剩余地点不应再发请求"

    lines = [json.loads(line) for line in progress.read_text(encoding='utf-8').splitlines()]
    assert [line['status'] for line in lines] == ['ok', 'failed']
    assert '10003' in lines[1]['reason']
    assert len(lines) == 2, "第 3 个地点从未被处理，不该出现在进度里"


def test_rate_limit_does_not_abort_batch(make_geocoder, tmp_path):
    """频率受限（10004）不提前结束——它稍后会恢复，剩下的地点还要试。"""
    progress = tmp_path / "progress.jsonl"
    geocoder = make_geocoder([_FakeResponse(_ok_payload())])

    calls = {'n': 0}

    def scripted_request(params):
        calls['n'] += 1
        if calls['n'] == 1:
            return 'ok', _ok_payload()['geocodes'][0]
        if calls['n'] == 2:
            return 'quota', '10004'
        return 'ok', _ok_payload('120.1', '30.2')['geocodes'][0]

    geocoder._request_once = scripted_request

    results = geocoder.batch_geocode(
        [_place(1), _place(2), _place(3)], delay=0, progress_path=str(progress))

    assert calls['n'] == 3, "频率受限后仍应继续处理剩余地点"
    assert [r['place_id'] for r in results] == [1, 3]
    lines = [json.loads(line) for line in progress.read_text(encoding='utf-8').splitlines()]
    assert [line['status'] for line in lines] == ['ok', 'failed', 'ok']


def test_progress_can_be_disabled(make_geocoder, tmp_path):
    """显式传 progress_path=None 时不落盘。"""
    geocoder = make_geocoder([_FakeResponse(_ok_payload())])
    geocoder._request_once = lambda params: ('ok', _ok_payload()['geocodes'][0])

    geocoder.batch_geocode([_place(1)], delay=0, progress_path=None)

    assert list(tmp_path.glob("*.jsonl")) == []


def test_default_progress_path_is_in_module_dir_and_carries_run_id():
    """默认进度文件固定在模块目录下，文件名带 run_id（A-2：一批一个文件）。"""
    geocoding_dir = Path(__file__).resolve().parents[1] / "war_extraction" / "geocoding"

    path = Path(default_progress_path('20260925_070325_001'))

    assert path.parent == geocoding_dir
    assert path.name == 'geocoding_progress_20260925_070325_001.jsonl'


def test_default_progress_path_is_unique_per_call():
    """连续两次推导必须给出不同文件名——同一秒/同一毫秒内连开两批也不能撞名。"""
    first = Path(default_progress_path()).name
    second = Path(default_progress_path()).name

    assert first != second, f"两批落到同一个进度文件：{first}"
    assert first.startswith('geocoding_progress_') and first.endswith('.jsonl')


def test_batch_geocode_writes_default_progress_file_next_to_module(monkeypatch, make_geocoder, tmp_path):
    """不传 progress_path 时，进度确实落到默认路径（A-4：补上 sentinel 分支的端到端断言）。

    用 monkeypatch 把模块级 default_progress_path 指到 tmp_path：既真跑通了
    "sentinel → 默认路径" 这一支，又不往仓库目录里写文件。
    """
    geocoder = make_geocoder([_FakeResponse(_ok_payload())])
    geocoder._request_once = lambda params: ('ok', _ok_payload()['geocodes'][0])

    def fake_default_progress_path(run_id=None):
        return str(tmp_path / f"geocoding_progress_{run_id or 'noid'}.jsonl")

    monkeypatch.setattr(geocode_amap, 'default_progress_path', fake_default_progress_path)

    results = geocoder.batch_geocode([_place(1), _place(2)], delay=0, run_id='run-X')

    assert len(results) == 2
    written = list(tmp_path.glob("geocoding_progress_*.jsonl"))
    assert len(written) == 1, f"应恰好写出一份默认进度文件，实际 {written}"
    assert written[0].name == 'geocoding_progress_run-X.jsonl'
    lines = [json.loads(line) for line in written[0].read_text(encoding='utf-8').splitlines()]
    assert [line['status'] for line in lines] == ['ok', 'ok']


def test_two_default_batches_produce_two_files(monkeypatch, make_geocoder, tmp_path):
    """连跑两批默认进度 → 两个文件，各自只含本批记录（A-2 验收）。"""
    monkeypatch.setattr(geocode_amap, 'default_progress_path',
                        lambda run_id=None: str(tmp_path / f"geocoding_progress_{run_id}.jsonl"))

    for name in ('牧野', '涿鹿'):
        geocoder = make_geocoder([_FakeResponse(_ok_payload())])
        geocoder._request_once = lambda params: ('ok', _ok_payload()['geocodes'][0])
        geocoder.batch_geocode([_place(1, name)], delay=0)

    written = sorted(tmp_path.glob("geocoding_progress_*.jsonl"))
    assert len(written) == 2, f"两批应各写一个文件，实际 {written}"
    for path in written:
        lines = path.read_text(encoding='utf-8').splitlines()
        assert len(lines) == 1, f"{path.name} 只应含本批那一条记录"


def test_quota_abort_marks_the_batch(make_geocoder):
    """日配额截断时置 batch_aborted_by_quota（A-3：调用方据此判断拿到的是部分结果）。"""
    geocoder = make_geocoder([_FakeResponse(_ok_payload())])
    geocoder._request_once = lambda params: ('quota', '10003')

    geocoder.batch_geocode([_place(1)], delay=0, progress_path=None)

    assert geocoder.batch_aborted_by_quota is True


def test_deterministic_failure_does_not_mark_batch_as_truncated(make_geocoder):
    """单条确定性失败不是"本批被截断"——别让调用方误判成部分结果。"""
    geocoder = make_geocoder([_FakeResponse(_err_payload(10001))])

    geocoder.batch_geocode([_place(1)], delay=0, progress_path=None)

    assert geocoder.batch_aborted_by_quota is False
