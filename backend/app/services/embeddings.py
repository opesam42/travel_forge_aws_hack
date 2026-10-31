import hashlib
import logging
import tarfile
from pathlib import Path
from typing import List

import httpx
import numpy as np
import onnxruntime
from tokenizers import Tokenizer

from app.core.config import settings

logger = logging.getLogger(__name__)

MODEL_URL = "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"
MODEL_SHA256 = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
MODEL_DIR = Path(settings.RAG_EMBEDDING_CACHE_DIR).expanduser()
MODEL_ARCHIVE = MODEL_DIR / "onnx.tar.gz"
MODEL_FILES = (
    "config.json",
    "model.onnx",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "tokenizer.json",
    "vocab.txt",
)
MAX_MODEL_ARCHIVE_BYTES = 120 * 1024 * 1024

_embedder: "MiniLMEmbedder | None" = None


class MiniLMEmbedder:
    """Generate embeddings compatible with the existing all-MiniLM-L6-v2 vectors."""

    def __init__(self) -> None:
        self._ensure_model_files()
        model_path = MODEL_DIR / "onnx" / "model.onnx"
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._model = onnxruntime.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
            sess_options=options,
        )
        self._tokenizer = self._load_tokenizer()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as model_file:
            for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _ensure_model_files(cls) -> None:
        extracted_dir = MODEL_DIR / "onnx"
        if all((extracted_dir / name).is_file() for name in MODEL_FILES):
            return

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        if not MODEL_ARCHIVE.is_file() or cls._file_sha256(MODEL_ARCHIVE) != MODEL_SHA256:
            MODEL_ARCHIVE.unlink(missing_ok=True)
            logger.info("Downloading MiniLM embedding model")
            total_bytes = 0
            digest = hashlib.sha256()
            try:
                with httpx.stream("GET", MODEL_URL, timeout=90.0, follow_redirects=True) as response:
                    response.raise_for_status()
                    with MODEL_ARCHIVE.open("wb") as archive:
                        for chunk in response.iter_bytes(1024 * 1024):
                            total_bytes += len(chunk)
                            if total_bytes > MAX_MODEL_ARCHIVE_BYTES:
                                raise RuntimeError("Embedding model archive exceeded size limit.")
                            digest.update(chunk)
                            archive.write(chunk)
            except Exception:
                MODEL_ARCHIVE.unlink(missing_ok=True)
                raise
            if digest.hexdigest() != MODEL_SHA256:
                MODEL_ARCHIVE.unlink(missing_ok=True)
                raise RuntimeError("Embedding model checksum verification failed.")

        with tarfile.open(MODEL_ARCHIVE, "r:gz") as archive:
            archive.extractall(MODEL_DIR, filter="data")
        if not all((extracted_dir / name).is_file() for name in MODEL_FILES):
            raise RuntimeError("Downloaded embedding model archive is incomplete.")

    @staticmethod
    def _load_tokenizer() -> Tokenizer:
        tokenizer = Tokenizer.from_file(str(MODEL_DIR / "onnx" / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=256)
        tokenizer.enable_padding(pad_id=0, pad_token="[PAD]", length=256)
        return tokenizer

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        encoded = [self._tokenizer.encode(text) for text in texts]
        attention_mask = np.asarray(
            [encoding.attention_mask for encoding in encoded],
            dtype=np.int64,
        )
        input_ids = np.asarray([encoding.ids for encoding in encoded], dtype=np.int64)
        model_inputs = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": np.zeros_like(input_ids, dtype=np.int64),
        }
        hidden_states = self._model.run(None, model_inputs)[0]
        expanded_mask = np.broadcast_to(attention_mask[..., None], hidden_states.shape)
        vectors = np.sum(hidden_states * expanded_mask, axis=1) / np.clip(
            expanded_mask.sum(axis=1),
            a_min=1e-9,
            a_max=None,
        )
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.maximum(norms, 1e-12)
        return vectors.astype(np.float32).tolist()


def get_embedder() -> MiniLMEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = MiniLMEmbedder()
    return _embedder
