#pragma once
#include <string>
#include <vector>
#include <functional>
#include <memory>
#include <map>

class RtMidiIn;  // fwd decl - keeps <RtMidi.h> out of this header

// MidiControl - optional hardware-MIDI mapping layer for the VJ engine.
//
// Model:
//   * Continuous params (knobs/faders)  -> MIDI CC        -> on_value(float 0..1)
//   * Momentary actions (pads/buttons)  -> MIDI Note-On   -> on_trigger()
//
// Polling only: the app calls poll() once per render frame from the main
// thread. No background threads, no RtMidi callbacks - nothing to lock
// against the render loop.
//
// Fully optional at runtime: if no backend/device exists, init() returns
// false and every other call is a safe no-op. No public method ever throws.
class MidiControl {
public:
    MidiControl();
    ~MidiControl();

    // Open the first available MIDI input port (or a named one). Returns false
    // if no port / no backend. Safe to call again to reopen.
    bool init(int port_index = 0);
    bool is_open() const;
    void close();

    // List input port names (for a device dropdown). Empty if none.
    std::vector<std::string> list_ports() const;
    int  current_port() const;

    // Register a continuous parameter: called with a value in [0,1] whenever a
    // bound MIDI CC moves. `name` is a stable id used for persistence + the UI.
    void register_param(const std::string& name, std::function<void(float)> on_value);
    // Register a momentary action: called once on a bound Note-On (velocity>0).
    void register_action(const std::string& name, std::function<void()> on_trigger);

    // MIDI-Learn: the next CC message binds to `name` (for params) / next Note
    // binds to `name` (for actions). Call begin_learn again to rebind; pass ""
    // to cancel. Learning a param listens for CC; learning an action listens
    // for Note-On. Provide is_learning()/learning_target() for UI feedback.
    void begin_learn_param(const std::string& name);
    void begin_learn_action(const std::string& name);
    void clear_binding(const std::string& name);
    bool is_learning() const;
    std::string learning_target() const;

    // Human-readable current binding for a name ("CC 74 ch1", "Note 36", or "-").
    std::string binding_label(const std::string& name) const;

    // Pump pending MIDI messages; invokes the registered callbacks. Call once
    // per frame from the render thread.
    void poll();

    // Persistence (bindings only, not the callbacks).
    bool save(const std::string& path) const;
    bool load(const std::string& path);

private:
    enum class BindType { CC, Note };
    struct Binding {
        BindType type    = BindType::CC;
        int      channel = 0;   // 0..15 (0-based internally; shown/saved 1-based)
        int      number  = 0;   // CC number or note number, 0..127
    };
    enum class LearnMode { None, Param, Action };

    void handle_message(const std::vector<unsigned char>& msg);
    void reconcile_pending(const std::string& name, BindType expected_type);
    static std::string label_for(const Binding& b);

    std::unique_ptr<RtMidiIn> midi_in_;
    int current_port_ = -1;

    // Registered callbacks (by stable name).
    std::map<std::string, std::function<void(float)>> params_;
    std::map<std::string, std::function<void()>>      actions_;

    // Active bindings: name -> (type, channel, number). Several names may
    // share one physical control; dispatch scans all bindings per message.
    std::map<std::string, Binding> bindings_;
    // Bindings loaded from disk whose names have no registered callback yet.
    // Moved into bindings_ when register_param/register_action sees the name.
    std::map<std::string, Binding> pending_;

    // MIDI-Learn state machine (see midi_control.cpp for the flow).
    LearnMode   learn_mode_ = LearnMode::None;
    std::string learn_target_;
};
