#include "AlertLogic.h"

namespace care_signal {

AlertLogic::AlertLogic() : urgent_active_(false), last_urgent_ms_(0) {}

AlertAction AlertLogic::apply(const AlertSnapshot& snapshot, uint32_t now_ms) {
  if (!snapshot.sound || snapshot.pattern == SoundPattern::NORMAL) {
    urgent_active_ = false;
    active_event_id_.clear();
    return AlertAction::STOP_SOUND;
  }

  if (snapshot.pattern == SoundPattern::GENTLE_ONCE) {
    urgent_active_ = false;
    active_event_id_ = snapshot.event_id;
    if (!snapshot.event_id.empty() && snapshot.event_id != last_gentle_event_id_) {
      last_gentle_event_id_ = snapshot.event_id;
      return AlertAction::PLAY_GENTLE;
    }
    return AlertAction::NONE;
  }

  const bool changed = !urgent_active_ || active_event_id_ != snapshot.event_id;
  urgent_active_ = true;
  active_event_id_ = snapshot.event_id;
  if (changed) {
    last_urgent_ms_ = now_ms;
    return AlertAction::PLAY_URGENT;
  }
  return AlertAction::NONE;
}

AlertAction AlertLogic::failSafe() {
  urgent_active_ = false;
  active_event_id_.clear();
  return AlertAction::STOP_SOUND;
}

AlertAction AlertLogic::tick(uint32_t now_ms) {
  if (urgent_active_ && static_cast<uint32_t>(now_ms - last_urgent_ms_) >= kUrgentRepeatMs) {
    last_urgent_ms_ = now_ms;
    return AlertAction::PLAY_URGENT;
  }
  return AlertAction::NONE;
}

}  // namespace care_signal
