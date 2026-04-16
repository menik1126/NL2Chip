import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 12-hour clock counter with BCD seconds (00-59), minutes (00-59), hours (01-12),
    and am/pm indicator. Synchronous reset to 12:00:00 AM. Enable controls counting. -/
def prob141_count_clock {dom : DomainConfig}
    (reset : Signal dom Bool)
    (ena   : Signal dom Bool)
    : Signal dom (Bool × BitVec 8 × BitVec 8 × BitVec 8) :=
  -- State: 25 bits = {pm(1), hh(8), mm(8), ss(8)}
  -- Reset value: pm=0, hh=0x12, mm=0x00, ss=0x00
  -- 0x120000 = 0*2^24 + 0x12*2^16 + 0*2^8 + 0 = 18*65536 = 1179648
  let stateReg : Signal dom (BitVec 25) :=
    Signal.loop fun (state : Signal dom (BitVec 25)) =>
      -- Extract 4-bit BCD nibbles using shift + mask
      let ss_ones : Signal dom (BitVec 25) := state &&& 15#25
      let ss_tens : Signal dom (BitVec 25) := (state >>> 4#25) &&& 15#25
      let mm_ones : Signal dom (BitVec 25) := (state >>> 8#25) &&& 15#25
      let mm_tens : Signal dom (BitVec 25) := (state >>> 12#25) &&& 15#25
      let hh_ones : Signal dom (BitVec 25) := (state >>> 16#25) &&& 15#25
      let hh_tens : Signal dom (BitVec 25) := (state >>> 20#25) &&& 15#25
      let pm_bit  : Signal dom (BitVec 25) := (state >>> 24#25) &&& 1#25

      -- Enable carry conditions (Bool signals)
      let en1 : Signal dom Bool := ss_ones === (9#25 : BitVec 25)
      let en2 : Signal dom Bool := en1 &&& (ss_tens === (5#25 : BitVec 25))
      let en3 : Signal dom Bool := en2 &&& (mm_ones === (9#25 : BitVec 25))
      let en4 : Signal dom Bool := en3 &&& (mm_tens === (5#25 : BitVec 25))
      let en5 : Signal dom Bool := en4 &&& (hh_ones === (9#25 : BitVec 25))
      let en6 : Signal dom Bool := en4 &&& (hh_tens === (1#25 : BitVec 25)) &&& (hh_ones === (1#25 : BitVec 25))

      -- Next ss_ones: 0→1→...→9→0
      let nso : Signal dom (BitVec 25) :=
        Signal.mux en1 (Signal.pure 0#25) (ss_ones + 1#25)
      -- Next ss_tens: 0→1→...→5→0 (when en1)
      let nst : Signal dom (BitVec 25) :=
        Signal.mux en2 (Signal.pure 0#25)
          (Signal.mux en1 (ss_tens + 1#25) ss_tens)
      -- Next mm_ones: 0→1→...→9→0 (when en2)
      let nmo : Signal dom (BitVec 25) :=
        Signal.mux en3 (Signal.pure 0#25)
          (Signal.mux en2 (mm_ones + 1#25) mm_ones)
      -- Next mm_tens: 0→1→...→5→0 (when en3)
      let nmt : Signal dom (BitVec 25) :=
        Signal.mux en4 (Signal.pure 0#25)
          (Signal.mux en3 (mm_tens + 1#25) mm_tens)
      -- Next hh: 01→02→...→12→01 (when en4)
      let hh_is_12 : Signal dom Bool :=
        (hh_tens === (1#25 : BitVec 25)) &&& (hh_ones === (2#25 : BitVec 25))
      let nho : Signal dom (BitVec 25) :=
        Signal.mux en4
          (Signal.mux hh_is_12 (Signal.pure 1#25)
            (Signal.mux en5 (Signal.pure 0#25) (hh_ones + 1#25)))
          hh_ones
      let nht : Signal dom (BitVec 25) :=
        Signal.mux en4
          (Signal.mux hh_is_12 (Signal.pure 0#25)
            (Signal.mux en5 (hh_tens + 1#25) hh_tens))
          hh_tens
      -- Next pm: toggle at 11:59:59 (when en6)
      let npm : Signal dom (BitVec 25) :=
        Signal.mux en6
          (Signal.mux (pm_bit === (0#25 : BitVec 25)) (Signal.pure 1#25) (Signal.pure 0#25))
          pm_bit

      -- Pack next state: {pm[0], hh[7:0], mm[7:0], ss[7:0]}
      let nextState : Signal dom (BitVec 25) :=
        (npm <<< 24#25) |||
        (nht <<< 20#25) |||
        (nho <<< 16#25) |||
        (nmt <<< 12#25) |||
        (nmo <<< 8#25)  |||
        (nst <<< 4#25)  |||
        nso

      -- Apply enable (hold state when not enabled) and synchronous reset
      let nextWithEna : Signal dom (BitVec 25) :=
        Signal.mux ena nextState state
      let nextWithReset : Signal dom (BitVec 25) :=
        Signal.mux reset (Signal.pure 1179648#25) nextWithEna

      Signal.register 1179648#25 nextWithReset

  -- Extract output fields
  let ss_out : Signal dom (BitVec 8) :=
    Signal.map (fun s => BitVec.extractLsb' 0 8 s) stateReg
  let mm_out : Signal dom (BitVec 8) :=
    Signal.map (fun s => BitVec.extractLsb' 8 8 s) stateReg
  let hh_out : Signal dom (BitVec 8) :=
    Signal.map (fun s => BitVec.extractLsb' 16 8 s) stateReg
  let pm_out : Signal dom Bool :=
    (Signal.map (fun s => BitVec.extractLsb' 24 1 s) stateReg) === (1#1 : BitVec 1)

  bundle2 pm_out (bundle2 hh_out (bundle2 mm_out ss_out))

#synthesizeVerilog prob141_count_clock
