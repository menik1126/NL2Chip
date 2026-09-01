import Sparkle
import Sparkle.Compiler.Elab

namespace Tests.AsyncFifoElab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Core.Circuit

def writeDomain : DomainConfig :=
  { period := 8000
  , activeEdge := .rising
  , resetKind := .asynchronous
  , name := "write"
  , clockName := "write_clock"
  , resetName := "write_reset"
  }

def readDomain : DomainConfig :=
  { period := 12000
  , activeEdge := .rising
  , resetKind := .asynchronous
  , name := "read"
  , clockName := "read_clock"
  , resetName := "read_reset"
  }

def parameterizedFifoTop {DEPTH DATA_WIDTH : Nat}
    (writeIncrement : Signal writeDomain Bool)
    (writeData : Signal writeDomain (BitVec DATA_WIDTH))
    (readIncrement : Signal readDomain Bool) : Circuit :=
  Circuit.asyncFifo DEPTH "write_full" "read_data" "read_empty"
    writeIncrement writeData readIncrement

def fifoTop (writeIncrement : Signal writeDomain Bool)
    (writeData : Signal writeDomain (BitVec 8))
    (readIncrement : Signal readDomain Bool) : Circuit :=
  Circuit.asyncFifo 8 "write_full" "read_data" "read_empty"
    writeIncrement writeData readIncrement

def fifoTop4x17 (writeIncrement : Signal writeDomain Bool)
    (writeData : Signal writeDomain (BitVec 17))
    (readIncrement : Signal readDomain Bool) : Circuit :=
  Circuit.asyncFifo 4 "write_full" "read_data" "read_empty"
    writeIncrement writeData readIncrement

#writeDesign fifoTop
  ".lake/build/gen/tests/async_fifo.sv"
  ".lake/build/gen/tests/async_fifo_cppsim.h"

#writeDesign fifoTop4x17
  ".lake/build/gen/tests/async_fifo_4x17.sv"
  ".lake/build/gen/tests/async_fifo_4x17_cppsim.h"

end Tests.AsyncFifoElab
