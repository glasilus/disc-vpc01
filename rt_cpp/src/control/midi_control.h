#pragma once
#include <string>
#include <vector>
#include <functional>
#include <memory>
#include <map>

class RtMidiIn;  // forward decl - чтобы не тащить <RtMidi.h> в этот заголовок

// MidiControl - опциональный слой маппинга аппаратного MIDI для VJ-движка.
//
// Модель:
//   * непрерывные параметры (ручки/фейдеры) -> MIDI CC      -> on_value(float 0..1)
//   * разовые действия (пэды/кнопки)        -> MIDI Note-On -> on_trigger()
//
// Только поллинг: приложение вызывает poll() раз за кадр рендера из основного
// потока. Никаких фоновых потоков, никаких RtMidi callback'ов - блокировать
// render loop нечем.
//
// Полностью опционален в рантайме: если нет бэкенда/устройства, init() вернёт
// false, а все остальные методы станут безопасным no-op. Ни один публичный
// метод не бросает исключений.
class MidiControl {
public:
    MidiControl();
    ~MidiControl();

    // Открывает первый доступный MIDI input порт (или по индексу). Возвращает
    // false, если порта/бэкенда нет. Можно вызывать повторно, чтобы переоткрыть.
    bool init(int port_index = 0);
    bool is_open() const;
    void close();

    // Список имён input-портов (для дропдауна устройств). Пуст, если их нет.
    std::vector<std::string> list_ports() const;
    int  current_port() const;

    // Регистрирует непрерывный параметр: вызывается со значением [0,1] при
    // каждом движении привязанного MIDI CC. `name` - стабильный id для
    // сохранения на диск и для UI.
    void register_param(const std::string& name, std::function<void(float)> on_value);
    // Регистрирует разовое действие: срабатывает один раз на привязанный Note-On (velocity>0).
    void register_action(const std::string& name, std::function<void()> on_trigger);

    // MIDI-Learn: следующее CC-сообщение привяжется к `name` (для параметров),
    // следующая нота - для действий. Повторный вызов begin_learn_* перепривязывает;
    // пустая строка отменяет обучение. is_learning()/learning_target() - для UI.
    void begin_learn_param(const std::string& name);
    void begin_learn_action(const std::string& name);
    void clear_binding(const std::string& name);
    bool is_learning() const;
    std::string learning_target() const;

    // Человекочитаемая текущая привязка ("CC 74 ch1", "Note 36" или "-").
    std::string binding_label(const std::string& name) const;

    // Разбирает накопленные MIDI-сообщения, вызывает зарегистрированные
    // callback'и. Вызывается раз за кадр из render-потока.
    void poll();

    // Персистентность (только привязки, не сами callback'и).
    bool save(const std::string& path) const;
    bool load(const std::string& path);

private:
    enum class BindType { CC, Note };
    struct Binding {
        BindType type    = BindType::CC;
        int      channel = 0;   // 0..15 (внутри 0-based; в UI/на диске - 1-based)
        int      number  = 0;   // номер CC или ноты, 0..127
    };
    enum class LearnMode { None, Param, Action };

    void handle_message(const std::vector<unsigned char>& msg);
    void reconcile_pending(const std::string& name, BindType expected_type);
    static std::string label_for(const Binding& b);

    std::unique_ptr<RtMidiIn> midi_in_;
    int current_port_ = -1;

    // Зарегистрированные callback'и по стабильному имени.
    std::map<std::string, std::function<void(float)>> params_;
    std::map<std::string, std::function<void()>>      actions_;

    // Активные привязки: имя -> (тип, канал, номер). Несколько имён могут
    // делить один физический контрол; dispatch сканирует все привязки на каждое сообщение.
    std::map<std::string, Binding> bindings_;
    // Привязки, загруженные с диска, для которых ещё нет зарегистрированного
    // callback'а. Переезжают в bindings_, как только register_param/register_action
    // увидит совпадающее имя.
    std::map<std::string, Binding> pending_;

    // Машина состояний MIDI-Learn (сам поток см. в midi_control.cpp).
    LearnMode   learn_mode_ = LearnMode::None;
    std::string learn_target_;
};
