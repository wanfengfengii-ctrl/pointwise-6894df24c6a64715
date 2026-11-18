from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from recordings.events import RecordingError


class RecordingSource:
    """一次性本地录制源，按实际物理行解码。"""

    def __init__(self, path: str | Path) -> None:
        try:
            self.path = Path(path)
        except TypeError as exc:
            raise RecordingError(None, '源路径无效') from exc
        self._stream: BinaryIO | None = None
        self._started = False
        self._closed = False

    def __enter__(self) -> 'RecordingSource':
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def iter_lines(self) -> Iterator[tuple[int, str]]:
        if self._closed or self._started:
            raise RecordingError(None, '源已经关闭或开始读取')
        self._started = True
        line = None
        try:
            stream = self.path.open('rb')
            self._stream = stream
            line = 1
            while not self._closed:
                raw = stream.readline()
                if not raw:
                    return
                yield line, raw.decode('utf-8')
                line += 1
            raise RecordingError(line, '源在读取期间关闭')
        except UnicodeDecodeError as exc:
            raise RecordingError(line, '无法按 UTF-8 解码') from exc
        except OSError as exc:
            raise RecordingError(line, f'读取失败 ({exc.strerror})') from exc
        finally:
            self.close()

    def close(self) -> None:
        self._closed = True
        if self._stream is not None:
            stream, self._stream = self._stream, None
            stream.close()
