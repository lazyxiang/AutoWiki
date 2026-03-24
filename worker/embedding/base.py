from abc import ABC, abstractmethod
import numpy as np


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, text: str, is_code: bool = False) -> np.ndarray:
        """Embed a single text. Returns float32 numpy array."""

    @abstractmethod
    async def embed_batch(self, texts: list[str], is_code: bool = False) -> list[np.ndarray]:
        """Embed multiple texts. Returns list of float32 arrays."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding vector dimension."""
