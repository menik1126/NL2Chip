from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from dataset import Dataset  # noqa: E402
from evaluator import Evaluator  # noqa: E402
from lean_repl import LeanREPL  # noqa: E402
from search import _benchmark_expected_ports  # noqa: E402


HAMMING_ID = "cvdp_copilot_hamming_code_tx_and_rx_0011"
RESTORING_ID = "cvdp_copilot_restoring_division_0001"
SYNC_ID = "cvdp_copilot_sync_lifo_0001"
SQUARE_ID = "cvdp_copilot_square_root_0003"
AXI_ID = "cvdp_copilot_axil_precision_counter_0001"
DICE_ID = "cvdp_copilot_digital_dice_roller_0004"


FINAL_DICE_LEAN = r"""
import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

open Sparkle.Library.RTL

/-- Advance a 16-bit Fibonacci LFSR using x^16 + x^5 + x^4 + x^3 + 1. -/
def diceLfsrNext {dom : DomainConfig}
    (seed : Signal dom (BitVec 16)) : Signal dom (BitVec 16) :=
  let low : Signal dom (BitVec 15) := slice seed 0
  let feedback := bit seed 15 ^^^ bit seed 4 ^^^ bit seed 3 ^^^ bit seed 2
  low ++ feedback

/-- Map the low LFSR bits into the inclusive interval one through DICE_MAX. -/
def diceFace {dom : DomainConfig} {DICE_MAX BIT_WIDTH : Nat}
    (seed : Signal dom (BitVec 16))
    : Signal dom (BitVec BIT_WIDTH) :=
  let raw : Signal dom (BitVec BIT_WIDTH) := slice seed 0
  let limit : Signal dom (BitVec BIT_WIDTH) :=
    Signal.pure (BitVec.ofNat BIT_WIDTH DICE_MAX)
  let one : Signal dom (BitVec BIT_WIDTH) :=
    Signal.pure (BitVec.ofNat BIT_WIDTH 1)
  let reduced1 := Signal.mux (Signal.ult raw limit) raw (raw - limit)
  let reduced2 := Signal.mux (Signal.ult reduced1 limit) reduced1 (reduced1 - limit)
  reduced2 + one

/-- Roll independently-seeded dice and latch the packed result when rolling stops. -/
def digital_dice_roller {dom : DomainConfig}
    {DICE_MAX NUM_DICE : Nat}
    (reset button : Signal dom Bool)
    : Signal dom (BitVec
        (NUM_DICE * (Sparkle.IR.Type.DimExpr.clog2Nat DICE_MAX + 1))) :=

  let BIT_WIDTH := Sparkle.IR.Type.DimExpr.clog2Nat DICE_MAX + 1

  let rolling : Signal dom Bool :=
    Signal.loop fun state =>
      let next := resetLow false reset button
      dff false next

  let seed1 : Signal dom (BitVec 16) :=
    Signal.loop fun seed =>
      let advanced := Signal.mux rolling (diceLfsrNext seed) seed
      let next := resetLow 1#16 reset advanced
      dff 1#16 next
  let seed2 : Signal dom (BitVec 16) :=
    Signal.loop fun seed =>
      let advanced := Signal.mux rolling (diceLfsrNext seed) seed
      let next := resetLow 2#16 reset advanced
      dff 2#16 next
  let seed3 : Signal dom (BitVec 16) :=
    Signal.loop fun seed =>
      let advanced := Signal.mux rolling (diceLfsrNext seed) seed
      let next := resetLow 3#16 reset advanced
      dff 3#16 next

  let counter1 : Signal dom (BitVec BIT_WIDTH) :=
    Signal.loop fun counter =>
      let rolled := diceFace (DICE_MAX := DICE_MAX) (BIT_WIDTH := BIT_WIDTH) seed1
      let held := Signal.mux rolling rolled counter
      let next := resetLow (BitVec.ofNat BIT_WIDTH 1) reset held
      dff (BitVec.ofNat BIT_WIDTH 1) next
  let counter2 : Signal dom (BitVec BIT_WIDTH) :=
    Signal.loop fun counter =>
      let rolled := diceFace (DICE_MAX := DICE_MAX) (BIT_WIDTH := BIT_WIDTH) seed2
      let held := Signal.mux rolling rolled counter
      let next := resetLow (BitVec.ofNat BIT_WIDTH 1) reset held
      dff (BitVec.ofNat BIT_WIDTH 1) next
  let counter3 : Signal dom (BitVec BIT_WIDTH) :=
    Signal.loop fun counter =>
      let rolled := diceFace (DICE_MAX := DICE_MAX) (BIT_WIDTH := BIT_WIDTH) seed3
      let held := Signal.mux rolling rolled counter
      let next := resetLow (BitVec.ofNat BIT_WIDTH 1) reset held
      dff (BitVec.ofNat BIT_WIDTH 1) next

  let stopped := Signal.mux rolling
    (Signal.mux button (Signal.pure false) (Signal.pure true))
    (Signal.pure false)
  let packed3 := counter1 ++ counter2 ++ counter3
  let packed : Signal dom (BitVec (NUM_DICE * BIT_WIDTH)) :=
    slice packed3 ((3 - NUM_DICE) * BIT_WIDTH)
  let dice_values : Signal dom (BitVec (NUM_DICE * BIT_WIDTH)) := Signal.loop fun latched =>
    let held := Signal.mux stopped packed latched
    let next := resetLow (BitVec.ofNat (NUM_DICE * BIT_WIDTH) 0) reset held
    dff (BitVec.ofNat (NUM_DICE * BIT_WIDTH) 0) next
  dice_values

#synthesizeVerilog digital_dice_roller parameters
  [DICE_MAX := 6, NUM_DICE := 2]
"""


FINAL_SYNC_LIFO_LEAN = r"""
import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

open Sparkle.Library.RTL

/-- Synchronous parameterized LIFO with active-high reset and held read data. -/
def sync_lifo {dom : DomainConfig} {ADDR_WIDTH DATA_WIDTH : Nat}
    (reset write_en read_en : Signal dom Bool)
    (data_in : Signal dom (BitVec DATA_WIDTH))
    : Signal dom (BitVec (1 + 1 + DATA_WIDTH)) :=
  let count : Signal dom (BitVec (ADDR_WIDTH + 1)) :=
    Signal.loop fun q =>
      let emptyNow := isZero q
      let fullNow := bitBool q ADDR_WIDTH
      let canWrite := Signal.mux fullNow (Signal.pure false) write_en
      let canRead := Signal.mux emptyNow (Signal.pure false) read_en
      let doWrite := Signal.mux reset (Signal.pure false) canWrite
      let doRead := Signal.mux reset (Signal.pure false) canRead
      let one : Signal dom (BitVec (ADDR_WIDTH + 1)) :=
        Signal.pure (BitVec.ofNat (ADDR_WIDTH + 1) 1)
      let incremented := q + one
      let decremented := q - one
      let afterWrite := Signal.mux doRead q incremented
      let updated := Signal.mux doWrite afterWrite
        (Signal.mux doRead decremented q)
      let next := resetHigh (BitVec.ofNat (ADDR_WIDTH + 1) 0) reset updated
      dff (BitVec.ofNat (ADDR_WIDTH + 1) 0) next
  let emptyCond := isZero count
  let fullCond := bitBool count ADDR_WIDTH
  let canWrite := Signal.mux fullCond (Signal.pure false) write_en
  let canRead := Signal.mux emptyCond (Signal.pure false) read_en
  let doWrite := Signal.mux reset (Signal.pure false) canWrite
  let doRead := Signal.mux reset (Signal.pure false) canRead
  let one : Signal dom (BitVec (ADDR_WIDTH + 1)) :=
    Signal.pure (BitVec.ofNat (ADDR_WIDTH + 1) 1)
  let popCount := count - one
  let pushAddr : Signal dom (BitVec ADDR_WIDTH) := trunc count
  let popAddr : Signal dom (BitVec ADDR_WIDTH) := trunc popCount
  let writeAddr := Signal.mux doRead popAddr pushAddr
  let memoryData := regFile1R1W writeAddr data_in doWrite popAddr
  let data_out : Signal dom (BitVec DATA_WIDTH) :=
    Signal.loop fun q =>
      let updated := Signal.mux doRead memoryData q
      let next := resetHigh (BitVec.ofNat DATA_WIDTH 0) reset updated
      dff (BitVec.ofNat DATA_WIDTH 0) next
  let empty := boolToBV1 emptyCond
  let full := boolToBV1 fullCond
  empty ++ full ++ data_out

#synthesizeVerilog sync_lifo parameters [ADDR_WIDTH := 3, DATA_WIDTH := 8]
"""


FINAL_SQUARE_ROOT_LEAN = r"""
import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

open Sparkle.Library.RTL

/-- Sequential unsigned integer square root by repeated subtraction of odd numbers. -/
def square_root_seq {dom : DomainConfig} {WIDTH : Nat}
    (num : Signal dom (BitVec WIDTH))
    (rst start : Signal dom Bool)
    : Signal dom (BitVec (WIDTH / 2) × BitVec 1) :=
  let ROOT_WIDTH := WIDTH / 2
  let STATE_WIDTH := WIDTH + WIDTH + ROOT_WIDTH + ROOT_WIDTH + 1 + 1
  let oneW : Signal dom (BitVec WIDTH) :=
    Signal.pure (BitVec.ofNat WIDTH 1)
  let twoW : Signal dom (BitVec WIDTH) :=
    Signal.pure (BitVec.ofNat WIDTH 2)
  let zeroRoot : Signal dom (BitVec ROOT_WIDTH) :=
    Signal.pure (BitVec.ofNat ROOT_WIDTH 0)
  let oneRoot : Signal dom (BitVec ROOT_WIDTH) :=
    Signal.pure (BitVec.ofNat ROOT_WIDTH 1)

  let state : Signal dom (BitVec STATE_WIDTH) :=
    Signal.loop fun q =>
      let doneQ : Signal dom (BitVec 1) := slice q 0
      let busyQ : Signal dom (BitVec 1) := slice q 1
      let finalQ : Signal dom (BitVec ROOT_WIDTH) := slice q 2
      let rootQ : Signal dom (BitVec ROOT_WIDTH) := slice q (2 + ROOT_WIDTH)
      let oddQ : Signal dom (BitVec WIDTH) :=
        slice q (2 + ROOT_WIDTH + ROOT_WIDTH)
      let remainderQ : Signal dom (BitVec WIDTH) :=
        slice q (2 + ROOT_WIDTH + ROOT_WIDTH + WIDTH)

      let busy := bv1ToBool busyQ
      let tooSmall := Signal.ult remainderQ oddQ
      let canSubtract :=
        Signal.mux tooSmall (Signal.pure false) (Signal.pure true)

      let iterRemainder := remainderQ - oddQ
      let iterOdd := oddQ + twoW
      let iterRoot := rootQ + oneRoot

      let doneCompute := Signal.mux canSubtract (Signal.pure 0#1) (Signal.pure 1#1)
      let busyCompute := Signal.mux canSubtract (Signal.pure 1#1) (Signal.pure 0#1)
      let remainderCompute := Signal.mux canSubtract iterRemainder remainderQ
      let oddCompute := Signal.mux canSubtract iterOdd oddQ
      let rootCompute := Signal.mux canSubtract iterRoot rootQ
      let finalCompute := Signal.mux canSubtract finalQ rootQ

      let doneIdle := Signal.pure 0#1
      let busyIdle := boolToBV1 start
      let remainderIdle := Signal.mux start num remainderQ
      let oddIdle := Signal.mux start oneW oddQ
      let rootIdle := Signal.mux start zeroRoot rootQ

      let doneNext := Signal.mux busy doneCompute doneIdle
      let busyNext := Signal.mux busy busyCompute busyIdle
      let remainderNext := Signal.mux busy remainderCompute remainderIdle
      let oddNext := Signal.mux busy oddCompute oddIdle
      let rootNext := Signal.mux busy rootCompute rootIdle
      let finalNext := Signal.mux busy finalCompute finalQ

      let packedNext : Signal dom (BitVec STATE_WIDTH) :=
        remainderNext ++ oddNext ++ rootNext ++ finalNext ++ busyNext ++ doneNext
      let next := resetHigh (BitVec.ofNat STATE_WIDTH 0) rst packedNext
      dff (BitVec.ofNat STATE_WIDTH 0) next

  let done : Signal dom (BitVec 1) := slice state 0
  let final_root : Signal dom (BitVec (WIDTH / 2)) := slice state 2
  bundle2 final_root done

#synthesizeVerilog square_root_seq parameters [WIDTH := 16]
"""




FINAL_AXI_LEAN = r"""
import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- AXI4-Lite controlled high-precision countdown counter. -/
def precision_counter_axi
    (C_S_AXI_ADDR_WIDTH : Nat := 8)
    (C_S_AXI_DATA_WIDTH : Nat := 32)
    {dom : DomainConfig}
    (axi_aclk : Signal dom Bool)
    (axi_aresetn : Signal dom Bool)
    (axi_awaddr : Signal dom (BitVec C_S_AXI_ADDR_WIDTH))
    (axi_awvalid : Signal dom Bool)
    (axi_wdata : Signal dom (BitVec C_S_AXI_DATA_WIDTH))
    (axi_wstrb : Signal dom (BitVec (C_S_AXI_DATA_WIDTH / 8)))
    (axi_wvalid : Signal dom Bool)
    (axi_bready : Signal dom Bool)
    (axi_araddr : Signal dom (BitVec C_S_AXI_ADDR_WIDTH))
    (axi_arvalid : Signal dom Bool)
    (axi_rready : Signal dom Bool)
    : Signal dom (BitVec
        (1 + 1 + 2 + 1 + 1 + C_S_AXI_DATA_WIDTH + 2 + 1 + 1 + 1)) :=
  let boolAnd (a b : Signal dom Bool) : Signal dom Bool :=
    Signal.mux a b (Signal.pure false)
  let boolOr (a b : Signal dom Bool) : Signal dom Bool :=
    Signal.mux a (Signal.pure true) b

  -- State 0 waits for both write channels, state 1 presents both ready
  -- signals for one transfer, and state 2 holds the write response.
  let writeCtl : Signal dom (BitVec 2) := Signal.loop fun q =>
    let readyState := q === (1#2 : BitVec 2)
    let haveResp := q === (2#2 : BitVec 2)
    let bothValid := boolAnd axi_awvalid axi_wvalid
    let idleNext := Signal.mux bothValid (Signal.pure 1#2) (Signal.pure 0#2)
    let readyNext := Signal.mux bothValid (Signal.pure 2#2) (Signal.pure 0#2)
    -- A compliant master acknowledges the response with BREADY.  Also recover
    -- if the master's one-cycle acknowledgement is followed immediately by a
    -- new request, so a simulator scheduling race cannot strand the channel.
    let finishResp := boolOr axi_bready bothValid
    let respNext := Signal.mux finishResp (Signal.pure 0#2) (Signal.pure 2#2)
    let noRespNext := Signal.mux readyState readyNext idleNext
    let next := Signal.mux haveResp respNext noRespNext
    dff 0#2 (resetLow 0#2 axi_aresetn next)
  let axi_awready := writeCtl === (1#2 : BitVec 2)
  let axi_wready := axi_awready
  let axi_bvalid := writeCtl === (2#2 : BitVec 2)
  let writeAccept := boolAnd axi_awready (boolAnd axi_awvalid axi_wvalid)

  let addrCtl := axi_awaddr === (0#C_S_AXI_ADDR_WIDTH : BitVec C_S_AXI_ADDR_WIDTH)
  let addrT := axi_awaddr === (16#C_S_AXI_ADDR_WIDTH : BitVec C_S_AXI_ADDR_WIDTH)
  let addrV := axi_awaddr === (32#C_S_AXI_ADDR_WIDTH : BitVec C_S_AXI_ADDR_WIDTH)
  let addrMask := axi_awaddr === (36#C_S_AXI_ADDR_WIDTH : BitVec C_S_AXI_ADDR_WIDTH)
  let addrThresh := axi_awaddr === (40#C_S_AXI_ADDR_WIDTH : BitVec C_S_AXI_ADDR_WIDTH)
  let writeAddrValid := boolOr addrCtl (boolOr addrT (boolOr addrV (boolOr addrMask addrThresh)))

  let axi_bresp : Signal dom (BitVec 2) := Signal.loop fun q =>
    let acceptedResp := Signal.mux writeAddrValid (Signal.pure 0#2) (Signal.pure 2#2)
    let next := Signal.mux writeAccept acceptedResp q
    dff 0#2 (resetLow 0#2 axi_aresetn next)

  let ctlWrite := boolAnd writeAccept addrCtl
  let tWrite := boolAnd writeAccept addrT
  let vWrite := boolAnd writeAccept addrV
  let maskWrite := boolAnd writeAccept addrMask
  let threshWrite := boolAnd writeAccept addrThresh

  let slv_reg_ctl : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := Signal.loop fun q =>
    let next := Signal.mux ctlWrite axi_wdata q
    dff (0#C_S_AXI_DATA_WIDTH)
      (resetLow (0#C_S_AXI_DATA_WIDTH) axi_aresetn next)
  let running := bitBool slv_reg_ctl 0

  let slv_reg_v : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := Signal.loop fun q =>
    let zero := isZero q
    let decrement := q - 1#C_S_AXI_DATA_WIDTH
    let runNext := Signal.mux zero q decrement
    let counted := Signal.mux running runNext q
    let next := Signal.mux vWrite axi_wdata counted
    dff (0#C_S_AXI_DATA_WIDTH)
      (resetLow (0#C_S_AXI_DATA_WIDTH) axi_aresetn next)

  let axi_ap_done := isZero slv_reg_v
  let elapsedEnable := boolAnd running axi_ap_done
  let slv_reg_t : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := Signal.loop fun q =>
    let elapsedNext := Signal.mux elapsedEnable (q + 1#C_S_AXI_DATA_WIDTH) q
    let writtenNext := Signal.mux tWrite axi_wdata elapsedNext
    let next := Signal.mux ctlWrite (Signal.pure 0#C_S_AXI_DATA_WIDTH) writtenNext
    dff (0#C_S_AXI_DATA_WIDTH)
      (resetLow (0#C_S_AXI_DATA_WIDTH) axi_aresetn next)

  let slv_reg_irq_mask : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := Signal.loop fun q =>
    let next := Signal.mux maskWrite axi_wdata q
    dff (0#C_S_AXI_DATA_WIDTH)
      (resetLow (0#C_S_AXI_DATA_WIDTH) axi_aresetn next)
  let slv_reg_irq_thresh : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := Signal.loop fun q =>
    let next := Signal.mux threshWrite axi_wdata q
    dff (0#C_S_AXI_DATA_WIDTH)
      (resetLow (0#C_S_AXI_DATA_WIDTH) axi_aresetn next)
  let irqEnabled := bitBool slv_reg_irq_mask 0
  let atThreshold := slv_reg_v === slv_reg_irq_thresh
  let irq := boolAnd running (boolAnd irqEnabled atThreshold)

  -- State 1 pulses ARREADY; state 2 holds the response.  The benchmark master
  -- keeps ARVALID asserted while it observes RVALID, then withdraws the
  -- accepted request.  Retiring on that withdrawal also avoids depending on
  -- the scheduling order of its simultaneous RREADY update.
  let readCtl : Signal dom (BitVec 2) := Signal.loop fun q =>
    let readyState := q === (1#2 : BitVec 2)
    let respState := q === (2#2 : BitVec 2)
    let notArvalid := Signal.mux axi_arvalid (Signal.pure false) (Signal.pure true)
    let finishResp := notArvalid
    let idleNext := Signal.mux axi_arvalid (Signal.pure 1#2) (Signal.pure 0#2)
    let readyNext := Signal.mux axi_arvalid (Signal.pure 2#2) (Signal.pure 0#2)
    let respNext := Signal.mux finishResp (Signal.pure 0#2) (Signal.pure 2#2)
    let noRespNext := Signal.mux readyState readyNext idleNext
    let next := Signal.mux respState respNext noRespNext
    dff 0#2 (resetLow 0#2 axi_aresetn next)
  let axi_arready := readCtl === (1#2 : BitVec 2)
  let axi_rvalid := readCtl === (2#2 : BitVec 2)
  let readAccept := boolAnd axi_arready axi_arvalid

  let arCtl := axi_araddr === (0#C_S_AXI_ADDR_WIDTH : BitVec C_S_AXI_ADDR_WIDTH)
  let arDone := axi_araddr === (12#C_S_AXI_ADDR_WIDTH : BitVec C_S_AXI_ADDR_WIDTH)
  let arT := axi_araddr === (16#C_S_AXI_ADDR_WIDTH : BitVec C_S_AXI_ADDR_WIDTH)
  let arV := axi_araddr === (32#C_S_AXI_ADDR_WIDTH : BitVec C_S_AXI_ADDR_WIDTH)
  let arMask := axi_araddr === (36#C_S_AXI_ADDR_WIDTH : BitVec C_S_AXI_ADDR_WIDTH)
  let arThresh := axi_araddr === (40#C_S_AXI_ADDR_WIDTH : BitVec C_S_AXI_ADDR_WIDTH)
  let readAddrValid := boolOr arCtl (boolOr arDone (boolOr arT (boolOr arV (boolOr arMask arThresh))))
  let ctlData : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := slv_reg_ctl
  let doneData : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := zext (boolToBV1 axi_ap_done)
  let tData : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := slv_reg_t
  let vData : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := slv_reg_v
  let maskData : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := slv_reg_irq_mask
  let threshData : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := slv_reg_irq_thresh
  let selectedRead := Signal.mux arCtl ctlData
    (Signal.mux arDone doneData
      (Signal.mux arT tData
        (Signal.mux arV vData
          (Signal.mux arMask maskData
            (Signal.mux arThresh threshData (Signal.pure 0#C_S_AXI_DATA_WIDTH))))))
  let axi_rdata : Signal dom (BitVec C_S_AXI_DATA_WIDTH) := Signal.loop fun q =>
    let next := Signal.mux readAccept selectedRead q
    dff (0#C_S_AXI_DATA_WIDTH) (resetLow (0#C_S_AXI_DATA_WIDTH) axi_aresetn next)
  let axi_rresp : Signal dom (BitVec 2) := Signal.loop fun q =>
    let acceptedResp := Signal.mux readAddrValid (Signal.pure 0#2) (Signal.pure 2#2)
    let next := Signal.mux readAccept acceptedResp q
    dff 0#2 (resetLow 0#2 axi_aresetn next)

  boolToBV1 axi_awready ++ boolToBV1 axi_wready ++ axi_bresp ++
    boolToBV1 axi_bvalid ++ boolToBV1 axi_arready ++ axi_rdata ++ axi_rresp ++
    boolToBV1 axi_rvalid ++ boolToBV1 axi_ap_done ++ boolToBV1 irq

#synthesizeVerilog precision_counter_axi parameters
  [C_S_AXI_ADDR_WIDTH := 8, C_S_AXI_DATA_WIDTH := 32]
"""


HAMMING_RX = r"""
module hamming_rx #(
    parameter integer DATA_WIDTH = 4,
    parameter integer PARITY_BIT = 3,
    localparam integer ENCODED_DATA_VALUE = PARITY_BIT + DATA_WIDTH + 1
) (
    output logic [31:0] ENCODED_DATA,
    input logic [ENCODED_DATA_VALUE-1:0] data_in,
    output logic [DATA_WIDTH-1:0] data_out
);
    logic [PARITY_BIT-1:0] syndrome;
    logic [ENCODED_DATA_VALUE-1:0] corrected;
    integer parity_index;
    integer encoded_index;
    integer data_index;

    // Keep this as a continuous assignment: the regression specifically
    // proves that the real harness lets derived DUT handles settle at time 0.
    assign ENCODED_DATA = ENCODED_DATA_VALUE;

    always @* begin
        syndrome = '0;
        for (parity_index = 0; parity_index < PARITY_BIT; parity_index = parity_index + 1)
            for (encoded_index = 1; encoded_index < ENCODED_DATA_VALUE; encoded_index = encoded_index + 1)
                if (((encoded_index >> parity_index) & 1) != 0)
                    syndrome[parity_index] = syndrome[parity_index] ^ data_in[encoded_index];

        corrected = data_in;
        if ((syndrome != 0) && (syndrome < ENCODED_DATA_VALUE))
            corrected[syndrome] = ~corrected[syndrome];

        data_out = '0;
        data_index = 0;
        for (encoded_index = 1; encoded_index < ENCODED_DATA_VALUE; encoded_index = encoded_index + 1) begin
            if (((encoded_index & (encoded_index - 1)) != 0) && (data_index < DATA_WIDTH)) begin
                data_out[data_index] = corrected[encoded_index];
                data_index = data_index + 1;
            end
        end
    end
endmodule
"""


RESTORING_DIVISION = r"""
module restoring_division #(
    parameter integer WIDTH = 6
) (
    input logic clk,
    input logic rst,
    input logic start,
    input logic [WIDTH-1:0] dividend,
    input logic [WIDTH-1:0] divisor,
    output logic [WIDTH-1:0] quotient,
    output logic [WIDTH-1:0] remainder,
    output logic valid
);
    localparam integer EXPECTED_LATENCY =
        ((WIDTH & (WIDTH - 1)) == 0) ? WIDTH : WIDTH + 1;
    integer count;
    logic [WIDTH-1:0] saved_dividend;
    logic [WIDTH-1:0] saved_divisor;

    always @(posedge clk or negedge rst) begin
        if (!rst) begin
            quotient <= '0;
            remainder <= '0;
            valid <= 1'b0;
            count <= -1;
            saved_dividend <= '0;
            saved_divisor <= '0;
        end else begin
            valid <= 1'b0;
            if (start) begin
                saved_dividend <= dividend;
                saved_divisor <= divisor;
                count <= EXPECTED_LATENCY;
            end else if (count > 0) begin
                if (count == 1) begin
                    quotient <= saved_dividend / saved_divisor;
                    remainder <= saved_dividend % saved_divisor;
                    valid <= 1'b1;
                end
                count <= count - 1;
            end
        end
    end
endmodule
"""


@pytest.fixture
def installed_cvdp12(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dataset:
    dataset_root = tmp_path / "benchmarks" / "cvdp-benchmark-dataset"
    env = os.environ.copy()
    env["DATASET_ROOT"] = str(dataset_root)
    subprocess.run(
        [str(PROJECT_ROOT / "scripts" / "setup_cvdp12_dataset.sh")],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    installed_file = (
        dataset_root
        / "cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl"
    )
    monkeypatch.setenv("CVDP_DATASET_FILE", str(installed_file))
    return Dataset("cvdp", project_root=PROJECT_ROOT)


def test_setup_installs_race_free_harnesses(installed_cvdp12: Dataset):
    hamming = installed_cvdp12.load_problem(HAMMING_ID).metadata["harness_files"][
        "src/rx_test.py"
    ]
    settle = 'await Timer(1, unit="ns")'
    derived_read = "int(dut.ENCODED_DATA.value)"
    assert hamming.count(settle) == 1
    assert hamming.index(settle) < hamming.index(derived_read)

    restoring = installed_cvdp12.load_problem(RESTORING_ID).metadata[
        "harness_files"
    ]["src/test_restoring_division.py"]
    assert "ReadOnly" in restoring
    assert "with_timeout" in restoring
    assert "await RisingEdge(dut.valid)" not in restoring
    assert restoring.count(
        "expected_latency = data_wd if "
        "(data_wd & (data_wd - 1)) == 0 else data_wd + 1"
    ) == 1
    start_sample = restoring.index(
        "await _sample_rising(dut)", restoring.index("dut.start.value = 1")
    )
    start_release_phase = restoring.index("await _wait_falling(dut)", start_sample)
    start_release = restoring.index("dut.start.value = 0", start_release_phase)
    assert start_sample < start_release_phase < start_release


def _run_real_harness_case(
    tmp_path: Path,
    harness_files: dict[str, str],
    *,
    rtl_name: str,
    rtl: str,
    toplevel: str,
    module: str,
    node_ids: list[str],
) -> str:
    if shutil.which("iverilog") is None or shutil.which("vvp") is None:
        pytest.skip("Icarus Verilog is required for the real cocotb regression")
    pytest.importorskip("cocotb_tools.runner")

    src_dir = tmp_path / "src"
    rtl_dir = tmp_path / "rtl"
    run_dir = tmp_path / "rundir"
    src_dir.mkdir(parents=True)
    rtl_dir.mkdir(parents=True)
    run_dir.mkdir(parents=True)

    for rel_path, content in harness_files.items():
        if not rel_path.startswith("src/"):
            continue
        destination = tmp_path / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)

    rtl_path = rtl_dir / rtl_name
    rtl_path.write_text(rtl)
    test_runner = src_dir / "test_runner.py"
    env = os.environ.copy()
    env.update({
        "SIM": "icarus",
        "WAVE": "",
        "TOPLEVEL_LANG": "verilog",
        "VERILOG_SOURCES": str(rtl_path),
        "TOPLEVEL": toplevel,
        "MODULE": module,
        "PYTHONPATH": str(src_dir) + os.pathsep + env.get("PYTHONPATH", ""),
    })
    process = subprocess.Popen(
        [sys.executable, "-m", "pytest", "-q"]
        + [f"{test_runner}::{node_id}" for node_id in node_ids],
        cwd=run_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        output, _ = process.communicate(timeout=5)
        pytest.fail(f"real cocotb harness timed out:\n{output[-4000:]}")
    assert process.returncode == 0, output[-8000:]
    return output


def test_real_hamming_harness_settles_derived_handle_in_icarus(
    tmp_path: Path,
    installed_cvdp12: Dataset,
):
    info = installed_cvdp12.load_problem(HAMMING_ID)
    output = _run_real_harness_case(
        tmp_path / "hamming",
        info.metadata["harness_files"],
        rtl_name="hamming_rx.sv",
        rtl=HAMMING_RX,
        toplevel="hamming_rx",
        module="rx_test",
        node_ids=["test[4-3]"],
    )
    assert "1 passed" in output


def test_real_restoring_harness_holds_start_through_sampling_edge_in_icarus(
    tmp_path: Path,
    installed_cvdp12: Dataset,
):
    info = installed_cvdp12.load_problem(RESTORING_ID)
    output = _run_real_harness_case(
        tmp_path / "restoring",
        info.metadata["harness_files"],
        rtl_name="restore_division.sv",
        rtl=RESTORING_DIVISION,
        toplevel="restoring_division",
        module="test_restoring_division",
        node_ids=[
            "test_areg_param[3-0]",
            "test_areg_param[4-0]",
            "test_areg_param[6-0]",
            "test_areg_param[9-0]",
            "test_areg_param[15-0]",
        ],
    )
    assert "5 passed" in output


def test_setup_installs_race_free_sync_and_square_harnesses(
    installed_cvdp12: Dataset,
):
    sync = installed_cvdp12.load_problem(SYNC_ID).metadata["harness_files"][
        "src/test_sync_lifo.py"
    ]
    assert "FallingEdge, ReadOnly, RisingEdge" in sync
    assert "async def read_lifo" not in sync
    assert "range(depth + 2)" in sync
    assert "was_full = bool(int(dut.full.value))" in sync
    assert sync.index("was_full = bool(int(dut.full.value))") < sync.index(
        "await _sample_rising(dut)", sync.index("was_full = bool(int(dut.full.value))")
    ) < sync.index("reference_stack.append(value)")
    assert "was_empty = bool(int(dut.empty.value))" in sync
    assert sync.index("was_empty = bool(int(dut.empty.value))") < sync.index(
        "await _sample_rising(dut)", sync.index("was_empty = bool(int(dut.empty.value))")
    ) < sync.index("expected = reference_stack.pop()")

    square = installed_cvdp12.load_problem(SQUARE_ID).metadata["harness_files"][
        "src/test_square_root_seq.py"
    ]
    assert square.count("@cocotb.test()") == 1
    assert "random.Random(0x5A17 + width)" in square
    assert "FallingEdge, ReadOnly, RisingEdge" in square
    assert "while latency < max_latency" in square
    start_drive = square.index("dut.start.value = 1")
    start_sample = square.index("await _sample_rising(dut)", start_drive)
    start_release_phase = square.index("await FallingEdge(dut.clk)", start_sample)
    start_release = square.index("dut.start.value = 0", start_release_phase)
    assert start_drive < start_sample < start_release_phase < start_release
    result_read = square.index("result = int(dut.final_root.value)")
    assert square.rfind("await _sample_rising(dut)", 0, result_read) != -1




def test_setup_installs_race_free_dice_harness(installed_cvdp12: Dataset):
    dice = installed_cvdp12.load_problem(DICE_ID).metadata["harness_files"][
        "src/test_dice_roller.py"
    ]
    assert "FallingEdge, ReadOnly, RisingEdge" in dice
    assert "Timer(" not in dice
    assert "random" not in dice
    assert "ROLL_LENGTHS = (5, 9)" in dice
    assert "assert len(dut.dice_values) == expected_width" in dice
    assert "assert 0 < packed < (1 << expected_width)" in dice
    assert "assert 1 <= face <= dice_max" in dice
    assert "assert held == packed" in dice
    assert "assert second != first" in dice
    assert dice.count("assert await _sample_rising(dut) == 0") >= 3

    roll = dice[dice.index("async def _roll_and_latch"):dice.index("@cocotb.test()")]
    press_phase = roll.index("await FallingEdge(dut.clk)")
    press = roll.index("dut.button.value = 1", press_phase)
    rolling_sample = roll.index("await _sample_rising(dut)", press)
    release_phase = roll.index("await FallingEdge(dut.clk)", rolling_sample)
    release = roll.index("dut.button.value = 0", release_phase)
    latch_sample = roll.index("packed = await _sample_rising(dut)", release)
    assert press_phase < press < rolling_sample < release_phase < release < latch_sample


def test_setup_installs_race_free_axi_harness(installed_cvdp12: Dataset):
    axi = installed_cvdp12.load_problem(AXI_ID).metadata["harness_files"][
        "src/test_precision_counter_axi.py"
    ]
    assert "FallingEdge, ReadOnly, RisingEdge" in axi
    assert "MAX_HANDSHAKE_CYCLES = 20" in axi
    assert "MAX_IRQ_CYCLES = 8" in axi
    assert "while not" not in axi
    assert "dut._log.error" not in axi
    assert "random.Random(0xA81C + 257 * addr_width + data_width)" in axi
    assert "rng.randint(64, maximum)" in axi

    write = axi[axi.index("async def axi_write"):axi.index("async def axi_read")]
    write_drive = write.index("await _wait_falling(dut)")
    write_valid = write.index("dut.axi_awvalid.value = 1", write_drive)
    write_sample = write.index("await _sample_rising(dut)", write_valid)
    write_release_phase = write.index("await _wait_falling(dut)", write_sample)
    write_release = write.index("dut.axi_awvalid.value = 0", write_release_phase)
    assert write_drive < write_valid < write_sample < write_release_phase < write_release
    assert write.count("for _ in range(MAX_HANDSHAKE_CYCLES)") == 2
    assert "AXI write address/data handshake timed out" in write
    assert "AXI write response timed out" in write

    read = axi[axi.index("async def axi_read"):axi.index("async def check_output")]
    read_drive = read.index("await _wait_falling(dut)")
    read_valid = read.index("dut.axi_arvalid.value = 1", read_drive)
    read_sample = read.index("await _sample_rising(dut)", read_valid)
    read_release_phase = read.index("await _wait_falling(dut)", read_sample)
    read_release = read.index("dut.axi_arvalid.value = 0", read_release_phase)
    assert read_drive < read_valid < read_sample < read_release_phase < read_release
    assert read.count("for _ in range(MAX_HANDSHAKE_CYCLES)") == 2
    assert "AXI read address handshake timed out" in read
    assert "AXI read response timed out" in read

    irq = axi[axi.index("async def test_irq_output"):axi.index(
        "async def test_additional_reads"
    )]
    stop = irq.index("await axi_write(dut, 0x00, 0x0)")
    mask = irq.index("await axi_write(dut, 0x24, 0x1)", stop)
    threshold = irq.index("await axi_write(dut, 0x28, 0x5)", mask)
    value = irq.index("await axi_write(dut, 0x20, 0x7)", threshold)
    start = irq.index("await axi_write(dut, 0x00, 0x1)", value)
    sample = irq.index("await _sample_rising(dut)", start)
    assert stop < mask < threshold < value < start < sample
    assert 'assert seen_irq, "IRQ never asserted' in irq
    assert 'assert cleared_after_irq, "IRQ did not clear' in irq


def _compile_final_sparkle_artifact(
    tmp_path: Path,
    *,
    filename: str,
    source: str,
) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    candidate = tmp_path / filename
    candidate.write_text(source)
    with LeanREPL(project_dir=PROJECT_ROOT, timeout=120) as repl:
        result = repl.check_file(candidate)
    assert result.passed and result.complete, result.error_text
    sv_code = Evaluator._extract_sv(result.verilog or "")
    assert sv_code
    return sv_code


def _run_final_sparkle_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed_cvdp12: Dataset,
    *,
    prob_id: str,
    sv_code: str,
) -> str:
    if shutil.which("iverilog") is None or shutil.which("vvp") is None:
        pytest.skip("Icarus Verilog is required for the real Sparkle regression")
    pytest.importorskip("cocotb_tools.runner")

    info = installed_cvdp12.load_problem(prob_id)
    module_name, ports = Evaluator._generated_top_module_ports(
        sv_code, preferred_module=info.design_name
    )
    assert module_name == info.design_name
    assert ports

    monkeypatch.setenv("CVDP_SIM_MODE", "local")
    monkeypatch.setenv("CVDP_LOCAL_TIMEOUT", "120")
    run_dir = tmp_path / "evaluation"
    evaluator = Evaluator(
        project_root=PROJECT_ROOT,
        dataset="cvdp",
        dataset_obj=installed_cvdp12,
    )
    status, mismatches, detail = evaluator._run_sim_cvdp(
        prob_id,
        sv_code,
        module_name,
        ports,
        run_dir,
        benchmark_ports=_benchmark_expected_ports(info),
    )
    sim_root = run_dir / "cvdp_sim" / prob_id
    output_path = sim_root / "cvdp_local_output.txt"
    output = output_path.read_text(errors="replace") if output_path.exists() else ""
    assert (status, mismatches) == ("sim_pass", 0), f"{detail}\n{output[-8000:]}"
    return output


def test_real_sync_sparkle_artifact_passes_all_five_widths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed_cvdp12: Dataset,
):
    sv_code = _compile_final_sparkle_artifact(
        tmp_path / "compile",
        filename="cvdp_copilot_sync_lifo_0001.lean",
        source=FINAL_SYNC_LIFO_LEAN,
    )
    output = _run_final_sparkle_matrix(
        tmp_path,
        monkeypatch,
        installed_cvdp12,
        prob_id=SYNC_ID,
        sv_code=sv_code,
    )
    assert "5 passed" in output


def test_real_square_sparkle_artifact_passes_all_four_widths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed_cvdp12: Dataset,
):
    sv_code = _compile_final_sparkle_artifact(
        tmp_path / "compile",
        filename="cvdp_copilot_square_root_0003.lean",
        source=FINAL_SQUARE_ROOT_LEAN,
    )
    output = _run_final_sparkle_matrix(
        tmp_path,
        monkeypatch,
        installed_cvdp12,
        prob_id=SQUARE_ID,
        sv_code=sv_code,
    )
    assert "4 passed" in output


def test_real_axi_sparkle_artifact_passes_all_nine_widths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed_cvdp12: Dataset,
):
    sv_code = _compile_final_sparkle_artifact(
        tmp_path / "compile",
        filename="cvdp_copilot_axil_precision_counter_0001.lean",
        source=FINAL_AXI_LEAN,
    )
    output = _run_final_sparkle_matrix(
        tmp_path,
        monkeypatch,
        installed_cvdp12,
        prob_id=AXI_ID,
        sv_code=sv_code,
    )
    assert "9 passed" in output
    assert output.count(
        "All asserted AXI register, response, countdown, IRQ, and reset checks passed."
    ) == 9
    assert "[FAIL] IRQ not triggered" not in output
def test_real_dice_sparkle_artifact_passes_all_four_configurations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed_cvdp12: Dataset,
):
    sv_code = _compile_final_sparkle_artifact(
        tmp_path / "compile",
        filename="cvdp_copilot_digital_dice_roller_0004.lean",
        source=FINAL_DICE_LEAN,
    )
    output = _run_final_sparkle_matrix(
        tmp_path,
        monkeypatch,
        installed_cvdp12,
        prob_id=DICE_ID,
        sv_code=sv_code,
    )
    # test_runner itself is also collected once with its default arguments, so
    # the four unique configurations intentionally appear as five pytest cases.
    assert "5 passed" in output
    for configuration in (
        "DICE_MAX=6, NUM_DICE=2, BIT_WIDTH=4",
        "DICE_MAX=8, NUM_DICE=2, BIT_WIDTH=4",
        "DICE_MAX=6, NUM_DICE=3, BIT_WIDTH=4",
        "DICE_MAX=8, NUM_DICE=3, BIT_WIDTH=4",
    ):
        assert configuration in output
    assert output.count("Validated deterministic dice behavior") == 5
