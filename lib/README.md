# lib

无业务小工具，供 scripts / data 各层 / contracts 复用。

## 文件

| 文件 | 说明 |
| --- | --- |
| `versions.py` | 治理版本号生成、版本目录发现、版本一致性比较。 |
| `json_io.py` | 带 `ensure_ascii=False` + 缩进的 JSON 读写，路径自动建目录。 |
| `logging_util.py` | 统一日志：控制台 + `logs/` 滚动文件。 |

## 约定

- 本层不 import contracts / config 以外的业务模块，保持无环。
- 不承载任何业务规则。
