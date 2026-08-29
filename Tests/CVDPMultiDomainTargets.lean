import Sparkle
import Sparkle.Compiler.Elab

namespace Tests.CVDPMultiDomainTargets

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Core.Circuit

def pulseSourceDomain : DomainConfig :=
  { period := 10000
  , activeEdge := .rising
  , resetKind := .asynchronous
  , name := "source"
  , clockName := "src_clock"
  , resetName := "rst_in"
  }

def pulseDestinationDomain : DomainConfig :=
  { period := 12000
  , activeEdge := .rising
  , resetKind := .asynchronous
  , name := "destination"
  , clockName := "des_clock"
  , resetName := "rst_in"
  }

def cdcPulseSynchronizer0004
    (src_pulse : Signal pulseSourceDomain Bool) : Circuit :=
  let desPulse : Signal pulseDestinationDomain Bool :=
    Signal.synchronizePulse src_pulse
  Circuit.ofOutputs
    [.bool "des_pulse" pulseDestinationDomain desPulse]

def cdcPulseSynchronizer0013 {NUM_CHANNELS : Nat}
    (src_pulse : Signal pulseSourceDomain (BitVec NUM_CHANNELS)) : Circuit :=
  let desPulse : Signal pulseDestinationDomain (BitVec NUM_CHANNELS) :=
    Signal.synchronizePulseVector src_pulse
  let sourceReset : Signal pulseSourceDomain Bool := Signal.resetSynchronizer
  let destinationReset : Signal pulseDestinationDomain Bool := Signal.resetSynchronizer
  Circuit.ofOutputs
    [ .bits "des_pulse" pulseDestinationDomain NUM_CHANNELS desPulse
    , .bool "rst_src_sync" pulseSourceDomain sourceReset
    , .bool "rst_des_sync" pulseDestinationDomain destinationReset
    ]

def fifoWriteDomain : DomainConfig :=
  { period := 10000
  , activeEdge := .rising
  , resetKind := .asynchronous
  , name := "write"
  , clockName := "w_clk"
  , resetName := "w_rst"
  }

def fifoReadDomain : DomainConfig :=
  { period := 13000
  , activeEdge := .rising
  , resetKind := .asynchronous
  , name := "read"
  , clockName := "r_clk"
  , resetName := "r_rst"
  }

def fifoAsync0001 {DATA_WIDTH DEPTH : Nat}
    (w_inc : Signal fifoWriteDomain Bool)
    (w_data : Signal fifoWriteDomain (BitVec DATA_WIDTH))
    (r_inc : Signal fifoReadDomain Bool) : Circuit :=
  Circuit.asyncFifo DEPTH "w_full" "r_data" "r_empty" w_inc w_data r_inc

#writeDesign cdcPulseSynchronizer0004
  ".lake/build/gen/tests/cvdp_cdc_pulse_synchronizer_0004_core.sv"
  ".lake/build/gen/tests/cvdp_cdc_pulse_synchronizer_0004_cppsim.h"

#writeParameterizedVerilogDesign cdcPulseSynchronizer0013
  [NUM_CHANNELS := 4]
  ".lake/build/gen/tests/cvdp_cdc_pulse_synchronizer_0013_core.sv"

#writeParameterizedVerilogDesign fifoAsync0001
  [DATA_WIDTH := 32, DEPTH := 8]
  ".lake/build/gen/tests/cvdp_fifo_async_0001_core.sv"

end Tests.CVDPMultiDomainTargets
