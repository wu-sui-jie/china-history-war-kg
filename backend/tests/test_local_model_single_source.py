"""本机 Ollama 模型名只能有一处来源。

**为什么要钉。** 这个名字原先散在 6 个地方：`llm_pipeline.OllamaAdapter` 的默认参数、
`llm_pipeline.stream_inference` 里硬编码的 `model='deepseek-r1:7b'`、两个问答接口与单例里
构造 `RuleLLMIntegration(model_name=...)`、`entity_extract.Extractor` 的默认参数、
`inference.rule_llm_integration.RuleLLMIntegration` 的默认参数。换模型要改 6 处，
**漏一处就是"一半走新模型、一半走旧模型"，而且日志里看不出来**——
`stream_inference` 那处尤其隐蔽：调用方传了 `model_name` 也不生效，因为请求里写死了。

现在只有 `local_settings.OLLAMA_MODEL` 一处（取值顺序：环境变量 `OLLAMA_MODEL`
→ `backend/.env` → 默认值）。本文件钉两件事：

1. 各模块用的是**同一个对象**（从 local_settings 导入，不是各自抄一份字面量）；
2. 构造函数读的是那个模块级常量、适配器读的是同一处——改常量即改行为，说明没写死。

注意范围：这只管**本机 Ollama**（旧问答链路）。文本实体识别的模型名在
`entity-event-relation/config/.env` 的 `DEEPSEEK_MODEL`（现为 `deepseek-flash`），
RAG 问答在自己的 `.env`——三条链路各读一份，别混。
"""

import local_settings
import llm_pipeline
from entity_extract import extractor as extractor_module
from inference import rule_llm_integration as rule_module


def test_各模块的模型名常量指向同一处():
    assert extractor_module.OLLAMA_MODEL is local_settings.OLLAMA_MODEL
    assert rule_module.OLLAMA_MODEL is local_settings.OLLAMA_MODEL
    assert llm_pipeline.OLLAMA_MODEL is local_settings.OLLAMA_MODEL


def test_构造函数读的是那一处常量而不是字面量(monkeypatch):
    monkeypatch.setattr(extractor_module, "OLLAMA_MODEL", "本机测试模型")
    assert extractor_module.Extractor().model_name == "本机测试模型"

    monkeypatch.setattr(rule_module, "OLLAMA_MODEL", "本机测试模型")
    engine = rule_module.RuleLLMIntegration(rule_file_path="rules/rule_base.json")
    assert engine.model_name == "本机测试模型"

    monkeypatch.setattr(llm_pipeline, "OLLAMA_MODEL", "本机测试模型")
    assert llm_pipeline.OllamaAdapter().model == "本机测试模型"


def test_显式传入时以传入值为准():
    """默认值取自配置，但调用方仍可覆盖——这是"留后路"，不是被写死。"""
    assert extractor_module.Extractor(model_name="显式模型").model_name == "显式模型"
    engine = rule_module.RuleLLMIntegration(rule_file_path="rules/rule_base.json",
                                            model_name="显式模型")
    assert engine.model_name == "显式模型"
    assert llm_pipeline.OllamaAdapter("显式模型").model == "显式模型"
