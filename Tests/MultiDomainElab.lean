import Sparkle
import Sparkle.Compiler.Elab

namespace Tests.MultiDomainElab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Core.Circuit

def sourceDomain : DomainConfig :=
  { period := 8000
  , activeEdge := .rising
  , resetKind := .asynchronous
  , name := "source"
  , clockName := "src_clk"
  , resetName := "src_rst"
  }

def destinationDomain : DomainConfig :=
  { period := 12000
  , activeEdge := .rising
  , resetKind := .synchronous
  , name := "destination"
  , clockName := "dst_clk"
  , resetName := "dst_rst"
  }

def synchronizedLevel (sourceInput : Signal sourceDomain Bool)
    : Signal destinationDomain Bool :=
  let sourceState := Signal.register false sourceInput
  let synchronized := Signal.synchronizeLevel sourceState
  Signal.register false synchronized

#writeDesign synchronizedLevel
  ".lake/build/gen/tests/multidomain_elab.sv"
  ".lake/build/gen/tests/multidomain_elab_cppsim.h"

def synchronizedPulse (sourcePulse : Signal sourceDomain Bool)
    : Signal destinationDomain Bool :=
  Signal.synchronizePulse sourcePulse

#writeDesign synchronizedPulse
  ".lake/build/gen/tests/multidomain_pulse.sv"
  ".lake/build/gen/tests/multidomain_pulse_cppsim.h"

def synchronizedPulseVector (sourcePulse : Signal sourceDomain (BitVec 4))
    : Signal destinationDomain (BitVec 4) :=
  Signal.synchronizePulseVector sourcePulse

#writeDesign synchronizedPulseVector
  ".lake/build/gen/tests/multidomain_pulse_vector.sv"
  ".lake/build/gen/tests/multidomain_pulse_vector_cppsim.h"

def multiOutputPulse (sourcePulse : Signal sourceDomain Bool) : Circuit :=
  let destinationPulse : Signal destinationDomain Bool :=
    Signal.synchronizePulse sourcePulse
  let sourceReset : Signal sourceDomain Bool := Signal.resetSynchronizer
  let destinationReset : Signal destinationDomain Bool := Signal.resetSynchronizer
  Circuit.ofOutputs
    [ .bool "destination_pulse" destinationDomain destinationPulse
    , .bool "source_reset_sync" sourceDomain sourceReset
    , .bool "destination_reset_sync" destinationDomain destinationReset
    ]

#writeDesign multiOutputPulse
  ".lake/build/gen/tests/multidomain_outputs.sv"
  ".lake/build/gen/tests/multidomain_outputs_cppsim.h"

end Tests.MultiDomainElab
