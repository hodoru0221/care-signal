#include "../AlertLogic.h"

#include <cassert>
#include <iostream>

using namespace care_signal;

int main() {
  AlertLogic logic;

  assert(logic.apply({false, SoundPattern::NORMAL, ""}, 0) == AlertAction::STOP_SOUND);
  assert(logic.apply({true, SoundPattern::GENTLE_ONCE, "event-1"}, 10) == AlertAction::PLAY_GENTLE);
  assert(logic.apply({true, SoundPattern::GENTLE_ONCE, "event-1"}, 1000) == AlertAction::NONE);
  assert(logic.failSafe() == AlertAction::STOP_SOUND);
  assert(logic.apply({true, SoundPattern::GENTLE_ONCE, "event-1"}, 2000) == AlertAction::NONE);
  assert(logic.apply({true, SoundPattern::GENTLE_ONCE, "event-2"}, 2100) == AlertAction::PLAY_GENTLE);

  assert(logic.apply({true, SoundPattern::URGENT_REPEAT, "event-3"}, 3000) == AlertAction::PLAY_URGENT);
  assert(logic.tick(4499) == AlertAction::NONE);
  assert(logic.tick(4500) == AlertAction::PLAY_URGENT);
  assert(logic.tick(6000) == AlertAction::PLAY_URGENT);

  // ACKNOWLEDGED is represented by the API as NORMAL/sound=false.
  assert(logic.apply({false, SoundPattern::NORMAL, ""}, 6100) == AlertAction::STOP_SOUND);
  assert(logic.tick(9000) == AlertAction::NONE);

  assert(logic.apply({true, SoundPattern::URGENT_REPEAT, "event-4"}, 0xFFFFFF00u) ==
         AlertAction::PLAY_URGENT);
  assert(logic.tick(0x000004DCu) == AlertAction::PLAY_URGENT);  // millis() rollover

  std::cout << "alert_logic_test: PASS\n";
  return 0;
}
