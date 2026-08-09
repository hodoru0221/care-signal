#pragma once

#include <stdint.h>
#include <string>

namespace care_signal {

enum class SoundPattern { NORMAL, GENTLE_ONCE, URGENT_REPEAT };
enum class AlertAction { NONE, STOP_SOUND, PLAY_GENTLE, PLAY_URGENT };

struct AlertSnapshot {
  bool sound;
  SoundPattern pattern;
  std::string event_id;
};

class AlertLogic {
 public:
  static const uint32_t kUrgentRepeatMs = 1500;

  AlertLogic();
  AlertAction apply(const AlertSnapshot& snapshot, uint32_t now_ms);
  AlertAction failSafe();
  AlertAction tick(uint32_t now_ms);

 private:
  bool urgent_active_;
  uint32_t last_urgent_ms_;
  std::string active_event_id_;
  std::string last_gentle_event_id_;
};

}  // namespace care_signal
