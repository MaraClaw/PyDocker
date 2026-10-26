from collections.abc import Callable, Generator, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from queue import Queue
from threading import Event, Lock
from types import TracebackType
from typing import Self, override

from pydocker.utils import MappedProcessStream


class MultiImageStream(Iterator[tuple[str, bytes]]):
    """Own a lazy four-worker image transfer group and its active child streams.

    Explicit close cancels local CLI processes, not daemon-managed images. A bare
    loop break leaves ownership with the caller. Worker errors stop sibling work
    immediately and are raised once on consumption, including after close.
    """

    def __init__(
        self,
        names: Sequence[str],
        factory: Callable[[str], MappedProcessStream[tuple[str, bytes]]],
    ) -> None:
        self._names = tuple(names)
        self._factory = factory
        self._lock = Lock()
        self._queue: Queue[tuple[str, bytes] | None] = Queue()
        self._executor: ThreadPoolExecutor | None = None
        self._children: set[MappedProcessStream[tuple[str, bytes]]] = set()
        self._remaining = len(names)
        self._stopped = False
        self._closing = False
        self._cleanup_done = Event()
        self._error: BaseException | None = None

    def _start(self) -> None:
        started = False
        try:
            with self._lock:
                if not self._stopped and self._executor is None:
                    self._executor = ThreadPoolExecutor(max_workers=4)
                    for name in self._names:
                        future = self._executor.submit(self._consume, name)
                        future.add_done_callback(self._worker_done)
                    if not self._names:
                        self._queue.put(None)
            started = True
        finally:
            if not started:
                self.close()

    def _consume(self, name: str) -> None:
        with self._lock:
            if self._stopped:
                return
            child = self._factory(name)
            self._children.add(child)
        try:
            for item in child:
                self._queue.put(item)
        finally:
            child.close()
            with self._lock:
                self._children.remove(child)

    def _worker_done(self, future: Future[None]) -> None:
        error = None if future.cancelled() else future.exception()
        with self._lock:
            self._remaining -= 1
            if error is not None and self._error is None:
                self._error = error
                self._stopped = True
                children = tuple(self._children)
                executor = self._executor
            else:
                children = ()
                executor = None
            if self._stopped or not self._remaining:
                self._queue.put(None)
        try:
            for child in children:
                child.close()
        finally:
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)

    @override
    def __iter__(self) -> Self:
        return self

    @override
    def __next__(self) -> tuple[str, bytes]:
        self._start()
        while True:
            with self._lock:
                stopped = self._stopped
            if stopped:
                break
            item = self._queue.get()
            with self._lock:
                if self._stopped:
                    break
            if item is None:
                break
            return item
        self.close()
        with self._lock:
            error = self._error
            self._error = None
        if error is not None:
            raise error
        raise StopIteration

    def close(self) -> None:
        """Stop admission, close children, then wait for all worker cleanup."""
        with self._lock:
            self._stopped = True
            self._queue.put(None)
            cleanup_owner = not self._closing
            self._closing = True
            children = tuple(self._children)
            executor = self._executor
        if not cleanup_owner:
            self._cleanup_done.wait()
            return
        try:
            for child in children:
                child.close()
        finally:
            try:
                if executor is not None:
                    executor.shutdown(wait=True, cancel_futures=True)
            finally:
                self._cleanup_done.set()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


type ImageStream = MappedProcessStream[tuple[str, bytes]] | MultiImageStream
type EmptyImageStream = Generator[tuple[str, bytes]]
