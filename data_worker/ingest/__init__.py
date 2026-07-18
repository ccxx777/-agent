"""文档入库流水线。

处理顺序固定为：Fingerprint → Loader → Chunker → Embedder → Writer。每个模块
只实现一个阶段，``service`` 负责顺序编排和统一结果格式。
"""
