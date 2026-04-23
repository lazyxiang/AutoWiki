from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

import numpy as np

from worker.utils.retry import TRANSIENT_EXCEPTIONS, OnRetryCallback, async_retry

EMBEDDING_BATCH_SIZE = 50
EMBEDDING_MAX_RETRIES = 6
EMBEDDING_INITIAL_DELAY = 10.0
EMBEDDING_BACKOFF_FACTOR = 2.0
EMBEDDING_MAX_DELAY = 120.0


def iter_embedding_batches(texts: list[str]) -> list[list[str]]:
    return [
        texts[i : i + EMBEDDING_BATCH_SIZE]
        for i in range(0, len(texts), EMBEDDING_BATCH_SIZE)
    ]


async def retry_embedding_call[T](
    fn: Callable[..., Awaitable[T]],
    *args,
    on_retry: OnRetryCallback | None = None,
    **kwargs,
) -> T:
    return await async_retry(
        fn,
        *args,
        max_retries=EMBEDDING_MAX_RETRIES,
        initial_delay=EMBEDDING_INITIAL_DELAY,
        backoff_factor=EMBEDDING_BACKOFF_FACTOR,
        max_delay=EMBEDDING_MAX_DELAY,
        transient_exceptions=TRANSIENT_EXCEPTIONS,
        on_retry=on_retry,
        **kwargs,
    )


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, text: str, is_code: bool = False) -> np.ndarray:
        """Embed a single text. Returns float32 numpy array."""

    @abstractmethod
    async def embed_batch(
        self,
        texts: list[str],
        is_code: bool = False,
        on_retry: OnRetryCallback | None = None,
    ) -> list[np.ndarray]:
        """Embed multiple texts. Returns list of float32 arrays."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding vector dimension."""
