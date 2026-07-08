"""Пресеты качества - надстройка над ручными CRF/preset/tune.

Это просто *подсказки*. GUI держит ручные поля CRF / ffmpeg-preset / tune
полностью редактируемыми; выбор пресета заполняет их одним кликом. Выбор
пресета пишет все три ключа сразу; ручное изменение любого из трёх
переключает селектор пресета на 'Custom', чтобы юзер видел, что дропдаун
больше не соответствует выставленным значениям.

`tune` работает только для x264/x265. Меню ограничено значениями,
поддерживаемыми обоими кодеками (`film`, `grain`, `animation`,
`stillimage`), чтобы смена пресета не сломала рендер при переключении
кодека. `none` значит: не передавать `-tune` вообще.
"""
from __future__ import annotations

from typing import Optional


CUSTOM = 'Custom'

QUALITY_PRESETS = {
    # Маркер - выбор 'Custom' НЕ меняет ни одно поле. Существует, чтобы у
    # дропдауна была стабильная метка, когда ручные значения не совпадают
    # ни с одним пресетом.
    CUSTOM:    None,
    # Тяжёлый: архивное качество, щедрый битрейт, tune под зерно - для
    # шумного/датамош-материала, который "гладкие" tune размажут.
    'Archive': {'crf': 17, 'export_preset': 'slow',   'tune': 'grain'},
    # По умолчанию. Визуально lossless для большинства материала при
    # разумной скорости.
    'High':    {'crf': 20, 'export_preset': 'medium', 'tune': 'none'},
    # Веб-совместимый размер, быстрое кодирование.
    'Web':     {'crf': 23, 'export_preset': 'fast',   'tune': 'none'},
    # Минимальный вменяемый файл, ещё смотрибельно.
    'Compact': {'crf': 26, 'export_preset': 'fast',   'tune': 'none'},
}

# Ключи, которые пишет пресет - используются GUI в проверке "совпадает ли
# текущее состояние с пресетом?".
PRESET_KEYS = ('crf', 'export_preset', 'tune')

# Значения tune, показываемые в дропдауне GUI.
TUNE_VALUES = ('none', 'film', 'grain', 'animation', 'stillimage')


def preset_names() -> list[str]:
    """Порядок показа в дропдауне."""
    return [CUSTOM, 'Archive', 'High', 'Web', 'Compact']


def matches(name: str, *, crf: int, export_preset: str,
            tune: str) -> bool:
    """True, если (crf, preset, tune) точно равны названному пресету.
    `Custom` никогда не совпадает - это резервная метка."""
    spec = QUALITY_PRESETS.get(name)
    if not spec:
        return False
    return (int(crf) == int(spec['crf'])
            and str(export_preset) == str(spec['export_preset'])
            and str(tune or 'none') == str(spec['tune']))


def detect_preset(*, crf: int, export_preset: str, tune: str) -> str:
    """Возвращает имя пресета с совпадающими (crf, preset, tune), либо 'Custom'."""
    for name in QUALITY_PRESETS:
        if name == CUSTOM:
            continue
        if matches(name, crf=crf, export_preset=export_preset, tune=tune):
            return name
    return CUSTOM


def tune_supported(vcodec: str) -> bool:
    """`-tune` имеет смысл только для libx264/libx265."""
    return vcodec in ('libx264', 'libx265')


def normalize_tune(value: Optional[str]) -> str:
    """Приводит произвольное значение из конфига к допустимому TUNE_VALUES."""
    if value is None:
        return 'none'
    v = str(value).strip().lower()
    return v if v in TUNE_VALUES else 'none'
