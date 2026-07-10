"""Кэш результатов анализа аудио между рендерами.

Анализ (HPSS + несколько STFT + onset/beat/rms) - самая тяжёлая фиксированная
часть рендера, и она не зависит от длины ролика: короткий клип платит за неё
столько же, сколько длинный. При повторном рендере того же трека с теми же
параметрами результат идентичен, поэтому кэшируем его на диск.

Ключ строится по контент-идентичности исходного файла (абсолютный путь +
mtime + размер) плюс всем параметрам анализа, границам окна отрывка, версии
формата кэша и версии librosa. Любое изменение входа или логики даёт другой
ключ и, значит, промах.

Устойчивость: любой сбой кэша (нет прав на temp, битый/обрезанный файл,
несовместимый пиклом формат, гонка записи) трактуется как промах и никогда не
роняет и не искажает рендер - в худшем случае анализ просто выполнится заново.
Читаем и пишем только собственные файлы в выделенном каталоге, поэтому pickle
здесь безопасен.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import tempfile
from typing import Optional

# Версия формата/логики. Поднимать при любом изменении, влияющем на то, что
# возвращает анализатор (структуры данных, формулы фич, дефолты). Старые файлы
# кэша с другой версией просто не совпадут по ключу.
CACHE_VERSION = 1

# Границы авто-очистки каталога кэша. Каждая запись включает полную моно-волну
# трека, поэтому файл может весить десятки МБ; держим и число файлов, и
# суммарный размер под потолком, удаляя самые старые.
_MAX_FILES = 24
_MAX_TOTAL_BYTES = 1024 * 1024 * 1024   # 1 ГиБ


def _cache_dir() -> str:
    return os.path.join(tempfile.gettempdir(), 'vpc_analysis_cache')


def _librosa_version() -> str:
    try:
        import librosa
        return str(getattr(librosa, '__version__', '?'))
    except Exception:
        return '?'


def make_key(audio_path: str, params: dict,
             window: Optional[tuple] = None) -> Optional[str]:
    """Строит ключ кэша по идентичности файла и параметрам анализа.

    Возвращает None, если файл нельзя стат-нуть (тогда кэш отключается для
    этого рендера - не строим ключ по нестабильному входу).
    """
    if not audio_path:
        return None
    try:
        st = os.stat(audio_path)
        ident = (
            os.path.abspath(audio_path),
            int(st.st_mtime_ns),
            int(st.st_size),
        )
    except OSError:
        return None

    # Стабильно сериализуем параметры: сортируем по ключу и приводим к строке,
    # чтобы порядок словаря и типы не влияли на ключ.
    param_items = tuple(sorted((str(k), repr(v)) for k, v in params.items()))
    win = None
    if window is not None:
        w0, w1 = window
        win = (round(float(w0), 4),
               None if w1 is None else round(float(w1), 4))

    raw = repr((CACHE_VERSION, _librosa_version(), ident, param_items, win))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _path_for(key: str) -> str:
    return os.path.join(_cache_dir(), key + '.pkl')


def load(key: Optional[str]):
    """Возвращает закэшированный payload или None при любом промахе/сбое."""
    if not key:
        return None
    path = _path_for(key)
    try:
        with open(path, 'rb') as f:
            payload = pickle.load(f)
    except (OSError, pickle.UnpicklingError, EOFError, ValueError,
            AttributeError, ImportError, MemoryError):
        return None
    except Exception:
        # Пикл может кинуть почти что угодно на несовместимых/битых данных.
        # Любой сбой = промах, рендер продолжится обычным анализом.
        return None

    if not _valid_payload(payload):
        return None
    # Освежаем mtime, чтобы очистка по возрасту считала запись используемой.
    try:
        os.utime(path, None)
    except OSError:
        pass
    return payload


def store(key: Optional[str], payload) -> None:
    """Атомарно сохраняет payload. Любой сбой проглатывается (кэш опционален)."""
    if not key or not _valid_payload(payload):
        return
    d = _cache_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return
    path = _path_for(key)
    tmp = f'{path}.{os.getpid()}.tmp'
    try:
        with open(tmp, 'wb') as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        # Атомарная замена в пределах одного каталога/ФС.
        os.replace(tmp, path)
    except (OSError, pickle.PicklingError, MemoryError):
        _safe_remove(tmp)
        return
    except Exception:
        _safe_remove(tmp)
        return
    _prune(d)


def _valid_payload(payload) -> bool:
    """Проверяет форму payload: (segments, duration, features, bpm)."""
    if not isinstance(payload, tuple) or len(payload) != 4:
        return False
    segments, duration, _features, bpm = payload
    if not isinstance(segments, list):
        return False
    if not isinstance(duration, (int, float)):
        return False
    if not isinstance(bpm, (int, float)):
        return False
    return True


def _prune(d: str) -> None:
    """Держит каталог кэша под лимитами числа файлов и суммарного размера."""
    try:
        entries = []
        total = 0
        for name in os.listdir(d):
            if not name.endswith('.pkl'):
                continue
            p = os.path.join(d, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        # Старые - первыми в очереди на удаление.
        entries.sort()
        i = 0
        n = len(entries)
        while (n - i) > _MAX_FILES or total > _MAX_TOTAL_BYTES:
            if i >= n:
                break
            _, size, p = entries[i]
            if _safe_remove(p):
                total -= size
            i += 1
    except OSError:
        pass


def _safe_remove(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except OSError:
        return False
