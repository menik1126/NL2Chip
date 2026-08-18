import Sparkle

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def symbolicIdentity {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  x

def symbolicXor {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  lhs ^^^ rhs

def symbolicConcat {dom : DomainConfig} {HI LO : Nat}
    (hi : Signal dom (BitVec HI))
    (lo : Signal dom (BitVec LO)) : Signal dom (BitVec (HI + LO)) :=
  hi ++ lo

def symbolicSliceLow {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec (W + 1))) : Signal dom (BitVec W) :=
  x.map (BitVec.extractLsb' 0 W ·)

def symbolicZeroExtend {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec (W + 1)) :=
  x.map (·.zeroExtend (W + 1))

def symbolicCastExtend {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec (W + 1)) :=
  Signal.cast x

def symbolicCastTrunc {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec (W + 1))) : Signal dom (BitVec W) :=
  Signal.cast x

def symbolicModulo {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  lhs % rhs

def symbolicRepeatVector {dom : DomainConfig} {W N : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec (N * W)) :=
  Sparkle.Library.RTL.repeatVector (N := N) x

def symbolicIotaVector1 {dom : DomainConfig} {W N : Nat}
    : Signal dom (BitVec (N * W)) :=
  Sparkle.Library.RTL.iotaVector1

@[sparkle_module]
def symbolicChunkReverse {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.reverseBits x

def symbolicMapChunks {dom : DomainConfig} {W N : Nat}
    (x : Signal dom (BitVec (N * W))) : Signal dom (BitVec (N * W)) :=
  Signal.mapChunks symbolicChunkReverse x

/-- A deliberately different-width lane transform. This exercises generated
    child output slices when a packed lane is narrowed. -/
@[sparkle_module]
def symbolicChunkLow2 {dom : DomainConfig}
    (x : Signal dom (BitVec 4)) : Signal dom (BitVec 2) :=
  x.map (BitVec.extractLsb' 0 2 ·)

def symbolicMapChunksNarrow {dom : DomainConfig} {N : Nat}
    (x : Signal dom (BitVec (N * 4))) : Signal dom (BitVec (N * 2)) :=
  Signal.mapChunks symbolicChunkLow2 x

/-- An indexed lane helper: each W-bit chunk is XORed with its zero-based lane
    index. This guards both lane-index wiring and generic generated hierarchy. -/
@[sparkle_module]
def symbolicChunkXorIndex {dom : DomainConfig} {W : Nat}
    (index x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  index ^^^ x

def symbolicMapChunksWithIndex {dom : DomainConfig} {W N : Nat}
    (x : Signal dom (BitVec (N * W))) : Signal dom (BitVec (N * W)) :=
  Signal.mapChunksWithIndex symbolicChunkXorIndex x

def symbolicRegister {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.register (BitVec.ofNat W 1) x

def symbolicMemory {dom : DomainConfig} {ADDR_W DATA_W : Nat}
    (writeAddr : Signal dom (BitVec ADDR_W))
    (writeData : Signal dom (BitVec DATA_W))
    (writeEnable : Signal dom Bool)
    (readAddr : Signal dom (BitVec ADDR_W)) : Signal dom (BitVec DATA_W) :=
  Signal.memory writeAddr writeData writeEnable readAddr

def symbolicComboMemory {dom : DomainConfig} {ADDR_W DATA_W : Nat}
    (writeAddr : Signal dom (BitVec ADDR_W))
    (writeData : Signal dom (BitVec DATA_W))
    (writeEnable : Signal dom Bool)
    (readAddr : Signal dom (BitVec ADDR_W)) : Signal dom (BitVec DATA_W) :=
  Signal.memoryComboRead writeAddr writeData writeEnable readAddr

@[sparkle_module]
def symbolicXorChild {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  lhs ^^^ rhs

def symbolicXorHierarchy {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  symbolicXorChild lhs rhs

def symbolicGenerateNot {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.mapBits (fun bit => Bool.not bit) x

/-- A symbolic-width feedback register. The explicit dff seed fixes the
    loop state type without freezing the retained width W. -/
def symbolicLoopXor {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.loop fun q => Signal.register (BitVec.ofNat W 0) (q ^^^ x)


/-- A symbolic-width feedback register with a packed, mixed-width output.
    This covers the common RTL shape of a state value paired with status flags. -/
def symbolicLoopBundle {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W × Bool × Bool) :=
  let q := Signal.loop fun q => Signal.register (BitVec.ofNat W 0) (q ^^^ x)
  let isZero : Signal dom Bool := q === BitVec.ofNat W 0
  let isOne : Signal dom Bool := q === BitVec.ofNat W 1
  bundle2 q (bundle2 isZero isOne)

/-- A symbolic-width feedback register whose state is itself a packed pair.
    This guards the tuple-state form used by multi-register control circuits. -/
def symbolicPairLoop {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W × BitVec W) :=
  Signal.loop fun state =>
    let next := bundle2 (state.fst ^^^ x) state.snd
    Signal.register (BitVec.ofNat W 0, BitVec.ofNat W 0) next

/-- A wide mixed-width tuple state with its reset seed in a local Lean let.
    Plain tuple seeds must stay host values until register-reset extraction;
    treating them as Signal-valued hardware attempts to instantiate `Prod.mk`. -/
def symbolicWideTupleLoop {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) :
    Signal dom
      (BitVec W × BitVec W × BitVec (W + 1) × BitVec W ×
       BitVec W × BitVec 1 × BitVec 1 × BitVec 1) :=
  Signal.loop fun state =>
    let nextState := bundleAll! [
      projN! state 8 0 ^^^ x,
      projN! state 8 1,
      projN! state 8 2,
      projN! state 8 3,
      projN! state 8 4,
      projN! state 8 5,
      projN! state 8 6,
      projN! state 8 7
    ]
    let initState :
        BitVec W × BitVec W × BitVec (W + 1) × BitVec W ×
        BitVec W × BitVec 1 × BitVec 1 × BitVec 1 :=
      (BitVec.ofNat W 0, BitVec.ofNat W 0, BitVec.ofNat (W + 1) 0,
       BitVec.ofNat W 0, BitVec.ofNat W 0, 0#1, 0#1, 0#1)
    Signal.register initState nextState

/-- Compare symbolic-width state against a retained parameter value. -/
def symbolicDepthCompare {dom : DomainConfig} {DEPTH : Nat}
    (x : Signal dom (BitVec (Sparkle.Library.RTL.clog2 (DEPTH + 1))))
    : Signal dom Bool :=
  let q := Signal.loop fun q =>
    Signal.register (BitVec.ofNat (Sparkle.Library.RTL.clog2 (DEPTH + 1)) 0) x
  q === BitVec.ofNat (Sparkle.Library.RTL.clog2 (DEPTH + 1)) DEPTH
/-- A symbolic derived width used by depth-indexed state such as a FIFO pointer.
    ceiling-log2 DEPTH is preserved as the backend's symbolic clog2 expression. -/
def symbolicDerivedLoop {dom : DomainConfig} {DEPTH : Nat}
    (x : Signal dom (BitVec (Sparkle.Library.RTL.clog2 DEPTH)))
    : Signal dom (BitVec (Sparkle.Library.RTL.clog2 DEPTH)) :=
  Signal.loop fun q =>
    Signal.register (BitVec.ofNat (Sparkle.Library.RTL.clog2 DEPTH) 0) (q ^^^ x)

def symbolicDerivedAlias {dom : DomainConfig} {DEPTH : Nat}
    (x : Signal dom (BitVec (Sparkle.Library.RTL.clog2 DEPTH))) : Signal dom (BitVec (Sparkle.Library.RTL.clog2 DEPTH)) :=
  let W := Sparkle.Library.RTL.clog2 DEPTH
  Signal.loop fun q => Signal.register (BitVec.ofNat W 0) (q ^^^ x)

/-- A mixed Signal/BitVec operator may consume a local packed constant. -/
def symbolicLetMaskXor {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  let mask : BitVec W := BitVec.ofNat W 1
  x ^^^ mask

/-- A retained parameter may be materialized at a clog2-derived width. -/
def symbolicParameterLiteral {dom : DomainConfig} {D : Nat}
    (x : Signal dom (BitVec (Sparkle.Library.RTL.clog2 D + 1)))
    : Signal dom (BitVec (Sparkle.Library.RTL.clog2 D + 1)) :=
  Signal.pure (BitVec.ofNat (Sparkle.Library.RTL.clog2 D + 1) D)

/-- A BitVec value may be an arithmetic expression over the retained width.
    This is common for counters, terminal indices, and width-wide masks. -/
def symbolicValueExpression {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  let terminal := BitVec.ofNat W (W - 1)
  let mask := BitVec.ofNat W (2 ^ W - 1)
  (x + terminal) ^^^ mask

/-- Top-level variadic tuple packing must remain a packed hardware result even
    when Lean reduces `bundleAll!` through applicative `Prod.mk`. -/
def symbolicBundleAllOutput {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) :
    Signal dom (BitVec W × BitVec W × BitVec 1) :=
  bundleAll! [x, x ^^^ BitVec.ofNat W 1, Signal.pure 1#1]

/-- Generic reduction primitive used by parameterized datapaths. -/
def symbolicPopCount {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec (Sparkle.Library.RTL.clog2 (W + 1))) :=
  Sparkle.Library.RTL.popCount x

/-- Parameterized unsigned comparison using the complete Signal comparison API. -/
def symbolicUnsignedGE {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom Bool :=
  Signal.uge lhs rhs

/-- Generic bit reversal must be lowered directly so the retained width becomes
    a SystemVerilog generate-loop bound rather than a Lean-unrolled helper. -/
def symbolicReverseBits {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.reverseBits x

/-- Reverse bits inside a constant number of equal symbolic-width blocks. -/
def symbolicReverseBlocks {dom : DomainConfig} {W BLOCKS : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.reverseBlocks (BLOCKS := BLOCKS) x
