// midi_control.cpp — hardware MIDI mapping (knobs -> params, pads -> actions)
// with runtime MIDI-Learn and JSON persistence.
//
// Dependency: RtMidi (vcpkg package: `rtmidi`). CMake integration (done in a
// separate step — do NOT duplicate here):
//     find_package(unofficial-rtmidi CONFIG REQUIRED)
//     target_link_libraries(<app_target> PRIVATE unofficial::rtmidi::rtmidi)
// JSON persistence uses nlohmann/json, already a project dependency.
//
// Design notes:
//   * Polling model only — the app calls poll() once per render frame. RtMidi
//     queues incoming messages internally (queue size set below); we drain the
//     queue each frame. No callbacks, no threads, no locks.
//   * Every RtMidi call is wrapped in try/catch — RtMidi throws RtMidiError
//     (e.g. no backend compiled in, device unplugged mid-session). A missing
//     device must never crash the app; failed calls degrade to no-ops.

#include "midi_control.h"

#include <RtMidi.h>
#include <nlohmann/json.hpp>

#include <filesystem>
#include <fstream>

namespace {

// RtMidi's queue holds messages between poll() calls. A frame at 60 fps is
// ~16 ms; a fast knob twist emits well under 100 CCs in that window, so 1024
// gives a huge safety margin without meaningful memory cost.
constexpr unsigned int kQueueSize = 1024;

// Silence RtMidi's default behavior of printing warnings to std::cerr
// (e.g. transient port glitches). Errors we care about surface as thrown
// RtMidiError from the calls we wrap. Stateless — safe with many instances.
void rtmidi_silent_error_cb(RtMidiError::Type /*type*/,
                            const std::string& /*text*/,
                            void* /*user*/) {}

} // namespace

MidiControl::MidiControl() = default;

// Out-of-line dtor required: unique_ptr<RtMidiIn> with fwd-declared type.
MidiControl::~MidiControl() {
    close();
}

// ---------------------------------------------------------------------------
// Device / port management
// ---------------------------------------------------------------------------

bool MidiControl::init(int port_index) {
    close();  // "safe to call again to reopen"
    try {
        auto in = std::make_unique<RtMidiIn>(RtMidi::UNSPECIFIED,
                                             "Disc VPC 01 RT", kQueueSize);
        in->setErrorCallback(&rtmidi_silent_error_cb);

        const unsigned int count = in->getPortCount();
        if (count == 0 || port_index < 0 ||
            static_cast<unsigned int>(port_index) >= count) {
            return false;  // no device / bad index — stay closed, no throw
        }

        in->openPort(static_cast<unsigned int>(port_index), "Disc VPC 01 RT In");
        // Ignore SysEx, MIDI timing clock and active sensing — we only want
        // channel voice messages (CC / notes); clock alone can be 24 msgs/beat.
        in->ignoreTypes(true, true, true);

        midi_in_      = std::move(in);
        current_port_ = port_index;
        return true;
    } catch (const RtMidiError&) {
        // No backend compiled in / driver failure — module stays inert.
    } catch (...) {
        // Never let anything escape a public method.
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
            // Ignore — we're tearing down anyway.
        }
        midi_in_.reset();
    }
    current_port_ = -1;
}

std::vector<std::string> MidiControl::list_ports() const {
    std::vector<std::string> names;
    try {
        // Use the open instance if we have one; otherwise probe with a
        // temporary (constructing RtMidiIn throws if no backend exists).
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
        names.clear();  // no backend — empty list, caller shows "no devices"
    }
    return names;
}

int MidiControl::current_port() const {
    return current_port_;
}

// ---------------------------------------------------------------------------
// Registration + pending-binding reconciliation
// ---------------------------------------------------------------------------

// Reconciliation: load() may run before the app registers its controls (or a
// preset may reference controls of a plugin loaded later). Such bindings park
// in pending_; the moment the matching name registers *with the matching
// type*, the binding becomes active. Type must match so that a saved CC
// binding never fires a same-named action (and vice versa).
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
    // Idempotent by name: re-registering replaces the callback only; any
    // existing binding for `name` is untouched.
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
// MIDI-Learn state machine
// ---------------------------------------------------------------------------
// States: None -> Param(name) or Action(name) via begin_learn_*; back to None
// when (a) the first matching message arrives (CC for Param, Note-On for
// Action — the message is *consumed*, i.e. binds but does not fire any
// callback), (b) begin_learn_*("") cancels, or (c) begin_learn_* is called
// with another name (rebind: the newer request simply wins).

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
    // 1-based channel for humans, matching most controller manuals.
    const std::string ch = " ch" + std::to_string(b.channel + 1);
    if (b.type == BindType::CC)
        return "CC " + std::to_string(b.number) + ch;
    return "Note " + std::to_string(b.number) + ch;
}

std::string MidiControl::binding_label(const std::string& name) const {
    auto it = bindings_.find(name);
    if (it != bindings_.end()) return label_for(it->second);
    // A loaded-but-not-yet-registered binding is still worth showing in UI.
    it = pending_.find(name);
    if (it != pending_.end()) return label_for(it->second);
    return "-";
}

// ---------------------------------------------------------------------------
// Message pump + dispatch
// ---------------------------------------------------------------------------

void MidiControl::poll() {
    if (!midi_in_) return;
    try {
        std::vector<unsigned char> msg;
        // Drain everything queued since last frame. getMessage returns an
        // empty vector when the queue is empty.
        for (;;) {
            msg.clear();
            midi_in_->getMessage(&msg);
            if (msg.empty()) break;
            handle_message(msg);
        }
    } catch (const RtMidiError&) {
        // Device likely vanished mid-session (unplugged). Drop to closed
        // state; the app can re-init from the device dropdown.
        close();
    } catch (...) {
        // Never propagate — a user callback misbehaving must not kill poll's
        // caller (the render loop). Remaining queue is retried next frame.
    }
}

void MidiControl::handle_message(const std::vector<unsigned char>& msg) {
    // Channel voice messages we care about are exactly 3 bytes; anything
    // shorter (or a system message >= 0xF0) is ignored as malformed/irrelevant.
    if (msg.size() < 3) return;
    const unsigned char status = msg[0];
    if (status >= 0xF0) return;

    const int type    = status & 0xF0;
    const int channel = status & 0x0F;
    const int number  = msg[1] & 0x7F;
    const int value   = msg[2] & 0x7F;

    if (type == 0xB0) {  // Control Change
        // Learn consumes the first CC entirely: bind, exit learn, do NOT fire
        // the callback for this message (constraint: learn must not trigger).
        if (learn_mode_ == LearnMode::Param) {
            bindings_[learn_target_] = { BindType::CC, channel, number };
            pending_.erase(learn_target_);  // fresh learn supersedes stale disk state
            learn_mode_ = LearnMode::None;
            learn_target_.clear();
            return;
        }
        const float norm = static_cast<float>(value) / 127.0f;
        // Collect matching callbacks first, invoke after: a callback may call
        // register_*/begin_learn_* and mutate the maps we'd be iterating.
        std::vector<std::function<void(float)>> to_fire;
        for (const auto& [name, bind] : bindings_) {
            if (bind.type != BindType::CC) continue;
            if (bind.channel != channel || bind.number != number) continue;
            auto cb = params_.find(name);
            if (cb != params_.end() && cb->second) to_fire.push_back(cb->second);
        }
        for (auto& fn : to_fire) fn(norm);
    } else if (type == 0x90) {  // Note-On
        // Velocity 0 is Note-Off in disguise (running status) — never triggers.
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
    // 0x80 (Note-Off) and everything else: intentionally ignored.
}

// ---------------------------------------------------------------------------
// Persistence — name -> { type, channel (1-based), number }
// ---------------------------------------------------------------------------

bool MidiControl::save(const std::string& path) const {
    try {
        nlohmann::json b = nlohmann::json::object();
        auto dump = [&b](const std::map<std::string, Binding>& src) {
            for (const auto& [name, bind] : src) {
                b[name] = {
                    { "type",    bind.type == BindType::CC ? "cc" : "note" },
                    { "channel", bind.channel + 1 },  // 1-based on disk (human-editable)
                    { "number",  bind.number },
                };
            }
        };
        dump(bindings_);
        dump(pending_);  // don't lose not-yet-registered bindings on round-trip

        nlohmann::json j;
        j["bindings"] = std::move(b);

        // u8path: MSVC's narrow ofstream uses the ANSI codepage, which breaks
        // on non-ASCII paths (e.g. C:\Users\Майя). Route through fs::path.
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
            else continue;  // unknown type — skip, don't fail the whole file

            const int channel = entry.value("channel", 1) - 1;  // disk is 1-based
            const int number  = entry.value("number", -1);
            if (channel < 0 || channel > 15) continue;
            if (number  < 0 || number  > 127) continue;

            const Binding bind{ type, channel, number };
            // Reconcile now if the name is already registered with a matching
            // type; otherwise park in pending_ until register_* claims it.
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
