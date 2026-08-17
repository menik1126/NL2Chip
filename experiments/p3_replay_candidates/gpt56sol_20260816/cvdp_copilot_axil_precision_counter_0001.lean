import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- AXI4-Lite controlled countdown counter with elapsed-time and threshold IRQ. -/
def precision_counter_axi {dom : DomainConfig} {C_S_AXI_ADDR_WIDTH : Nat} {C_S_AXI_DATA_WIDTH : Nat}
    (axi_aresetn : Signal dom Bool)
    (axi_araddr : Signal dom (BitVec C_S_AXI_ADDR_WIDTH))
    (axi_arvalid : Signal dom Bool)
    (axi_awaddr : Signal dom (BitVec C_S_AXI_ADDR_WIDTH))
    (axi_awvalid : Signal dom Bool)
    (axi_bready : Signal dom Bool)
    (axi_rready : Signal dom Bool)
    (axi_wdata : Signal dom (BitVec C_S_AXI_DATA_WIDTH))
    (axi_wstrb : Signal dom (BitVec (C_S_AXI_DATA_WIDTH / 8)))
    (axi_wvalid : Signal dom Bool)
    : Signal dom (BitVec (1 + 1 + C_S_AXI_DATA_WIDTH + 1 + 1 + 1)) :=
  let zeroD := BitVec.ofNat C_S_AXI_DATA_WIDTH 0
  let oneD := BitVec.ofNat C_S_AXI_DATA_WIDTH 1
  let addrCtl := BitVec.ofNat C_S_AXI_ADDR_WIDTH 0
  let addrTime := BitVec.ofNat C_S_AXI_ADDR_WIDTH 16
  let addrDone := BitVec.ofNat C_S_AXI_ADDR_WIDTH 12
  let addrValue := BitVec.ofNat C_S_AXI_ADDR_WIDTH 32
  let addrMask := BitVec.ofNat C_S_AXI_ADDR_WIDTH 36
  let addrThresh := BitVec.ofNat C_S_AXI_ADDR_WIDTH 40
  let state :=
    Signal.loop fun (s : Signal dom
        (BitVec C_S_AXI_DATA_WIDTH × BitVec C_S_AXI_DATA_WIDTH ×
         BitVec C_S_AXI_DATA_WIDTH × BitVec C_S_AXI_DATA_WIDTH ×
         BitVec C_S_AXI_DATA_WIDTH × Bool × Bool × BitVec C_S_AXI_DATA_WIDTH)) =>
      let ctl := projN! s 8 0
      let elapsed := projN! s 8 1
      let value := projN! s 8 2
      let irqMask := projN! s 8 3
      let irqThresh := projN! s 8 4
      let writeBusy := projN! s 8 5
      let readValid := projN! s 8 6
      let readData := projN! s 8 7

      let bothWriteValid := Signal.mux axi_awvalid axi_wvalid (Signal.pure false)
      let writeFire := Signal.mux writeBusy (Signal.pure false) bothWriteValid
      let ctlWrite := Signal.mux writeFire (axi_awaddr === addrCtl) (Signal.pure false)
      let timeWrite := Signal.mux writeFire (axi_awaddr === addrTime) (Signal.pure false)
      let valueWrite := Signal.mux writeFire (axi_awaddr === addrValue) (Signal.pure false)
      let maskWrite := Signal.mux writeFire (axi_awaddr === addrMask) (Signal.pure false)
      let threshWrite := Signal.mux writeFire (axi_awaddr === addrThresh) (Signal.pure false)

      let running := bitBool ctl 0
      let valueZero := isZero value
      let decremented := value - oneD
      let countedValue := Signal.mux running (Signal.mux valueZero value decremented) value
      let nextCtl0 := Signal.mux ctlWrite axi_wdata ctl
      let nextValue0 := Signal.mux valueWrite axi_wdata countedValue
      let elapsedInc := elapsed + oneD
      let elapsedCounted := Signal.mux valueZero elapsedInc elapsed
      let nextElapsed0 := Signal.mux ctlWrite (Signal.pure zeroD)
        (Signal.mux timeWrite axi_wdata elapsedCounted)
      let nextMask0 := Signal.mux maskWrite axi_wdata irqMask
      let nextThresh0 := Signal.mux threshWrite axi_wdata irqThresh

      let nextWriteBusy0 := Signal.mux bothWriteValid (Signal.pure true) (Signal.pure false)
      let readSlot := Signal.mux readValid axi_rready (Signal.pure true)
      let readFire := Signal.mux axi_arvalid readSlot (Signal.pure false)
      let doneData : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := zext (boolToBV1 valueZero)
      let selected0 := Signal.mux (axi_araddr === addrCtl) ctl (Signal.pure zeroD)
      let selected1 := Signal.mux (axi_araddr === addrTime) elapsed selected0
      let selected2 := Signal.mux (axi_araddr === addrDone) doneData selected1
      let selected3 := Signal.mux (axi_araddr === addrValue) value selected2
      let selected4 := Signal.mux (axi_araddr === addrMask) irqMask selected3
      let selected5 := Signal.mux (axi_araddr === addrThresh) irqThresh selected4
      let nextReadData0 := Signal.mux readFire selected5 readData
      let nextReadValid0 := Signal.mux readFire (Signal.pure true)
        (Signal.mux axi_rready (Signal.pure false) readValid)

      let nextCtl := resetLow zeroD axi_aresetn nextCtl0
      let nextElapsed := resetLow zeroD axi_aresetn nextElapsed0
      let nextValue := resetLow zeroD axi_aresetn nextValue0
      let nextMask := resetLow zeroD axi_aresetn nextMask0
      let nextThresh := resetLow zeroD axi_aresetn nextThresh0
      let nextWriteBusy := resetLow false axi_aresetn nextWriteBusy0
      let nextReadValid := resetLow false axi_aresetn nextReadValid0
      let nextReadData := resetLow zeroD axi_aresetn nextReadData0
      Signal.register
        (BitVec.ofNat C_S_AXI_DATA_WIDTH 0,
         BitVec.ofNat C_S_AXI_DATA_WIDTH 0,
         BitVec.ofNat C_S_AXI_DATA_WIDTH 0,
         BitVec.ofNat C_S_AXI_DATA_WIDTH 0,
         BitVec.ofNat C_S_AXI_DATA_WIDTH 0,
         false, false, BitVec.ofNat C_S_AXI_DATA_WIDTH 0)
        (bundleAll! [nextCtl, nextElapsed, nextValue, nextMask, nextThresh,
                     nextWriteBusy, nextReadValid, nextReadData])

  let ctl := projN! state 8 0
  let value := projN! state 8 2
  let irqMask := projN! state 8 3
  let irqThresh := projN! state 8 4
  let writeBusy := projN! state 8 5
  let readValid := projN! state 8 6
  let readData := projN! state 8 7
  let bothWriteValid := Signal.mux axi_awvalid axi_wvalid (Signal.pure false)
  let writeReadyB := Signal.mux writeBusy (Signal.pure false) bothWriteValid
  let readSlot := Signal.mux readValid axi_rready (Signal.pure true)
  let arreadyB := Signal.mux axi_aresetn readSlot (Signal.pure false)
  let awreadyB := Signal.mux axi_aresetn writeReadyB (Signal.pure false)
  let running := bitBool ctl 0
  let irqEnabled := bitBool irqMask 0
  let atThreshold := value === irqThresh
  let irq0 := Signal.mux running (Signal.mux irqEnabled atThreshold (Signal.pure false)) (Signal.pure false)
  let irqB := Signal.mux axi_aresetn irq0 (Signal.pure false)
  let axi_arready := boolToBV1 arreadyB
  let axi_awready := boolToBV1 awreadyB
  let axi_rvalid := boolToBV1 readValid
  let axi_wready := boolToBV1 awreadyB
  let irq := boolToBV1 irqB
  axi_arready ++ axi_awready ++ readData ++ axi_rvalid ++ axi_wready ++ irq

#synthesizeParameterizedVerilog precision_counter_axi [C_S_AXI_ADDR_WIDTH := 8, C_S_AXI_DATA_WIDTH := 32]
