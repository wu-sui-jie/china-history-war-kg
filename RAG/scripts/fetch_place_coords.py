"""为旧库地点批量获取高德坐标（RAGv5 数据准备工具，复用旧项目 geocoding 编码器）。

为什么需要它：旧项目 `entity-event-relation/src/geocoding/` 已有完整工具链
（export → geocode → review → import），但其 `batch_geocode` **只在整批跑完才落盘、无断点续跑**，
高德个人开发者日额度有限（地理编码 5000 次/日），中途中断等于白烧额度。本脚本只做两件事：

1. **断点续跑**：每调用一条就追加写入 `coords_progress.jsonl`，中断后可继续；
2. **配额保护**：命中"日额度/鉴权"类错误码立即停止并报告，不把剩余额度烧在必然失败的请求上。

复用而不修改旧代码（只继承其 `AmapGeocoder` 取检索名/地址构造/会话），也不写旧库——
写库仍走旧项目自己的 `import` 步骤（需用户确认后执行）。

用法：
  python scripts/fetch_place_coords.py --dry-run                # 看本轮会处理哪些（不调 API）
  python scripts/fetch_place_coords.py --limit 1000             # 跑 1000 条（默认）
  python scripts/fetch_place_coords.py --limit 1000 --retry-failed

密钥：读取顺序 `AMAP_API_KEY` 环境变量 → `RAG/.env` 的 `AMAP_API_KEY` → `--api-key`
（`.env` 已在 .gitignore 中，推荐写在那里；脚本永不打印密钥）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GEOCODER_SRC = RAG_ROOT.parent / "entity-event-relation" / "src"
CACHE_DIR = RAG_ROOT / "data" / "cache" / "amap"

# 出现即停止：鉴权/权限/日额度类，继续跑只会继续失败
STOP_CODES = {
    "10001": "key 失效或未启用",
    "10002": "服务未授权（key 未开通 Web 服务）",
    "10003": "日访问量已达上限",
    "10044": "日调用量超限",
    "10005": "IP 白名单限制",
    "10009": "key 与绑定的平台不符",
}
# 出现即退避重试一次：QPS 类
QPS_CODES = {"10019", "10020", "10021", "10022", "10014"}


def _load_api_key(explicit: str | None, geocoder_src: Path) -> str:
    """密钥顺序：--api-key → AMAP_API_KEY 环境变量 → RAG/.env → 旧项目 geocoding/.env。

    最后一条是旧项目自己的约定位置（`AmapGeocoder` 默认就读它），通常无需另配。
    本函数只读取，任何日志与异常都不得回显密钥内容。
    """
    if explicit:
        return explicit.strip()
    import os

    env = (os.environ.get("AMAP_API_KEY") or "").strip()
    if env:
        return env
    for env_file in (RAG_ROOT / ".env", geocoder_src / "geocoding" / ".env"):
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if s.startswith("AMAP_API_KEY="):
                val = s.split("=", 1)[1].strip().strip('"').strip("'")
                if val and val != "your_api_key_here":
                    return val
    return ""


def _load_geocoder(src_dir: Path):
    if not (src_dir / "geocoding" / "geocode_amap.py").exists():
        raise SystemExit(f"未找到旧项目编码器：{src_dir / 'geocoding'}")
    sys.path.insert(0, str(src_dir))
    from geocoding.geocode_amap import AmapGeocoder  # noqa: PLC0415

    return AmapGeocoder


def build_fetcher(AmapGeocoder, api_key: str):
    """继承旧编码器：父类失败时只打印 infocode，这里要拿到它才能做配额判断。"""

    class Fetcher(AmapGeocoder):
        def fetch(self, place: dict):
            name, name_source = self.get_search_name(place)
            if not name:
                return None, "no_search_name", None
            address = self.build_address(place, name)
            params = {"key": self.api_key, "address": address, "output": "JSON"}
            if place.get("city"):
                params["city"] = place["city"]
            try:
                resp = self.session.get(self.base_url, params=params, timeout=10)
                data = resp.json()
            except Exception as exc:  # noqa: BLE001  网络类失败可重试
                return None, f"network:{type(exc).__name__}", None
            self.request_count += 1
            if data.get("status") == "1" and data.get("geocodes"):
                g = data["geocodes"][0]
                lng, lat = (float(x) for x in g["location"].split(","))
                self.success_count += 1
                return {
                    "place_id": place["id"],
                    "original_name": place["name"],
                    "longitude": lng,
                    "latitude": lat,
                    "search_name": name,
                    "name_source": name_source,
                    "formatted_address": g.get("formatted_address", ""),
                    "confidence": g.get("level", ""),
                    "source": "amap",
                }, "ok", None
            self.fail_count += 1
            code = str(data.get("infocode", "unknown"))
            return None, f"api:{code}", code

    return Fetcher(api_key)


def _load_progress(path: Path) -> dict:
    """读进度（JSONL，一行一条）：按 group_key 建索引，同一目标的最后一次记录生效。"""
    done: dict = {}
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = rec.get("group_key")
        if key is None:
            continue
        done[key] = rec
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description="批量获取高德坐标（断点续跑 + 配额保护）")
    ap.add_argument("--input", default=None, help="待编码清单（默认取 data/cache/amap 下最新的 unmapped_places_*.json）")
    ap.add_argument("--progress", default=str(CACHE_DIR / "coords_progress.jsonl"),
                    help="进度文件（逐条追加；相同 place_id 以最后一次为准）")
    ap.add_argument("--limit", type=int, default=1000, help="本轮最多调用多少条（默认 1000）")
    ap.add_argument("--group-by", choices=["row", "name", "name-province"], default="name-province",
                    help="去重口径（默认 name-province）：同一地名+同一省份只调用一次，"
                         "坐标写回该组所有行（地名相同的多为跨朝代重复行，按名字匹配即可共用坐标）")
    ap.add_argument("--api-key", default=None, help="高德 key（默认读 AMAP_API_KEY 环境变量或 RAG/.env）")
    ap.add_argument("--geocoder-src", default=str(DEFAULT_GEOCODER_SRC), help="旧项目 src 目录")
    ap.add_argument("--retry-failed", action="store_true", help="重试此前失败（非配额类）的条目")
    ap.add_argument("--min-richness", type=int, default=0, choices=[0, 1, 2, 3],
                    help="只处理地址线索齐全度≥N 的目标（0=全试；实测无线索的古地名多为 30001 引擎无结果，"
                         "额度紧张时用 --min-richness 1 先保命中率）")
    ap.add_argument("--dry-run", action="store_true", help="只列本轮要处理的前若干条，不调 API")
    args = ap.parse_args()

    cache = CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    if args.input:
        input_file = Path(args.input)
    else:
        cands = sorted(cache.glob("unmapped_places_*.json"))
        if not cands:
            raise SystemExit(f"未找到待编码清单（先跑 export，见本文件头部说明）：{cache}")
        input_file = cands[-1]
    places = json.loads(input_file.read_text(encoding="utf-8"))

    def _key(p: dict) -> str:
        if args.group_by == "row":
            return str(p.get("id"))
        if args.group_by == "name":
            return p.get("name") or ""
        return f"{p.get('name') or ''}|{(p.get('province') or '').strip()}"

    def _richness(p: dict) -> int:
        return sum(bool((p.get(k) or "").strip()) for k in ("modern_name", "province", "city"))

    groups: dict[str, list[dict]] = {}
    for p in places:
        groups.setdefault(_key(p), []).append(p)
    # 每组取"地址字段最全"的那一行发起编码；坐标按组内名字匹配即可共用
    targets = [(k, max(rows, key=_richness), rows) for k, rows in groups.items()]

    progress_path = Path(args.progress)
    done = _load_progress(progress_path)
    succeeded = {k for k, r in done.items() if r.get("code") == "ok"}
    failed = {k for k, r in done.items() if r.get("code") != "ok"}
    todo = [(k, best, rows) for k, best, rows in targets
            if k not in succeeded and (k not in failed or args.retry_failed)
            and _richness(best) >= args.min_richness]
    # 地址线索越全越可能命中（高德对"无省无市的古地名"多返回 30001）：先花在命中率高的目标上
    todo.sort(key=lambda item: (-_richness(item[1]), item[0]))

    print(f"清单: {input_file.name}（{len(places)} 行）")
    print(f"去重口径 {args.group_by} → 编码目标 {len(targets)} 个")
    print(f"已完成: 成功 {len(succeeded)} / 失败 {len(failed)}（失败条目用 --retry-failed 重试）")
    print(f"本轮待处理: {len(todo)} 个，上限 {args.limit}（按地址线索齐全度降序）")

    if args.dry_run:
        for k, best, rows in todo[: min(args.limit, 10)]:
            print(f"  - {k}（{len(rows)} 行）| modern={best.get('modern_name')!r} "
                  f"{best.get('province')!r} {best.get('city')!r}")
        print(f"（dry-run：实际会处理前 {min(args.limit, len(todo))} 个目标）")
        return 0

    AmapGeocoder = _load_geocoder(Path(args.geocoder_src))
    api_key = _load_api_key(args.api_key, Path(args.geocoder_src))
    fetcher = build_fetcher(AmapGeocoder, api_key)
    if not fetcher.api_key:
        raise SystemExit(
            "未读到高德 key。请在 RAG/.env 写入一行 AMAP_API_KEY=你的key（该文件不入库）、"
            "或设置环境变量 AMAP_API_KEY、或传 --api-key。"
        )

    used = ok = fail = 0
    qps_retries = 0
    stop_reason = ""
    t0 = time.time()
    base_delay = 0.35                                 # ≈3 次/秒：实测免费账号 QPS 限制比旧工具假定的 20 次/秒低
    with open(progress_path, "a", encoding="utf-8") as fp:
        for key, place, rows in todo[: args.limit]:
            result, code, info, calls = None, "", None, 0
            for attempt in range(3):                  # QPS 类失败原地退避重试，不把目标记成失败
                result, code, info = fetcher.fetch(place)
                calls += 1
                if not (code.startswith("api:") and info in QPS_CODES and result is None):
                    break
                qps_retries += 1
                time.sleep(1.5 * (attempt + 1))
            used += calls
            rec = {"group_key": key, "row_ids": [r.get("id") for r in rows],
                   "name": place.get("name"), "code": code, "calls": calls,
                   "info_code": info, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            if result:
                rec.update(result)
                ok += 1
            else:
                fail += 1
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fp.flush()
            if info in STOP_CODES:
                stop_reason = f"{info}（{STOP_CODES[info]}）"
                break
            if used % 100 == 0:
                print(f"  进度 调用 {used} 次（成功 {ok} / 失败 {fail} / QPS 重试 {qps_retries}，"
                      f"{time.time() - t0:.0f}s）", flush=True)
            time.sleep(base_delay)

    print("\n=== 本轮结束 ===")
    print(f"调用 {used} 次（含 QPS 重试 {qps_retries}）| 成功 {ok} | 失败 {fail} | 用时 {time.time() - t0:.0f}s")
    if stop_reason:
        print(f"⚠ 因配额/鉴权问题提前停止：{stop_reason}")
        print("  → 明天额度重置后用同一命令继续（进度已落盘，不会重复消耗）：")
        print(f"     python scripts/fetch_place_coords.py --limit 1000")
    print(f"进度文件: {progress_path}")
    print("下一步（需确认后执行）：用 progress 中的成功条目生成审核文件 → 旧项目 import 写回 places 表")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
