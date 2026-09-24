"""抽取链（war_extraction）常驻测试的公共夹具。

只做一件事：保证 `import war_extraction` 在两种跑法下都能解析。

- CI 里装的是可编辑包（`pip install -e entity-event-relation`），路径本来就有；
- 本机直接在 entity-event-relation/ 下跑时，靠 pytest 把 rootdir 放进 sys.path；
- 但从仓库根跑（`python -m pytest entity-event-relation/tests`）时上面两条都不成立，
  所以这里显式把模块根塞进 sys.path——与 backend/tests/conftest.py 的做法一致。
"""

import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))
