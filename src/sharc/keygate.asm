// keygate.asm - the Keyed Gate block's kernel: a noise gate keyed from the Input block, as a Boss NS-2 in the loop
// or a ZUUL with its key input, in one block.
//
// The key: the Input block's noise gate runs primitive 0x2720 (a gate core with its own key input) once for each
// channel, on the clean guitar, first in every frame. KG_TAP takes 0x2720's process entry: when a Keyed Gate has
// asked for it (KG_ARMED, set at the end of each Gate call to the two calls the Input block makes) it publishes
// the largest sample of the 16 words at I1 (KG_PEAK, KG_STAMP bumped), then runs the stock code. The stock Noise
// Gate block runs 0x2720 too: one placed after a Keyed Gate takes the key from it instead, so place it before.
// The Input block's gate must be On for this (its Threshold as low as -96 dB leaves the input ungated).
//
// The block: a primitive of the stock HX Stomp 3.80 firmware, called once per 16-sample block with I0 the
// instance's DM block, I1 the input and I2 the output (the same buffer when it runs in place). With Key on Input
// and the key fresh (KG_STAMP moved within STALE_MAX calls) it gates on the key; otherwise (Key on Self, or the
// Input gate Off) it gates on its own input, as a plain gate.
//
// Knobs (the generic store puts pid p in DM(p, I0)):
//   DM(5)  Key: 0 Input, 1 Self
//   DM(6)  Open Threshold, dB: the gate opens when the level reaches it
//   DM(7)  Close Threshold, dB: once open, the gate stays open down to it (no higher than Open)
//   DM(8)  Hold, s: how long it stays open after the level falls under Close
//   DM(9)  Decay, s: the time constant of every fall in gain
//   DM(10) Type: 0 Smooth (under Close, a 1:4 downward expander: the gain falls with the level, so a note fades
//          away instead of being cut), 1 Hard (under Close, after Hold, the gain goes to 0)
// The level is a peak follower of the 16-sample peaks, falling with a 20 ms time constant, so the gate does
// not chatter on a low note's waveform. The gate opens in 0.5 ms (time constant).

.equ G_ENV,    20                       // the level
.equ G_GAIN,   21                       // the gain applied, 0..1
.equ G_HOLD,   22                       // blocks left to hold open
.equ G_OPEN,   23                       // not 0 while open
.equ G_STAMP,  24                       // KG_STAMP as last seen
.equ G_STALE,  25                       // calls since KG_STAMP last changed
.equ C_SAVE,   100                      // the caller's registers

.equ PEYEN,    0x200000
.equ BDCST9,   0x400000
.equ STALE_MAX, 8
.equ ORIG_2720, 0x8e8f2                 // the stock process code of primitive 0x2720 (the Input block's and Noise Gate's core)
.equ TAPS,     2                        // the Input block's 0x2720 calls a frame: one a channel

.section dm KIT_DM_BASE
KG_PEAK:  .space 1                      // the Input block's last 16-sample peak, the larger of its two channels
KG_STAMP: .space 1                      // bumped by each published call
KG_ARMED: .space 1                      // 0x2720 calls still to publish: TAPS after each Gate call, then down to 0
KT_SAVE:  .space 6                      // KG_TAP's registers

.section pm KIT_PM_BASE

CUSTOM_AMP_ENTRY:
    DM(C_SAVE + 0, I0) = MODE1;
    BIT CLR MODE1 PEYEN | BDCST9;
    DM(C_SAVE + 1, I0) = R0;
    DM(C_SAVE + 2, I0) = R1;
    DM(C_SAVE + 3, I0) = R2;
    DM(C_SAVE + 4, I0) = R3;
    DM(C_SAVE + 5, I0) = R4;
    DM(C_SAVE + 6, I0) = R5;
    DM(C_SAVE + 7, I0) = R6;
    DM(C_SAVE + 8, I0) = R7;
    DM(C_SAVE + 9, I0) = R8;
    DM(C_SAVE + 10, I0) = R9;
    DM(C_SAVE + 11, I0) = R10;
    DM(C_SAVE + 12, I0) = R11;
    DM(C_SAVE + 13, I0) = R12;
    DM(C_SAVE + 14, I0) = R13;
    DM(C_SAVE + 15, I0) = R14;
    R0 = I4;                            // (a DAG1 register cannot be stored through I0)
    DM(C_SAVE + 16, I0) = R0;
    R0 = L4;
    DM(C_SAVE + 17, I0) = R0;
    R0 = I5;
    DM(C_SAVE + 18, I0) = R0;
    R0 = L5;
    DM(C_SAVE + 19, I0) = R0;
    L4 = 0;
    L5 = 0;

    I4 = I1;                            // the block's peak
    F1 = 0.0;
    LCNTR = 16, DO g_peak UNTIL LCE;
      F2 = DM(I4, 1);
      F2 = ABS F2;
g_peak: F1 = MAX(F1, F2);

    R0 = DM(KG_STAMP);                  // the detector: the Input block's peak while it comes, else this input's
    R2 = DM(G_STAMP, I0);
    R3 = DM(G_STALE, I0);
    COMP(R0, R2);
    IF NE R3 = R3 - R3;
    IF EQ R3 = R3 + 1;
    R4 = STALE_MAX;
    R3 = MIN(R3, R4);
    DM(G_STAMP, I0) = R0;
    DM(G_STALE, I0) = R3;
    F2 = DM(KG_PEAK);
    R5 = DM(5, I0);                     // Key on Self: this input's
    R5 = PASS R5;
    IF NE JUMP g_level;
    COMP(R3, R4);
    IF LT F1 = PASS F2;
g_level:

    F3 = DM(G_ENV, I0);                 // the level: the peak, or the last level falling
    F4 = 0.98347;                       // 20 ms a 16-sample block: 1 - 16 / (0.02 * 48000)
    F3 = F3 * F4;
    F3 = MAX(F3, F1);
    DM(G_ENV, I0) = F3;

    F8 = DM(6, I0);                     // Open, then Close (no higher than Open), as gains
    CALL g_db;
    F13 = PASS F6;
    F8 = DM(7, I0);
    CALL g_db;
    F14 = MIN(F6, F13);

    F8 = DM(8, I0);                     // Hold in blocks: 48000 / 16 a second
    F9 = 3000.0;
    F8 = F8 * F9;
    R12 = FIX F8;
    R9 = 0;
    R12 = MAX(R12, R9);

    R5 = DM(G_OPEN, I0);
    R6 = DM(G_HOLD, I0);
    COMP(F3, F13);                      // at Open: open, hold from now
    IF GE JUMP g_opened;
    R5 = PASS R5;
    IF EQ JUMP g_state;                 // closed and under Open: stays closed
    COMP(F3, F14);                      // open and at Close: hold from now
    IF GE JUMP g_opened;
    R6 = R6 - 1;                        // open and under Close: hold runs out, then it closes
    IF LT R5 = R5 - R5;
    R9 = 0;
    R6 = MAX(R6, R9);
    JUMP g_state;
g_opened:
    R5 = 1;
    R6 = PASS R12;
g_state:
    DM(G_OPEN, I0) = R5;
    DM(G_HOLD, I0) = R6;

    R5 = PASS R5;                       // the target gain: open 1
    F7 = 1.0;
    IF NE JUMP g_target;
    F7 = 0.0;                           // closed, Hard: 0
    R8 = DM(10, I0);                    // Type: 0 Smooth, 1 Hard
    R8 = PASS R8;
    IF NE JUMP g_target;
    F8 = PASS F14;                      // closed, Smooth: (level / Close)^3, a 1:4 expander under Close
    F0 = RECIPS F8;
    F9 = 2.0;
    F12 = F8 * F0;
    F12 = F9 - F12;
    F0 = F0 * F12;
    F12 = F8 * F0;
    F12 = F9 - F12;
    F0 = F0 * F12;
    F7 = F3 * F0;
    F9 = 1.0;
    F7 = MIN(F7, F9);
    F8 = F7 * F7;
    F7 = F8 * F7;
g_target:
    F8 = DM(9, I0);                     // the step down: Decay, 1 - 1 / (Decay x 48000)
    F9 = 48000.0;
    F8 = F8 * F9;
    F9 = 24.0;                          // (no faster than opening)
    F8 = MAX(F8, F9);
    F0 = RECIPS F8;                     // 1 / F8, refined twice
    F9 = 2.0;
    F12 = F8 * F0;
    F12 = F9 - F12;
    F0 = F0 * F12;
    F12 = F8 * F0;
    F12 = F9 - F12;
    F0 = F0 * F12;
    F9 = 1.0;
    F5 = F9 - F0;
    F4 = DM(G_GAIN, I0);                // the step up: 0.5 ms, 1 - 1 / 24
    F9 = 0.95833333;
    COMP(F7, F4);
    IF GE F5 = PASS F9;

    I4 = I1;
    I5 = I2;
    LCNTR = 16, DO g_last UNTIL LCE;
      F8 = F4 - F7;                     // gain = target + (gain - target) x step
      F8 = F8 * F5;
      F4 = F8 + F7;
      F2 = DM(I4, 1);
      F2 = F2 * F4;
g_last: DM(I5, 1) = F2;
    DM(G_GAIN, I0) = F4;
    R0 = TAPS;                          // the Input block's next two 0x2720 calls publish
    DM(KG_ARMED) = R0;

    R0 = DM(C_SAVE + 16, I0);
    I4 = R0;
    R0 = DM(C_SAVE + 17, I0);
    L4 = R0;
    R0 = DM(C_SAVE + 18, I0);
    I5 = R0;
    R0 = DM(C_SAVE + 19, I0);
    L5 = R0;
    R0 = DM(C_SAVE + 1, I0);
    R1 = DM(C_SAVE + 2, I0);
    R2 = DM(C_SAVE + 3, I0);
    R3 = DM(C_SAVE + 4, I0);
    R4 = DM(C_SAVE + 5, I0);
    R5 = DM(C_SAVE + 6, I0);
    R6 = DM(C_SAVE + 7, I0);
    R7 = DM(C_SAVE + 8, I0);
    R8 = DM(C_SAVE + 9, I0);
    R9 = DM(C_SAVE + 10, I0);
    R10 = DM(C_SAVE + 11, I0);
    R11 = DM(C_SAVE + 12, I0);
    R12 = DM(C_SAVE + 13, I0);
    R13 = DM(C_SAVE + 14, I0);
    R14 = DM(C_SAVE + 15, I0);
    MODE1 = DM(C_SAVE + 0, I0);
    NOP;
    RTS (LR);                           // as NAM's kernel returns

// In place of primitive 0x2720's process code: publishes the clean guitar's peak when armed, then runs the
// stock code with every register as it came.
KG_TAP:
    DM(KT_SAVE + 0) = MODE1;
    BIT CLR MODE1 PEYEN | BDCST9;
    NOP;
    DM(KT_SAVE + 1) = R0;
    DM(KT_SAVE + 2) = R1;
    DM(KT_SAVE + 3) = R2;
    DM(KT_SAVE + 4) = I4;
    DM(KT_SAVE + 5) = L4;
    R0 = DM(KG_ARMED);
    R0 = PASS R0;
    IF EQ JUMP kt_out;
    L4 = 0;
    I4 = I1;
    F1 = 0.0;
    LCNTR = 16, DO kt_peak UNTIL LCE;
      F2 = DM(I4, 1);
      F2 = ABS F2;
kt_peak: F1 = MAX(F1, F2);
    R2 = TAPS;                          // the first of the frame replaces the peak, the second raises it
    COMP(R0, R2);
    IF EQ JUMP kt_put;
    F2 = DM(KG_PEAK);
    F1 = MAX(F1, F2);
kt_put:
    DM(KG_PEAK) = F1;
    R0 = R0 - 1;
    DM(KG_ARMED) = R0;
    R0 = DM(KG_STAMP);
    R0 = R0 + 1;
    DM(KG_STAMP) = R0;
kt_out:
    R0 = DM(KT_SAVE + 1);
    R1 = DM(KT_SAVE + 2);
    R2 = DM(KT_SAVE + 3);
    I4 = DM(KT_SAVE + 4);
    L4 = DM(KT_SAVE + 5);
    MODE1 = DM(KT_SAVE + 0);
    NOP;
    JUMP ORIG_2720;

// F8 dB (-96 .. 0) -> F6 = 10^(dB / 20), as 2^(dB / 20 log2 10) with a cubic for 2^f. Uses R9..R11.
g_db:
    F9 = 0.16609640474;
    F8 = F8 * F9;
    F9 = 16.0;                          // (+16 keeps it positive for TRUNC: dB >= -96)
    F8 = F8 + F9;
    R10 = TRUNC F8;
    F11 = FLOAT R10;
    F8 = F8 - F11;
    F6 = 0.0781633;
    F6 = F6 * F8;
    F9 = 0.2261586;
    F6 = F6 + F9;
    F6 = F6 * F8;
    F9 = 0.6951786;
    F6 = F6 + F9;
    F6 = F6 * F8;
    F9 = 1.0;
    F6 = F6 + F9;
    R9 = 16;
    R10 = R10 - R9;
    F6 = SCALB F6 BY R10;
    RTS;
