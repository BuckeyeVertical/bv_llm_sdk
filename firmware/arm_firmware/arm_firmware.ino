#include <Servo.h>
#include <EEPROM.h>

const uint8_t N = 4;
struct Hw { uint8_t pin; bool enabled; int minUs; int maxUs; float travel; };

Hw HW[N] = {
  {3, true, 500, 2500, 175.0},
  {5, true,  500, 2500, 300.0},
  {6, true,  500, 2500, 300.0},
  {9, true,  500, 2500, 300.0},
};
const float MAX_SPEED_DPS = 60.0;
const float CAL_SPEED_DPS = 20.0;
const long  BAUD = 115200;

const float DEFAULT_REF[N] = {90.0, 0.0, 180.0, 90.0};

struct Cal { float minDeg; float maxDeg; float refDeg; int8_t dir; };
struct Store { uint16_t magic; uint8_t version; Cal cal[N]; float park[N]; uint8_t hasPark; };
const uint16_t MAGIC = 0xA27E;
const uint8_t  STORE_VERSION = 1;
Store st;

Servo sv[N];
float cur[N], fromA[N], toA[N];
bool moving = false;
unsigned long moveT0 = 0, moveDur = 0;

bool pending = false;
float pendA[N];
unsigned long pendMs = 0;
uint8_t pendMask = 0;

bool waitIdle = false;
int  calServo = -1;
float calStep = 1.0;

char lineBuf[64];
uint8_t lineLen = 0;
bool lineReady = false;

void ok() { Serial.println(F("ok")); }
void err(const __FlashStringHelper* m) { Serial.print(F("err ")); Serial.println(m); }

float clampTravel(uint8_t i, float a) { return constrain(a, 0.0, HW[i].travel); }
float clampLimits(uint8_t i, float a) { return constrain(a, st.cal[i].minDeg, st.cal[i].maxDeg); }

void writeDeg(uint8_t i, float a) {
  long us = HW[i].minUs + (long)((a / HW[i].travel) * (HW[i].maxUs - HW[i].minUs) + 0.5);
  sv[i].writeMicroseconds((int)us);
}

void attachAt(uint8_t i, float a) {
  cur[i] = fromA[i] = toA[i] = a;
  writeDeg(i, a);
  sv[i].attach(HW[i].pin, HW[i].minUs, HW[i].maxUs);
  writeDeg(i, a);
}

void detachAll() {
  for (uint8_t i = 0; i < N; i++) if (sv[i].attached()) sv[i].detach();
}

void stopAll() {
  moving = false; pending = false; waitIdle = false;
  for (uint8_t i = 0; i < N; i++) toA[i] = fromA[i] = cur[i];
  detachAll();
}

void loadDefaults() {
  st.magic = MAGIC; st.version = STORE_VERSION; st.hasPark = 0;
  for (uint8_t i = 0; i < N; i++) {
    st.cal[i].minDeg = 0; st.cal[i].maxDeg = HW[i].travel;
    st.cal[i].refDeg = DEFAULT_REF[i]; st.cal[i].dir = 1;
    st.park[i] = DEFAULT_REF[i];
  }
}

void loadStore() {
  EEPROM.get(0, st);
  if (st.magic != MAGIC || st.version != STORE_VERSION) {
    loadDefaults();
    Serial.println(F("info no saved calibration, using defaults"));
  } else {
    Serial.println(F("info calibration loaded from EEPROM"));
  }
}

void saveStore() { EEPROM.put(0, st); }

int parseFloats(char* s, float* out, int maxN) {
  int n = 0;
  while (n < maxN) {
    while (*s == ' ' || *s == ',' || *s == '\t') s++;
    if (!*s) break;
    char* e;
    double d = strtod(s, &e);
    if (e == s) return -1;
    out[n++] = (float)d;
    s = e;
  }
  return n;
}

bool validServo(int i) { return i >= 0 && i < N; }

void startMove(const float* a, unsigned long ms, uint8_t mask) {
  float biggest = 0;
  for (uint8_t i = 0; i < N; i++) {
    fromA[i] = toA[i] = cur[i];
    if (!HW[i].enabled || !(mask & (1 << i))) continue;
    float t = clampLimits(i, a[i]);
    if (fabs(t - a[i]) > 0.05) {
      Serial.print(F("warn S")); Serial.print(i + 1);
      Serial.print(F(" clamped to ")); Serial.println(t, 1);
    }
    if (!sv[i].attached()) {
      attachAt(i, t);
      Serial.print(F("warn S")); Serial.print(i + 1);
      Serial.println(F(" was limp, attached directly at target"));
    }
    fromA[i] = cur[i];
    toA[i] = t;
    biggest = max(biggest, (float)fabs(t - cur[i]));
  }
  unsigned long minMs = (unsigned long)(biggest / MAX_SPEED_DPS * 1000.0);
  moveDur = max(ms, minMs);
  moveT0 = millis();
  moving = true;
}

void updateMotion() {
  if (!moving) return;
  unsigned long el = millis() - moveT0;
  float f = (moveDur == 0) ? 1.0 : min(1.0f, el / (float)moveDur);
  for (uint8_t i = 0; i < N; i++) {
    if (!HW[i].enabled || !sv[i].attached()) continue;
    cur[i] = fromA[i] + (toA[i] - fromA[i]) * f;
    writeDeg(i, cur[i]);
  }
  if (f >= 1.0) moving = false;
}

void printPos() {
  Serial.print(F("pos"));
  for (uint8_t i = 0; i < N; i++) { Serial.print(' '); Serial.print(cur[i], 1); }
  Serial.print(F(" att "));
  for (uint8_t i = 0; i < N; i++) Serial.print(sv[i].attached() ? '1' : '0');
  Serial.print(F(" mov ")); Serial.println((moving || pending) ? 1 : 0);
}

void printCfg() {
  for (uint8_t i = 0; i < N; i++) {
    Serial.print(F("cfg ")); Serial.print(i + 1);
    Serial.print(' '); Serial.print(st.cal[i].minDeg, 1);
    Serial.print(' '); Serial.print(st.cal[i].maxDeg, 1);
    Serial.print(' '); Serial.print(st.cal[i].refDeg, 1);
    Serial.print(' '); Serial.print((int)st.cal[i].dir);
    Serial.print(' '); Serial.print(HW[i].travel, 0);
    Serial.print(' '); Serial.println(HW[i].enabled ? 1 : 0);
  }
  Serial.print(F("info park "));
  if (!st.hasPark) Serial.println(F("not saved"));
  else { for (uint8_t i = 0; i < N; i++) { Serial.print(st.park[i], 1); Serial.print(' '); } Serial.println(); }
}

void printHelp() {
  Serial.println(F("info M a1 a2 a3 a4 [ms] | J n ang | A attach | D limp | X stop | Z wait"));
  Serial.println(F("info P pos | K save park | G cfg | L n min max | R n ref dir | W save | F defaults"));
  Serial.println(F("info C n [ang] calibrate: + - s g [ ] 0 ^ v p x"));
}

void processLine(char* s);

void calPrint() {
  uint8_t i = calServo;
  Serial.print(F("cal S")); Serial.print(i + 1);
  Serial.print(F(" at ")); Serial.print(cur[i], 1);
  Serial.print(F(" step ")); Serial.print(calStep, 1);
  Serial.print(F(" min ")); Serial.print(st.cal[i].minDeg, 1);
  Serial.print(F(" max ")); Serial.print(st.cal[i].maxDeg, 1);
  Serial.print(F(" ref ")); Serial.print(st.cal[i].refDeg, 1);
  Serial.print(F(" dir ")); Serial.println((int)st.cal[i].dir);
}

void calMoveTo(float a) {
  uint8_t i = calServo;
  a = clampTravel(i, a);
  float s = cur[i], d = a - s;
  int steps = max(1, (int)(fabs(d) / (CAL_SPEED_DPS * 0.02)));
  for (int k = 1; k <= steps; k++) { writeDeg(i, s + d * k / steps); delay(20); }
  cur[i] = fromA[i] = toA[i] = a;
}

void calEnter(int i, bool hasAngle, float a) {
  calServo = i;
  if (!sv[i].attached()) {
    float start = hasAngle ? a : (st.cal[i].minDeg + st.cal[i].maxDeg) / 2.0;
    start = clampTravel(i, start);
    Serial.print(F("warn attaching S")); Serial.print(i + 1);
    Serial.print(F(" at ")); Serial.println(start, 1);
    attachAt(i, start);
  } else if (hasAngle) {
    calMoveTo(a);
  }
  calPrint();
}

void calCommand(char* s) {
  uint8_t i = calServo;
  char c = s[0];
  switch (c) {
    case '+': calMoveTo(cur[i] + calStep); break;
    case '-': calMoveTo(cur[i] - calStep); break;
    case 's': case 'S': calStep = constrain(atof(s + 1), 0.1, 30.0); break;
    case 'g': case 'G': calMoveTo(atof(s + 1)); break;
    case '[': st.cal[i].minDeg = cur[i]; break;
    case ']': st.cal[i].maxDeg = cur[i]; break;
    case '0': st.cal[i].refDeg = cur[i]; break;
    case '^': st.cal[i].dir = 1; break;
    case 'v': case 'V': st.cal[i].dir = -1; break;
    case 'p': case 'P': break;
    case 'x':
      if (st.cal[i].minDeg >= st.cal[i].maxDeg) Serial.println(F("warn min is not below max; fix before drawing"));
      Serial.println(F("info left calibration. Send W to save to EEPROM."));
      calServo = -1;
      ok();
      return;
    case 'X':
      calServo = -1;
      stopAll();
      Serial.println(F("info stopped, servos limp"));
      ok();
      return;
    case 'C': case 'c':
    case 'W': case 'w':
      if (st.cal[i].minDeg >= st.cal[i].maxDeg) Serial.println(F("warn min is not below max; fix before drawing"));
      calServo = -1;
      processLine(s);
      return;
    default: err(F("calibration keys: + - s g [ ] 0 ^ v p x (C n switches servo, W saves)")); return;
  }
  calPrint();
  ok();
}

void processLine(char* s) {
  if (calServo >= 0) { calCommand(s); return; }

  if (s[0] == 'x') { err(F("not in calibration mode (capital X = stop)")); return; }

  char cmd = toupper(s[0]);
  float v[6];
  int n = parseFloats(s + 1, v, 6);
  if (n < 0) { err(F("bad number")); return; }

  switch (cmd) {
    case 'M':
      if (n < 4) { err(F("M needs 4 angles")); return; }
      for (uint8_t i = 0; i < N; i++) pendA[i] = v[i];
      pendMs = (n >= 5 && v[4] > 0) ? (unsigned long)v[4] : 0;
      pendMask = 0x0F;
      pending = true;
      ok();
      break;

    case 'J': {
      if (n < 2) { err(F("J needs servo and angle")); return; }
      int i = (int)v[0] - 1;
      if (!validServo(i) || !HW[i].enabled) { err(F("servo not enabled")); return; }
      if (pending) { err(F("busy")); return; }
      for (uint8_t k = 0; k < N; k++) pendA[k] = toA[k];
      pendA[i] = v[1];
      pendMs = 0; pendMask = (1 << i); pending = true;
      ok();
      break;
    }

    case 'A':
      for (uint8_t i = 0; i < N; i++) {
        if (!HW[i].enabled || sv[i].attached()) continue;
        float a = st.hasPark ? st.park[i] : st.cal[i].refDeg;
        attachAt(i, clampLimits(i, a));
      }
      printPos();
      ok();
      break;

    case 'D': stopAll(); ok(); break;
    case 'X': stopAll(); Serial.println(F("info stopped, servos limp")); ok(); break;
    case 'Z': waitIdle = true; break;
    case 'P': printPos(); ok(); break;

    case 'K':
      if (moving || pending) { err(F("busy, send Z first")); return; }
      for (uint8_t i = 0; i < N; i++) st.park[i] = cur[i];
      st.hasPark = 1;
      saveStore();
      Serial.println(F("info park pose saved"));
      ok();
      break;

    case 'G': printCfg(); ok(); break;

    case 'L': {
      if (n < 3) { err(F("L needs servo min max")); return; }
      int i = (int)v[0] - 1;
      if (!validServo(i)) { err(F("bad servo")); return; }
      float lo = clampTravel(i, v[1]), hi = clampTravel(i, v[2]);
      if (lo >= hi) { err(F("min must be below max")); return; }
      st.cal[i].minDeg = lo; st.cal[i].maxDeg = hi;
      ok();
      break;
    }

    case 'R': {
      if (n < 3) { err(F("R needs servo ref dir")); return; }
      int i = (int)v[0] - 1;
      if (!validServo(i)) { err(F("bad servo")); return; }
      st.cal[i].refDeg = clampTravel(i, v[1]);
      st.cal[i].dir = v[2] < 0 ? -1 : 1;
      ok();
      break;
    }

    case 'W': saveStore(); Serial.println(F("info calibration saved")); ok(); break;
    case 'F': loadDefaults(); Serial.println(F("info defaults loaded (not saved)")); ok(); break;

    case 'C': {
      if (n < 1) { err(F("C needs servo number")); return; }
      int i = (int)v[0] - 1;
      if (!validServo(i) || !HW[i].enabled) { err(F("servo not enabled")); return; }
      if (moving || pending) { err(F("busy, send Z first")); return; }
      calEnter(i, n >= 2, n >= 2 ? v[1] : 0);
      ok();
      break;
    }

    case 'H': case '?': printHelp(); ok(); break;
    default: err(F("unknown command, send H"));
  }
}

void setup() {
  Serial.begin(BAUD);
  loadStore();
  for (uint8_t i = 0; i < N; i++) {
    cur[i] = fromA[i] = toA[i] = st.hasPark ? st.park[i] : st.cal[i].refDeg;
  }
  Serial.println(F("info servos are limp. Pose the arm at park, then send A."));
  Serial.println(F("ready"));
}

void loop() {
  updateMotion();

  if (!moving && pending) { pending = false; startMove(pendA, pendMs, pendMask); }

  if (waitIdle) {
    if (!moving && !pending) { waitIdle = false; ok(); }
    return;
  }

  if (lineReady) {
    if (toupper(lineBuf[0]) == 'M' && pending) return;
    processLine(lineBuf);
    lineReady = false;
    lineLen = 0;
  }

  while (!lineReady && Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      lineBuf[lineLen] = 0;
      if (lineLen > 0) lineReady = true;
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    }
  }
}
