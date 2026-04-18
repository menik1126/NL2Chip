import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 12-hour clock with BCD outputs for hours (01-12), minutes (00-59), seconds (00-59), and AM/PM indicator -/
def prob141_count_clock {dom : DomainConfig}
    (ena : Signal dom Bool)
    (reset : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 8 × BitVec 8 × BitVec 8) :=
  -- State: [pm:1][hh:8][mm:8][ss:8] = 25 bits
  -- Initial: pm=0, hh=0x12, mm=0x00, ss=0x00
  let state : Signal dom (BitVec 25) :=
    Signal.loop fun (state : Signal dom (BitVec 25)) =>
      -- Extract current values
      let pm := Signal.map (fun s => s.extractLsb 24 24) state
      let hh := Signal.map (fun s => s.extractLsb 23 16) state
      let mm := Signal.map (fun s => s.extractLsb 15 8) state
      let ss := Signal.map (fun s => s.extractLsb 7 0) state
      
      -- Extract BCD digits
      let ss_ones := Signal.map (fun s => s.extractLsb 3 0) ss
      let ss_tens := Signal.map (fun s => s.extractLsb 7 4) ss
      let mm_ones := Signal.map (fun s => s.extractLsb 3 0) mm
      let mm_tens := Signal.map (fun s => s.extractLsb 7 4) mm
      let hh_ones := Signal.map (fun s => s.extractLsb 3 0) hh
      let hh_tens := Signal.map (fun s => s.extractLsb 7 4) hh
      
      -- Enable conditions (cascade)
      let en0 := ena
      let en1 := ena &&& (ss_ones === 9#4)
      let en2 := en1 &&& (ss_tens === 5#4)
      let en3 := en2 &&& (mm_ones === 9#4)
      let en4 := en3 &&& (mm_tens === 5#4)
      let en5 := en4 &&& (hh_ones === 9#4)
      let en6 := en4 &&& (hh === 0x12#8)
      
      -- Update seconds ones digit
      let ss_ones_next := Signal.mux (en0 &&& (ss_ones === 9#4))
        (Signal.pure 0#4)
        (Signal.mux en0 (ss_ones + 1#4) ss_ones)
      
      -- Update seconds tens digit
      let ss_tens_next := Signal.mux (en1 &&& (ss_tens === 5#4))
        (Signal.pure 0#4)
        (Signal.mux en1 (ss_tens + 1#4) ss_tens)
      
      -- Update minutes ones digit
      let mm_ones_next := Signal.mux (en2 &&& (mm_ones === 9#4))
        (Signal.pure 0#4)
        (Signal.mux en2 (mm_ones + 1#4) mm_ones)
      
      -- Update minutes tens digit
      let mm_tens_next := Signal.mux (en3 &&& (mm_tens === 5#4))
        (Signal.pure 0#4)
        (Signal.mux en3 (mm_tens + 1#4) mm_tens)
      
      -- Update hours ones digit
      let hh_ones_next := Signal.mux (en4 &&& (hh_ones === 9#4))
        (Signal.pure 0#4)
        (Signal.mux en4 (hh_ones + 1#4) hh_ones)
      
      -- Update hours tens digit and handle 12->01 rollover
      let hh_tens_next := Signal.mux en5 (hh_tens + 1#4) hh_tens
      
      -- Combine hour digits
      let hh_next_temp := Signal.map (fun t => t.1 ++ t.2) (bundle2 hh_tens_next hh_ones_next)
      
      -- Handle 12->01 rollover
      let hh_next := Signal.mux (en4 &&& (hh === 0x12#8))
        (Signal.pure 0x01#8)
        hh_next_temp
      
      -- Update PM flag (toggle when going from 11:59:59 to 12:00:00)
      let pm_next := Signal.mux en6 (~~~pm) pm
      
      -- Combine digits back into bytes
      let ss_next := Signal.map (fun t => t.1 ++ t.2) (bundle2 ss_tens_next ss_ones_next)
      let mm_next := Signal.map (fun t => t.1 ++ t.2) (bundle2 mm_tens_next mm_ones_next)
      
      -- Pack next state: use bit concatenation
      let pm_ext := Signal.map (fun p => p.zeroExtend 25) pm_next
      let hh_ext := Signal.map (fun h => h.zeroExtend 25) hh_next
      let mm_ext := Signal.map (fun m => m.zeroExtend 25) mm_next
      let ss_ext := Signal.map (fun s => s.zeroExtend 25) ss_next
      
      let pm_shifted := pm_ext <<< 24#25
      let hh_shifted := hh_ext <<< 16#25
      let mm_shifted := mm_ext <<< 8#25
      
      let next_packed := pm_shifted ||| hh_shifted ||| mm_shifted ||| ss_ext
      
      -- Apply reset
      let reset_packed := Signal.pure 0x0120000#25  -- pm=0, hh=0x12, mm=0x00, ss=0x00
      let final_next := Signal.mux reset reset_packed next_packed
      
      Signal.register 0x0120000#25 final_next
  
  -- Extract outputs from state
  let pm_out := Signal.map (fun s => s.extractLsb 24 24) state
  let hh_out := Signal.map (fun s => s.extractLsb 23 16) state
  let mm_out := Signal.map (fun s => s.extractLsb 15 8) state
  let ss_out := Signal.map (fun s => s.extractLsb 7 0) state
  
  -- Bundle outputs: (pm, (hh, (mm, ss)))
  let mm_ss := bundle2 mm_out ss_out
  let hh_mm_ss := bundle2 hh_out mm_ss
  bundle2 pm_out hh_mm_ss

#synthesizeVerilog prob141_count_clock
