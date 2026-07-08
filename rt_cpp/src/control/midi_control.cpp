// midi_control.cpp - маппинг аппаратного MIDI (ручки -> параметры, пэды ->
// действия) с рантайм MIDI-Learn и сохранением в JSON.
//
// Зависимость: RtMidi (vcpkg-пакет `rtmidi`). Интеграция с CMake настроена
// отдельно:
//     find_package(unofficial-rtmidi CONFIG REQUIRED)
//     target_link_libraries(<app_target> PRIVATE unofficial::rtmidi::rtmidi)
// Для JSON используется nlohmann/json, он уже есть в зависимостях проекта.
//
// Заметки по устройству:
//   * Только модель поллинга - приложение вызывает poll() раз за кадр рендера.
//     RtMidi сам копит входящие сообщения в очередь (размер задан ниже),
//     мы вычерпываем её каждый кадр. Ни callback'ов, ни потоков, ни локов.
//   * Каждый вызов RtMidi обёрнут в try/catch - RtMidi бросает RtMidiError
//     (нет скомпилированного бэкенда, устройство отключили посреди сессии).
//     Отсутствие устройства не должно ронять приложение; неудачные вызовы
//     превращаются в no-op.

#include "midi_control.h"

// Путь до заголовка RtMidi отличается в зависимости от дистрибуции: vcpkg
// ставит его как <rtmidi/RtMidi.h> (include-каталог пакета - корневой
// `include`), а системная/pkg-config установка обычно даёт <RtMidi.h>.
// Проверяем оба варианта.
#if __has_include(<rtmidi/RtMidi.h>)
#  include <rtmidi/RtMidi.h>
#elif __has_include(<RtMidi.h>)
#  include <RtMidi.h>
#else
#  error "RtMidi.h not found - ensure the 'rtmidi' vcpkg port is installed"
#endif
#include <nlohmann/json.hpp>

#include <filesystem>
#include <fstream>

namespace {

// Очередь RtMidi держит сообщения между вызовами poll(). Кадр на 60 fps - это
// ~16 мс; даже быстрое вращение ручки даёт заметно меньше 100 CC за это время,
// так что 1024 - большой запас без ощутимой цены по памяти.
constexpr unsigned int kQueueSize = 1024;

// Отключаем стандартное поведение RtMidi печатать предупреждения в std::cerr
// (например, при кратковременных глюках порта). Ошибки, которые нас волнуют,
// приходят как брошенный RtMidiError из обёрнутых вызовов. Без состояния -
// безопасно при нескольких экземплярах.
void rtmidi_silent_error_cb(RtMidiError::Type /*type*/,
                            const std::string& /*text*/,
                            void* /*user*/) {}

} // namespace

MidiControl::MidiControl() = default;

// Деструктор вынесен из класса: unique_ptr<RtMidiIn> с forward-declared типом
// иначе не скомпилируется в заголовке.
MidiControl::~MidiControl() {
    close();
}

// ---------------------------------------------------------------------------
// Управление устройством / портом
// ---------------------------------------------------------------------------

bool MidiControl::init(int port_index) {
    close();  // можно вызывать повторно, чтобы переоткрыть
    try {
        auto in = std::make_unique<RtMidiIn>(RtMidi::UNSPECIFIED,
                                             "Disc VPC 01 RT", kQueueSize);
        in->setErrorCallback(&rtmidi_silent_error_cb);

        const unsigned int count = in->getPortCount();
        if (count == 0 || port_index < 0 ||
            static_cast<unsigned int>(port_index) >= count) {
            return false;  // нет устройства / неверный индекс - остаёмся закрытыми, без исключений
        }

        in->openPort(static_cast<unsigned int>(port_index), "Disc VPC 01 RT In");
        // Игнорируем SysEx, MIDI timing clock и active sensing - нужны только
        // channel voice messages (CC / ноты); один clock может давать 24 сообщения на бит.
        in->ignoreTypes(true, true, true);

        midi_in_      = std::move(in);
        current_port_ = port_index;
        return true;
    } catch (const RtMidiError&) {
        // Не скомпилирован бэкенд / сбой драйвера - модуль остаётся неактивным.
    } catch (...) {
        // Публичный метод не должен пропускать исключения наружу.
    }
    midi_in_.reset();
    current_port_ = -1;
    return false;
}

bool MidiControl::is_open() const {
    if (!midi_in_) return false;
    try {
        return midi_in_->isPortOpen();
    } catch (...) {
        return false;
    }
}

void MidiControl::close() {
    if (midi_in_) {
        try {
            midi_in_->closePort();
        } catch (...) {
            // Игнорируем - мы всё равно разрушаем объект.
        }
        midi_in_.reset();
    }
    current_port_ = -1;
}

std::vector<std::string> MidiControl::list_ports() const {
    std::vector<std::string> names;
    try {
        // Используем уже открытый инстанс, если есть; иначе пробуем через
        // временный объект (конструктор RtMidiIn бросает, если нет бэкенда).
        std::unique_ptr<RtMidiIn> probe;
        RtMidiIn* in = midi_in_.get();
        if (!in) {
            probe = std::make_unique<RtMidiIn>(RtMidi::UNSPECIFIED,
                                               "Disc VPC 01 RT probe");
            probe->setErrorCallback(&rtmidi_silent_error_cb);
            in = probe.get();
        }
        const unsigned int count = in->getPortCount();
        names.reserve(count);
        for (unsigned int i = 0; i < count; ++i) {
            try {
                names.push_back(in->getPortName(i));
            } catch (...) {
                names.push_back("(unknown port)");
            }
        }
    } catch (...) {
        names.clear();  // нет бэкенда - пустой список, вызывающий код покажет "нет устройств"
    }
    return names;
}

int MidiControl::current_port() const {
    return current_port_;
}

// ---------------------------------------------------------------------------
// Регистрация + разбор отложенных привязок
// ---------------------------------------------------------------------------

// Разбор: load() может выполниться до того, как приложение зарегистрирует
// свои контролы (или пресет ссылается на контролы плагина, который загрузится
// позже). Такие привязки паркуются в pending_; как только совпадающее имя
// регистрируется *с совпадающим типом*, привязка становится активной. Тип
// обязателен: иначе сохранённая CC-привязка могла бы сработать на action
// с тем же именем, и наоборот.
void MidiControl::reconcile_pending(const std::string& name, BindType expected_type) {
    auto it = pending_.find(name);
    if (it != pending_.end() && it->second.type == expected_type) {
        bindings_[name] = it->second;
        pending_.erase(it);
    }
}

void MidiControl::register_param(const std::string& name,
                                 std::function<void(float)> on_value) {
    if (name.empty()) return;
    // Идемпотентно по имени: повторная регистрация меняет только callback,
    // существующая привязка для `name` не трогается.
    params_[name] = std::move(on_value);
    reconcile_pending(name, BindType::CC);
}

void MidiControl::register_action(const std::string& name,
                                  std::function<void()> on_trigger) {
    if (name.empty()) return;
    actions_[name] = std::move(on_trigger);
    reconcile_pending(name, BindType::Note);
}

// ---------------------------------------------------------------------------
// Машина состояний MIDI-Learn
// ---------------------------------------------------------------------------
// Состояния: None -> Param(name) или Action(name) через begin_learn_*;
// обратно в None, когда (a) приходит первое подходящее сообщение (CC для
// Param, Note-On для Action - сообщение *поглощается*, то есть привязывается,
// но не вызывает callback), (b) begin_learn_*("") отменяет обучение, или
// (c) begin_learn_* вызван с другим именем (перепривязка: побеждает
// последний запрос).

void MidiControl::begin_learn_param(const std::string& name) {
    if (name.empty()) {
        learn_mode_ = LearnMode::None;
        learn_target_.clear();
        return;
    }
    learn_mode_   = LearnMode::Param;
    learn_target_ = name;
}

void MidiControl::begin_learn_action(const std::string& name) {
    if (name.empty()) {
        learn_mode_ = LearnMode::None;
        learn_target_.clear();
        return;
    }
    learn_mode_   = LearnMode::Action;
    learn_target_ = name;
}

void MidiControl::clear_binding(const std::string& name) {
    bindings_.erase(name);
    pending_.erase(name);
}

bool MidiControl::is_learning() const {
    return learn_mode_ != LearnMode::None;
}

std::string MidiControl::learning_target() const {
    return is_learning() ? learn_target_ : std::string();
}

std::string MidiControl::label_for(const Binding& b) {
    // Канал 1-based - так удобнее людям, совпадает с большинством мануалов контроллеров.
    const std::string ch = " ch" + std::to_string(b.channel + 1);
    if (b.type == BindType::CC)
        return "CC " + std::to_string(b.number) + ch;
    return "Note " + std::to_string(b.number) + ch;
}

std::string MidiControl::binding_label(const std::string& name) const {
    auto it = bindings_.find(name);
    if (it != bindings_.end()) return label_for(it->second);
    // Привязку, загруженную, но ещё не зарегистрированную, всё равно стоит показать в UI.
    it = pending_.find(name);
    if (it != pending_.end()) return label_for(it->second);
    return "-";
}

// ---------------------------------------------------------------------------
// Разбор сообщений + диспетчеризация
// ---------------------------------------------------------------------------

void MidiControl::poll() {
    if (!midi_in_) return;
    try {
        std::vector<unsigned char> msg;
        // Вычерпываем всё, что накопилось с прошлого кадра. getMessage
        // возвращает пустой вектор, когда очередь пуста.
        for (;;) {
            msg.clear();
            midi_in_->getMessage(&msg);
            if (msg.empty()) break;
            handle_message(msg);
        }
    } catch (const RtMidiError&) {
        // Устройство, скорее всего, пропало посреди сессии (отключили).
        // Переходим в закрытое состояние; переоткрыть можно из дропдауна устройств.
        close();
    } catch (...) {
        // Не пробрасываем дальше - сломанный пользовательский callback не
        // должен убивать вызывающего poll() (render loop). Остаток очереди
        // разберём на следующем кадре.
    }
}

void MidiControl::handle_message(const std::vector<unsigned char>& msg) {
    // Интересующие нас channel voice messages всегда 3 байта; всё короче
    // (или системное сообщение >= 0xF0) игнорируем как мусор/нерелевантное.
    if (msg.size() < 3) return;
    const unsigned char status = msg[0];
    if (status >= 0xF0) return;

    const int type    = status & 0xF0;
    const int channel = status & 0x0F;
    const int number  = msg[1] & 0x7F;
    const int value   = msg[2] & 0x7F;

    if (type == 0xB0) {  // Control Change
        // Learn полностью поглощает первый CC: привязывает, выходит из
        // режима обучения, но НЕ вызывает callback на это сообщение
        // (обучение не должно ничего триггерить).
        if (learn_mode_ == LearnMode::Param) {
            bindings_[learn_target_] = { BindType::CC, channel, number };
            pending_.erase(learn_target_);  // fresh learn supersedes stale disk state
            learn_mode_ = LearnMode::None;
            learn_target_.clear();
            return;
        }
        const float norm = static_cast<float>(value) / 127.0f;
        // Сначала собираем подходящие callback'и, вызываем после: callback
        // может дёрнуть register_*/begin_learn_* и поменять карты, по которым мы итерируемся.
        std::vector<std::function<void(float)>> to_fire;
        for (const auto& [name, bind] : bindings_) {
            if (bind.type != BindType::CC) continue;
            if (bind.channel != channel || bind.number != number) continue;
            auto cb = params_.find(name);
            if (cb != params_.end() && cb->second) to_fire.push_back(cb->second);
        }
        for (auto& fn : to_fire) fn(norm);
    } else if (type == 0x90) {  // Note-On
        // Velocity 0 - это замаскированный Note-Off (running status), никогда не триггерит.
        if (value == 0) return;
        if (learn_mode_ == LearnMode::Action) {
            bindings_[learn_target_] = { BindType::Note, channel, number };
            pending_.erase(learn_target_);
            learn_mode_ = LearnMode::None;
            learn_target_.clear();
            return;
        }
        std::vector<std::function<void()>> to_fire;
        for (const auto& [name, bind] : bindings_) {
            if (bind.type != BindType::Note) continue;
            if (bind.channel != channel || bind.number != number) continue;
            auto cb = actions_.find(name);
            if (cb != actions_.end() && cb->second) to_fire.push_back(cb->second);
        }
        for (auto& fn : to_fire) fn();
    }
    // 0x80 (Note-Off) и всё остальное сознательно игнорируется.
}

// ---------------------------------------------------------------------------
// Персистентность - name -> { type, channel (1-based), number }
// ---------------------------------------------------------------------------

bool MidiControl::save(const std::string& path) const {
    try {
        nlohmann::json b = nlohmann::json::object();
        auto dump = [&b](const std::map<std::string, Binding>& src) {
            for (const auto& [name, bind] : src) {
                b[name] = {
                    { "type",    bind.type == BindType::CC ? "cc" : "note" },
                    { "channel", bind.channel + 1 },  // на диске 1-based (для ручного редактирования)
                    { "number",  bind.number },
                };
            }
        };
        dump(bindings_);
        dump(pending_);  // не теряем ещё не зарегистрированные привязки при сохранении

        nlohmann::json j;
        j["bindings"] = std::move(b);

        // u8path: узкий ofstream у MSVC работает через ANSI-кодовую страницу,
        // что ломается на не-ASCII путях (например, C:\Users\Майя). Идём через fs::path.
        std::ofstream f(std::filesystem::u8path(path),
                        std::ios::out | std::ios::trunc);
        if (!f) return false;
        f << j.dump(2) << '\n';
        return f.good();
    } catch (...) {
        return false;
    }
}

bool MidiControl::load(const std::string& path) {
    try {
        std::ifstream f(std::filesystem::u8path(path));
        if (!f) return false;
        nlohmann::json j = nlohmann::json::parse(f, /*cb=*/nullptr,
                                                 /*allow_exceptions=*/false);
        if (j.is_discarded() || !j.is_object()) return false;
        auto it = j.find("bindings");
        if (it == j.end() || !it->is_object()) return false;

        bindings_.clear();
        pending_.clear();

        for (auto& [name, entry] : it->items()) {
            if (name.empty() || !entry.is_object()) continue;

            const std::string type_s = entry.value("type", std::string());
            BindType type;
            if      (type_s == "cc")   type = BindType::CC;
            else if (type_s == "note") type = BindType::Note;
            else continue;  // неизвестный тип - пропускаем запись, весь файл не валим

            const int channel = entry.value("channel", 1) - 1;  // на диске 1-based
            const int number  = entry.value("number", -1);
            if (channel < 0 || channel > 15) continue;
            if (number  < 0 || number  > 127) continue;

            const Binding bind{ type, channel, number };
            // Если имя уже зарегистрировано с подходящим типом - привязываем
            // сразу; иначе паркуем в pending_ до вызова register_*.
            const bool registered =
                (type == BindType::CC   && params_.count(name)  != 0) ||
                (type == BindType::Note && actions_.count(name) != 0);
            if (registered) bindings_[name] = bind;
            else            pending_[name]  = bind;
        }
        return true;
    } catch (...) {
        return false;
    }
}
