"""A-3：日配额截断时，pipeline 必须让"只跑了一半"这件事可见。

抽取层刻意保留"日配额耗尽即停整批"的行为（当天不会再成功，继续跑只是把剩余地点全刷成失败），
但**调用方**——审核与导入——看到的是一份部分结果。此前 `run_full_pipeline` 拿到截断结果后
照常往下走，于是"跑完了"的错觉会带着半份结果入库。这里的用例钉住修后的口径：
打醒目提示，且默认不进入审核 / 导入。

全部用桩件驱动，不联网、不碰数据库。
"""

import json

import pytest

from war_extraction.geocoding import main as geo_main


class _FakeGeocoder:
    """假编码器：本批被日配额截断，但已成功 1 条。"""

    batch_aborted_by_quota = True
    quota_exhausted = True
    last_failure_code = '10003'
    success_count = 1

    def __init__(self, api_key=None):
        self.api_key = api_key

    def batch_geocode(self, places, **kwargs):
        return [{'place_id': 1, 'original_name': '牧野', 'longitude': 116.4,
                 'latitude': 39.9, 'source': 'amap'}]

    def save_results(self, results, output_dir=None):
        path = self.output_dir / 'geocoded_results.json'
        path.write_text(json.dumps(results, ensure_ascii=False), encoding='utf-8')
        return str(path)


@pytest.fixture
def pipeline_env(monkeypatch, tmp_path):
    """把 pipeline 的外部依赖全部换成桩件，返回记录下来副作用的盒子。"""
    seen = {'imported': False, 'reviewed': False}

    places_file = tmp_path / 'unmapped_places.json'
    places_file.write_text(json.dumps([{'id': 1, 'name': '牧野'}], ensure_ascii=False),
                           encoding='utf-8')

    class FakeGeocoder(_FakeGeocoder):
        def __init__(self, api_key=None):
            super().__init__(api_key)
            self.output_dir = tmp_path

    class FakeReviewer:
        def __init__(self, results_file=None):
            seen['reviewed'] = True
            self.approved = [{'place_id': 1}]

        def review_auto_approve(self):
            pass

        def save_approved(self):
            path = tmp_path / 'approved_coordinates.json'
            path.write_text('[]', encoding='utf-8')
            return str(path)

    monkeypatch.setattr(geo_main, 'load_env_file', lambda *a, **k: None)
    monkeypatch.setattr(geo_main, 'get_statistics', lambda *a, **k: {
        'total_places': 2, 'with_coordinates': 1,
        'without_coordinates': 1, 'completion_rate': '50.0%'})
    monkeypatch.setattr(geo_main, 'export_unmapped_places', lambda *a, **k: str(places_file))
    monkeypatch.setattr(geo_main, 'AmapGeocoder', FakeGeocoder)
    monkeypatch.setattr(geo_main, 'GeocodingReviewer', FakeReviewer)
    monkeypatch.setattr(geo_main, 'import_coordinates',
                        lambda *a, **k: seen.__setitem__('imported', True))
    return seen


def test_pipeline_stops_after_quota_truncation(pipeline_env, capsys):
    """被日配额截断：控制台出现提示，且不进入审核 / 导入。"""
    geo_main.run_full_pipeline(mode='auto-approve')

    out = capsys.readouterr().out
    assert '本批被高德日配额截断' in out
    assert '部分结果' in out
    assert pipeline_env['reviewed'] is False, "截断后默认不该继续审核"
    assert pipeline_env['imported'] is False, "截断后默认不该导入"


def test_pipeline_continues_when_partial_results_are_explicitly_allowed(pipeline_env, capsys):
    """明确加了 --allow-partial：提示照打，但流程继续走完。"""
    geo_main.run_full_pipeline(mode='auto-approve', allow_partial=True)

    out = capsys.readouterr().out
    assert '本批被高德日配额截断' in out
    assert pipeline_env['reviewed'] is True
    assert pipeline_env['imported'] is True
