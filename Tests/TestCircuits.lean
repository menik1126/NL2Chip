import Sparkle

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-!
# Test Circuit Definitions

Hardware circuit definitions used for Verilog generation testing.
-/

-- Combinational circuits (no registers, no state)
def test_add (a b : Signal Domain (BitVec 16)) : Signal Domain (BitVec 16) :=
  a + b

def test_sub (a b : Signal Domain (BitVec 16)) : Signal Domain (BitVec 16) :=
  a - b

def test_and (a b : Signal Domain (BitVec 16)) : Signal Domain (BitVec 16) :=
  a &&& b

def test_mux (sel : Signal Domain Bool) (a b : Signal Domain (BitVec 16))
    : Signal Domain (BitVec 16) :=
  Signal.mux sel a b

def test_hierarchical_alu (op : Signal Domain Bool) (a b : Signal Domain (BitVec 16))
    : Signal Domain (BitVec 16) :=
  let addResult := test_add a b
  let subResult := test_sub a b
  Signal.mux op subResult addResult

-- Generic helpers can either be specialized in Lean or retained as native
-- SystemVerilog parameters by the synthesis entry point.
def testGenericIdentity {dom : DomainConfig} {width : Nat}
    (sig : Signal dom (BitVec width)) : Signal dom (BitVec width) :=
  sig

def testGenericIdentity4 {dom : DomainConfig}
    (sig : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  testGenericIdentity sig

def testGenericIdentity16 {dom : DomainConfig}
    (sig : Signal dom (BitVec 16)) : Signal dom (BitVec 16) :=
  testGenericIdentity sig

abbrev testClosedWidth : Nat := 8 + 8

def testClosedWidthIdentity {dom : DomainConfig}
    (sig : Signal dom (BitVec testClosedWidth))
    : Signal dom (BitVec testClosedWidth) :=
  sig

def testNativeAdd {dom : DomainConfig} {width : Nat}
    (a b : Signal dom (BitVec width)) : Signal dom (BitVec width) :=
  a + b

def testNativeDerivedWidth {dom : DomainConfig} {width : Nat}
    (sig : Signal dom (BitVec (width + 1))) : Signal dom (BitVec (width + 1)) :=
  sig

def testNativeConstant {width : Nat} : Signal Domain (BitVec width) :=
  Signal.pure (1 : BitVec width)

def testNativeRegister {dom : DomainConfig} {width : Nat}
    (sig : Signal dom (BitVec width)) : Signal dom (BitVec width) :=
  Signal.register (0 : BitVec width) sig

def testNativeMemory {dom : DomainConfig} {addrWidth dataWidth : Nat}
    (writeAddr : Signal dom (BitVec addrWidth))
    (writeData : Signal dom (BitVec dataWidth))
    (writeEnable : Signal dom Bool)
    (readAddr : Signal dom (BitVec addrWidth))
    : Signal dom (BitVec dataWidth) :=
  Signal.memory writeAddr writeData writeEnable readAddr

-- Sequential circuits (with flip-flops/registers)
def test_flipflop (input : Signal Domain (BitVec 16)) : Signal Domain (BitVec 16) :=
  Signal.register (0 : BitVec 16) input
