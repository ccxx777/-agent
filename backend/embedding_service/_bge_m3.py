"""BGE-M3 推理核心 — 从 FlagEmbedding 提取，只保留 encode 逻辑。

外部依赖（5 个）:
  - torch
  - transformers (AutoModel, AutoTokenizer)
  - numpy
  - tqdm
  - huggingface_hub (模型下载)

用法:
    from _bge_m3 import BGEM3Embedder
    model = BGEM3Embedder("/app/models/bge-m3", use_fp16=False, device="cpu")
    output = model.encode(["text"], return_dense=True, return_sparse=True)
    # output["dense_vecs"] -> np.ndarray  (N, 1024)
    # output["lexical_weights"] -> list[dict[int, float]]
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Union

import numpy as np
import torch
import torch.nn.functional as F
from tqdm import trange

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# 模型加载
# ══════════════════════════════════════════════════════════════════


def _load_model_components(
    model_name_or_path: str,
    colbert_dim: int = -1,
    torch_dtype: Optional[torch.dtype] = None,
) -> Dict[str, Any]:
    """加载 XLMRobertaModel + colbert_linear + sparse_linear 三个组件。"""
    from huggingface_hub import snapshot_download
    from transformers import AutoModel

    cache_folder = os.getenv("HF_HUB_CACHE", None)

    if not os.path.exists(model_name_or_path):
        model_name_or_path = snapshot_download(
            repo_id=model_name_or_path,
            cache_dir=cache_folder,
            ignore_patterns=["flax_model.msgpack", "rust_model.ot", "tf_model.h5"],
        )

    model = AutoModel.from_pretrained(
        model_name_or_path,
        cache_dir=cache_folder,
        trust_remote_code=False,
    )
    if torch_dtype is not None:
        model = model.to(dtype=torch_dtype)

    hidden_size = model.config.hidden_size
    colbert_linear = torch.nn.Linear(
        in_features=hidden_size,
        out_features=hidden_size if colbert_dim <= 0 else colbert_dim,
        dtype=torch_dtype,
    )
    sparse_linear = torch.nn.Linear(
        in_features=hidden_size, out_features=1, dtype=torch_dtype
    )

    colbert_path = os.path.join(model_name_or_path, "colbert_linear.pt")
    sparse_path = os.path.join(model_name_or_path, "sparse_linear.pt")
    if os.path.exists(colbert_path) and os.path.exists(sparse_path):
        logger.info("Loading colbert_linear.pt and sparse_linear.pt weights")
        colbert_linear.load_state_dict(torch.load(colbert_path, map_location="cpu", weights_only=True))
        sparse_linear.load_state_dict(torch.load(sparse_path, map_location="cpu", weights_only=True))

    return {"model": model, "colbert_linear": colbert_linear, "sparse_linear": sparse_linear}


# ══════════════════════════════════════════════════════════════════
# 模型前向传播 (EncoderOnlyEmbedderM3ModelForInference)
# ══════════════════════════════════════════════════════════════════


class _M3InferenceModel:
    """封装 XLMRoberta + colbert/sparse 线性层的前向传播。"""

    def __init__(
        self,
        model: torch.nn.Module,
        colbert_linear: torch.nn.Module,
        sparse_linear: torch.nn.Module,
        tokenizer: Any,
        sentence_pooling_method: str = "cls",
        normalize_embeddings: bool = True,
    ):
        self.model = model
        self.colbert_linear = colbert_linear
        self.sparse_linear = sparse_linear
        self.tokenizer = tokenizer
        self.pooling_method = sentence_pooling_method
        self.normalize_embeddings = normalize_embeddings
        self.vocab_size = model.config.vocab_size

    def _dense_embedding(self, last_hidden_state: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        if self.pooling_method == "cls":
            return last_hidden_state[:, 0]
        elif self.pooling_method == "mean":
            s = torch.sum(last_hidden_state * attention_mask.unsqueeze(-1).float(), dim=1)
            d = attention_mask.sum(dim=1, keepdim=True).float()
            return s / d
        elif self.pooling_method == "last_token":
            left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]
            if left_padding:
                return last_hidden_state[:, -1]
            sequence_lengths = attention_mask.sum(dim=1) - 1
            batch_size = last_hidden_state.shape[0]
            return last_hidden_state[torch.arange(batch_size, device=last_hidden_state.device), sequence_lengths]
        else:
            raise ValueError(f"Unknown pooling method: {self.pooling_method}")

    def _sparse_embedding(self, hidden_state: torch.Tensor, input_ids: torch.Tensor) -> torch.Tensor:
        """返�� token_weights (B, seq_len, 1)，非 aggregated sparse embedding。"""
        token_weights = torch.relu(self.sparse_linear(hidden_state))
        return token_weights

    def forward(
        self,
        text_input: Dict[str, torch.Tensor],
        return_dense: bool = True,
        return_sparse: bool = False,
        return_colbert_vecs: bool = False,
        truncate_dim: Optional[int] = None,
    ) -> Dict[str, torch.Tensor]:
        last_hidden_state = self.model(**text_input, return_dict=True).last_hidden_state

        output: Dict[str, torch.Tensor] = {}
        if return_dense:
            dense = self._dense_embedding(last_hidden_state, text_input["attention_mask"])
            if truncate_dim is not None:
                dense = dense[..., :truncate_dim]
            output["dense_vecs"] = dense
        if return_sparse:
            output["sparse_vecs"] = self._sparse_embedding(last_hidden_state, text_input["input_ids"])
        if return_colbert_vecs:
            colbert = self.colbert_linear(last_hidden_state[:, 1:])
            colbert = colbert * text_input["attention_mask"][:, 1:][:, :, None].float()
            if truncate_dim is not None:
                colbert = colbert[..., :truncate_dim]
            output["colbert_vecs"] = colbert

        if self.normalize_embeddings:
            if "dense_vecs" in output:
                output["dense_vecs"] = F.normalize(output["dense_vecs"], dim=-1)

        return output


# ══════════════════════════════════════════════════════════════════
# BGEM3Embedder — 主入口
# ══════════════════════════════════════════════════════════════════


class BGEM3Embedder:
    """BGE-M3 推理器，提供 dense + sparse 编码。"""

    def __init__(
        self,
        model_name_or_path: str,
        normalize_embeddings: bool = True,
        use_fp16: bool = False,
        device: str = "cpu",
        pooling_method: str = "cls",
        batch_size: int = 256,
        max_length: int = 512,
    ):
        from transformers import AutoTokenizer

        self.model_name_or_path = model_name_or_path
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self.use_fp16 = use_fp16

        torch_dtype = torch.float16 if use_fp16 else torch.float32

        components = _load_model_components(model_name_or_path, torch_dtype=torch_dtype)

        self.tokenizer = AutoTokenizer.from_pretrained(model_name_or_path)

        self._inference_model = _M3InferenceModel(
            model=components["model"],
            colbert_linear=components["colbert_linear"],
            sparse_linear=components["sparse_linear"],
            tokenizer=self.tokenizer,
            sentence_pooling_method=pooling_method,
            normalize_embeddings=normalize_embeddings,
        )

        if device == "cpu":
            self._inference_model.model.float()
            self._inference_model.colbert_linear.float()
            self._inference_model.sparse_linear.float()

        self._inference_model.model.to(device)
        self._inference_model.colbert_linear.to(device)
        self._inference_model.sparse_linear.to(device)
        self._inference_model.model.eval()

    @torch.no_grad()
    def encode(
        self,
        sentences: Union[List[str], str],
        batch_size: Optional[int] = None,
        max_length: Optional[int] = None,
        return_dense: bool = True,
        return_sparse: bool = False,
        return_colbert_vecs: bool = False,
    ) -> Dict[str, Any]:
        """编码句子，返回 dense + sparse 向量。

        Returns:
            dict:
                "dense_vecs": np.ndarray (N, 1024)
                "lexical_weights": list[dict[int, float]]
                "colbert_vecs": list[np.ndarray] | None
        """
        if batch_size is None:
            batch_size = self.batch_size
        if max_length is None:
            max_length = self.max_length

        input_was_string = isinstance(sentences, str)
        if input_was_string:
            sentences = [sentences]

        # ── Tokenize（不 padding，先获取每句长度） ──
        all_inputs = []
        for start in range(0, len(sentences), batch_size):
            batch = sentences[start : start + batch_size]
            inputs_batch = self.tokenizer(batch, truncation=True, max_length=max_length)
            all_inputs.extend([
                {k: inputs_batch[k][i] for k in inputs_batch}
                for i in range(len(batch))
            ])

        # 按长度排序以减少 padding 浪费
        length_sorted_idx = np.argsort([-len(x["input_ids"]) for x in all_inputs])
        all_inputs_sorted = [all_inputs[i] for i in length_sorted_idx]

        # ── Batch 推理 ──
        all_dense, all_lexical, all_colbert = [], [], []

        for start in trange(0, len(sentences), batch_size, desc="Embedding", disable=len(sentences) < batch_size):
            inputs_batch = all_inputs_sorted[start : start + batch_size]
            inputs_batch = self.tokenizer.pad(inputs_batch, padding=True, return_tensors="pt").to(self.device)

            output = self._inference_model.forward(
                inputs_batch,
                return_dense=return_dense,
                return_sparse=return_sparse,
                return_colbert_vecs=return_colbert_vecs,
            )

            if return_dense:
                all_dense.append(output["dense_vecs"].cpu().numpy())

            if return_sparse:
                token_weights = output["sparse_vecs"].squeeze(-1).cpu().numpy()
                for tw, ids in zip(token_weights, inputs_batch["input_ids"].cpu().numpy()):
                    all_lexical.append(_token_weights_to_dict(tw, ids, self.tokenizer))

            if return_colbert_vecs:
                colbert_np = output["colbert_vecs"].cpu().numpy()
                attn = inputs_batch["attention_mask"].cpu().numpy()
                for cv, am in zip(colbert_np, attn):
                    tokens_num = int(np.sum(am))
                    all_colbert.append(cv[: tokens_num - 1])  # 去掉 CLS

        # ── ���复原始顺序 ──
        restore_idx = np.argsort(length_sorted_idx)

        result: Dict[str, Any] = {}
        if return_dense:
            dense = np.concatenate(all_dense, axis=0)[restore_idx]
            result["dense_vecs"] = dense[0] if input_was_string else dense

        if return_sparse:
            lexical = [all_lexical[i] for i in restore_idx]
            result["lexical_weights"] = lexical[0] if input_was_string else lexical

        if return_colbert_vecs:
            colbert = [all_colbert[i] for i in restore_idx]
            result["colbert_vecs"] = colbert[0] if input_was_string else colbert

        return result


def _token_weights_to_dict(
    token_weights: np.ndarray, input_ids: np.ndarray, tokenizer: Any
) -> Dict[int, float]:
    """将 token_weights + input_ids ���为 {token_id: max_weight} 字���。"""
    unused_tokens = {
        tokenizer.cls_token_id,
        tokenizer.eos_token_id,
        tokenizer.pad_token_id,
        tokenizer.unk_token_id,
    }
    result: Dict[int, float] = {}
    for w, idx in zip(token_weights, input_ids):
        idx = int(idx)
        if idx not in unused_tokens and w > 0:
            w = float(w)
            if w > result.get(idx, 0.0):
                result[idx] = w
    return result
