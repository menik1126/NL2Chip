import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- gshare branch predictor
-- State = 263 bits: [262:256] = history (7 bits), [255:0] = PHT (256 bits)
-- PHT[i] = 2-bit saturating counter, initialized to 01 (LNT)

-- Initial PHT: 128 entries each = 01 (LNT), total 256 bits
-- 0x5555... = alternating bits 0101...
private def initPHT : BitVec 256 :=
  BitVec.ofNat 256 0x5555555555555555555555555555555555555555555555555555555555555555

private def initState : BitVec 263 :=
  initPHT.zeroExtend 263  -- history = 0, PHT = initPHT

-- Extract 2-bit PHT entry at 7-bit index
private def getPHT (pht : BitVec 256) (idx : BitVec 7) : BitVec 2 :=
  BitVec.extractLsb' (idx.toNat * 2) 2 pht

-- Set 2-bit PHT entry at 7-bit index
private def setPHT (pht : BitVec 256) (idx : BitVec 7) (val : BitVec 2) : BitVec 256 :=
  let bp := idx.toNat * 2
  (pht &&& ~~~((3#256) <<< bp)) ||| ((val.zeroExtend 256) <<< bp)

-- Saturating 2-bit increment
private def inc2 (v : BitVec 2) : BitVec 2 :=
  if v == 3#2 then 3#2 else v + 1#2

-- Saturating 2-bit decrement
private def dec2 (v : BitVec 2) : BitVec 2 :=
  if v == 0#2 then 0#2 else v - 1#2

-- Full state transition function (pure, no Signal)
-- Input encoding in inp27 (27 bits):
--   [26:20] = predict_pc (7 bits)
--   [19:13] = train_pc   (7 bits)
--   [12:6]  = train_history (7 bits)
--   [5]     = train_taken
--   [4]     = train_mispredicted
--   [3]     = train_valid
--   [2]     = predict_valid
--   [1]     = areset
--   [0]     = unused (pad to byte boundary)
-- State encoding in s263 (263 bits):
--   [262:256] = history (7 bits)
--   [255:0]   = PHT (256 bits)
private def gshareNextState (s263 : BitVec 263) (inp27 : BitVec 27) : BitVec 263 :=
  -- Extract state
  let pht := BitVec.extractLsb' 0 256 s263
  let history := BitVec.extractLsb' 256 7 s263
  -- Extract inputs
  let predict_pc     := BitVec.extractLsb' 20 7 inp27
  let train_pc       := BitVec.extractLsb' 13 7 inp27
  let train_history  := BitVec.extractLsb' 6  7 inp27
  let train_taken    := (BitVec.extractLsb' 5 1 inp27) == 1#1
  let train_mispred  := (BitVec.extractLsb' 4 1 inp27) == 1#1
  let train_valid    := (BitVec.extractLsb' 3 1 inp27) == 1#1
  let predict_valid  := (BitVec.extractLsb' 2 1 inp27) == 1#1
  let do_areset      := (BitVec.extractLsb' 1 1 inp27) == 1#1
  -- Compute predict_taken from current state (combinational, before update)
  let predict_index  := history ^^^ predict_pc
  let pht_entry      := getPHT pht predict_index
  let predict_taken  := pht_entry.msb  -- bit 1 of 2-bit counter
  -- Compute next PHT
  let train_index    := train_history ^^^ train_pc
  let train_entry    := getPHT pht train_index
  let new_entry      := if train_taken then inc2 train_entry else dec2 train_entry
  let pht_trained    := setPHT pht train_index new_entry
  let next_pht       := if do_areset then initPHT
                        else if train_valid then pht_trained
                        else pht
  -- Compute next history
  -- Recovery: {train_history, train_taken} = (train_history << 1) | train_taken_bit
  let recovery_hist  := (train_history <<< 1) ||| (if train_taken then 1#7 else 0#7)
  -- Predict shift: {history[5:0], predict_taken} = (history << 1) | predict_taken_bit
  let predict_hist   := (history <<< 1) ||| (if predict_taken then 1#7 else 0#7)
  let next_hist      := if do_areset then 0#7
                        else if train_valid && train_mispred then recovery_hist
                        else if predict_valid then predict_hist
                        else history
  -- Pack next state
  (next_hist.zeroExtend 263 <<< 256) ||| next_pht.zeroExtend 263

-- Output function: predict_taken from current state + predict_pc
private def gshareOutput (s263 : BitVec 263) (inp27 : BitVec 27) : BitVec 8 :=
  let pht     := BitVec.extractLsb' 0 256 s263
  let history := BitVec.extractLsb' 256 7 s263
  let predict_pc := BitVec.extractLsb' 20 7 inp27
  let predict_valid := (BitVec.extractLsb' 2 1 inp27) == 1#1
  let predict_index := history ^^^ predict_pc
  let pht_entry := getPHT pht predict_index
  let predict_taken := if predict_valid then pht_entry.msb else false
  -- Pack output: [7] = predict_taken (1 bit), [6:0] = history (when predict_valid, else history)
  -- Actually outputs are predict_taken (1 bit) and predict_history = current history
  -- We'll pack: [7] = predict_taken, [6:0] = history
  let taken_bit : BitVec 8 := if predict_taken then 128#8 else 0#8
  taken_bit ||| history.zeroExtend 8

/-- gshare branch predictor with 7-bit PC and 7-bit global history,
    128-entry PHT of 2-bit saturating counters.
    Async active-high reset. Returns (predict_taken, predict_history). -/
def prob153_gshare {dom : DomainConfig}
    (areset             : Signal dom Bool)
    (predict_valid      : Signal dom Bool)
    (predict_pc         : Signal dom (BitVec 7))
    (train_valid        : Signal dom Bool)
    (train_taken        : Signal dom Bool)
    (train_mispredicted : Signal dom Bool)
    (train_history      : Signal dom (BitVec 7))
    (train_pc           : Signal dom (BitVec 7))
    : Signal dom (BitVec 1 × BitVec 7) :=
  -- Pack all inputs into a 27-bit word
  -- [26:20] = predict_pc (7), [19:13] = train_pc (7), [12:6] = train_history (7)
  -- [5] = train_taken, [4] = train_mispredicted, [3] = train_valid
  -- [2] = predict_valid, [1] = areset, [0] = 0
  let inp27 : Signal dom (BitVec 27) :=
    Signal.map (fun ppc => ppc.zeroExtend 27 <<< 20) predict_pc |||
    Signal.map (fun tpc => tpc.zeroExtend 27 <<< 13) train_pc |||
    Signal.map (fun thi => thi.zeroExtend 27 <<< 6)  train_history |||
    Signal.mux train_taken        (Signal.pure (32#27))  (Signal.pure 0#27) |||
    Signal.mux train_mispredicted (Signal.pure (16#27))  (Signal.pure 0#27) |||
    Signal.mux train_valid        (Signal.pure (8#27))   (Signal.pure 0#27) |||
    Signal.mux predict_valid      (Signal.pure (4#27))   (Signal.pure 0#27) |||
    Signal.mux areset             (Signal.pure (2#27))   (Signal.pure 0#27)

  -- State register: 263 bits
  let state : Signal dom (BitVec 263) :=
    Signal.loop fun (s : Signal dom (BitVec 263)) =>
      let next_s : Signal dom (BitVec 263) :=
        Signal.lift2 gshareNextState s inp27
      Signal.register initState next_s

  -- Compute outputs from current state and inputs (combinational)
  let output8 : Signal dom (BitVec 8) :=
    Signal.lift2 gshareOutput state inp27

  -- Extract outputs
  let predict_taken_out : Signal dom (BitVec 1) :=
    Signal.map (fun o => BitVec.extractLsb' 7 1 o) output8

  let predict_history_out : Signal dom (BitVec 7) :=
    Signal.map (fun o => BitVec.extractLsb' 0 7 o) output8

  bundle2 predict_taken_out predict_history_out

#synthesizeVerilog prob153_gshare
