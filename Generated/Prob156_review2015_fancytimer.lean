import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- FSM state encoding (4 bits)
private abbrev stS    : BitVec 4 := 0#4  -- searching
private abbrev stS1   : BitVec 4 := 1#4  -- seen 1
private abbrev stS11  : BitVec 4 := 2#4  -- seen 11
private abbrev stS110 : BitVec 4 := 3#4  -- seen 110
private abbrev stB0   : BitVec 4 := 4#4  -- loading bit 0
private abbrev stB1   : BitVec 4 := 5#4  -- loading bit 1
private abbrev stB2   : BitVec 4 := 6#4  -- loading bit 2
private abbrev stB3   : BitVec 4 := 7#4  -- loading bit 3
private abbrev stCnt  : BitVec 4 := 8#4  -- counting
private abbrev stWait : BitVec 4 := 9#4  -- waiting for ack

/-- Fancy timer: detects 1101 pattern, loads 4-bit delay, counts (delay+1)*1000 cycles,
    then asserts done and waits for ack.
    State packed as BitVec 18: [3:0]=FSM state, [13:4]=fcount(10-bit), [17:14]=scount(4-bit) -/
def prob156_review2015_fancytimer {dom : DomainConfig}
    (reset : Signal dom Bool)
    (data : Signal dom Bool)
    (ack : Signal dom Bool)
    : Signal dom (BitVec 4 × BitVec 1 × BitVec 1) :=
  -- Pack all state into an 18-bit register:
  --   bits [3:0]   = FSM state (0-9)
  --   bits [13:4]  = fcount (10 bits, 0-999)
  --   bits [17:14] = scount (4 bits)
  let combined : Signal dom (BitVec 18) :=
    Signal.loop fun (reg : Signal dom (BitVec 18)) =>
      -- Extract current state components using direct signal operators
      -- &&&, >>>, <<<, ||| work on Signal dom (BitVec n) directly
      let fsm       : Signal dom (BitVec 18) := reg &&& (15#18 : BitVec 18)
      let fcountRaw : Signal dom (BitVec 18) := (reg >>> (4#18 : BitVec 18)) &&& (1023#18 : BitVec 18)
      let scountRaw : Signal dom (BitVec 18) := (reg >>> (14#18 : BitVec 18)) &&& (15#18 : BitVec 18)

      -- Data as 18-bit bitvec for shift register
      let dataBit : Signal dom (BitVec 18) := Signal.mux data (Signal.pure 1#18) (Signal.pure 0#18)

      -- FSM state comparisons (fsm is BitVec 18, compare with zero-extended constants)
      let isS    := fsm === Signal.pure (stS.zeroExtend 18)
      let isS1   := fsm === Signal.pure (stS1.zeroExtend 18)
      let isS11  := fsm === Signal.pure (stS11.zeroExtend 18)
      let isS110 := fsm === Signal.pure (stS110.zeroExtend 18)
      let isB0   := fsm === Signal.pure (stB0.zeroExtend 18)
      let isB1   := fsm === Signal.pure (stB1.zeroExtend 18)
      let isB2   := fsm === Signal.pure (stB2.zeroExtend 18)
      let isB3   := fsm === Signal.pure (stB3.zeroExtend 18)
      let isCnt  := fsm === Signal.pure (stCnt.zeroExtend 18)
      let isWait := fsm === Signal.pure (stWait.zeroExtend 18)

      -- done_counting: scount==0 && fcount==999
      let scountZero  := scountRaw === Signal.pure 0#18
      let fcountAt999 := fcountRaw === Signal.pure 999#18
      let done_counting := scountZero &&& fcountAt999

      -- Next FSM state
      let nextFsm : Signal dom (BitVec 18) :=
        Signal.mux isS
          (Signal.mux data (Signal.pure (stS1.zeroExtend 18)) (Signal.pure (stS.zeroExtend 18)))
        (Signal.mux isS1
          (Signal.mux data (Signal.pure (stS11.zeroExtend 18)) (Signal.pure (stS.zeroExtend 18)))
        (Signal.mux isS11
          (Signal.mux data (Signal.pure (stS11.zeroExtend 18)) (Signal.pure (stS110.zeroExtend 18)))
        (Signal.mux isS110
          (Signal.mux data (Signal.pure (stB0.zeroExtend 18)) (Signal.pure (stS.zeroExtend 18)))
        (Signal.mux isB0 (Signal.pure (stB1.zeroExtend 18))
        (Signal.mux isB1 (Signal.pure (stB2.zeroExtend 18))
        (Signal.mux isB2 (Signal.pure (stB3.zeroExtend 18))
        (Signal.mux isB3 (Signal.pure (stCnt.zeroExtend 18))
        (Signal.mux isCnt
          (Signal.mux done_counting (Signal.pure (stWait.zeroExtend 18)) (Signal.pure (stCnt.zeroExtend 18)))
        (Signal.mux isWait
          (Signal.mux ack (Signal.pure (stS.zeroExtend 18)) (Signal.pure (stWait.zeroExtend 18)))
          (Signal.pure (stS.zeroExtend 18)))))))))))

      -- Next scount:
      -- In B0..B3: shift in data bit MSB first: scount = {scount[2:0], data}
      -- In Count, when fcount==999: decrement scount
      -- Otherwise: hold
      let shifting := isB0 ||| isB1 ||| isB2 ||| isB3
      -- shift: (scount & 0x7) << 1 | dataBit (in 18-bit space)
      let scountLow3  : Signal dom (BitVec 18) := scountRaw &&& (7#18 : BitVec 18)
      let scountShifted : Signal dom (BitVec 18) :=
        (scountLow3 <<< (1#18 : BitVec 18)) ||| dataBit
      let scountDecremented : Signal dom (BitVec 18) := scountRaw - (1#18 : BitVec 18)
      let nextScount : Signal dom (BitVec 18) :=
        Signal.mux shifting scountShifted
          (Signal.mux (isCnt &&& fcountAt999) scountDecremented scountRaw)

      -- Next fcount:
      -- Not counting: reset to 0
      -- Counting && fcount==999: reset to 0
      -- Counting && fcount<999: increment
      let fcountNext : Signal dom (BitVec 18) :=
        Signal.mux (~~~isCnt) (Signal.pure 0#18)
          (Signal.mux fcountAt999 (Signal.pure 0#18)
            (fcountRaw + (1#18 : BitVec 18)))

      -- Pack next state: [3:0]=FSM, [13:4]=fcount, [17:14]=scount
      let nextFsmMasked     : Signal dom (BitVec 18) := nextFsm &&& (15#18 : BitVec 18)
      let nextFcountShifted : Signal dom (BitVec 18) :=
        (fcountNext &&& (1023#18 : BitVec 18)) <<< (4#18 : BitVec 18)
      let nextScountShifted : Signal dom (BitVec 18) :=
        (nextScount &&& (15#18 : BitVec 18)) <<< (14#18 : BitVec 18)

      -- Combine: OR together the three parts
      let nextCombined : Signal dom (BitVec 18) :=
        nextFsmMasked ||| nextFcountShifted ||| nextScountShifted

      -- Apply synchronous reset
      let nextWithReset := Signal.mux reset (Signal.pure 0#18) nextCombined

      Signal.register 0#18 nextWithReset

  -- Extract outputs from combined state
  let fsm       : Signal dom (BitVec 18) := combined &&& (15#18 : BitVec 18)
  let scountRaw : Signal dom (BitVec 18) := (combined >>> (14#18 : BitVec 18)) &&& (15#18 : BitVec 18)

  let isCnt  := fsm === Signal.pure (stCnt.zeroExtend 18)
  let isWait := fsm === Signal.pure (stWait.zeroExtend 18)

  -- counting: in Count state
  let counting : Signal dom (BitVec 1) := Signal.mux isCnt (Signal.pure 1#1) (Signal.pure 0#1)
  -- done: in Wait state
  let done : Signal dom (BitVec 1) := Signal.mux isWait (Signal.pure 1#1) (Signal.pure 0#1)
  -- count: scount (4 bits) - truncate from 18-bit to 4-bit
  let count : Signal dom (BitVec 4) := Signal.map (fun s => s.truncate 4) scountRaw

  bundle2 count (bundle2 counting done)

#synthesizeVerilog prob156_review2015_fancytimer
