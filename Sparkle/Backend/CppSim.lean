/-
  C++ Simulation Backend

  Generates C++ simulation code from the IR.
  Produces a C++ class with eval()/tick()/reset() methods.
-/

import Sparkle.IR.AST
import Sparkle.IR.Type
import Std.Data.HashMap
import Std.Data.HashSet

namespace Sparkle.Backend.CppSim

open Sparkle.IR.AST
open Sparkle.IR.Type

-- Helper to embed literal braces in string interpolation
private def ob : String := "{"
private def cb : String := "}"

/-- O(1) name-to-type index shared by validation and emission.  Large flattened
    designs perform a lookup for nearly every expression and statement, so an
    association list makes CppSim generation quadratic in the wire count. -/
abbrev TypeMap := Std.HashMap String HWType

def buildTypeMap (m : Module) : TypeMap :=
  let addPorts (types : TypeMap) (ports : List Port) :=
    ports.foldl (fun result port => result.insert port.name port.ty) types
  addPorts (addPorts (addPorts {} m.inputs) m.outputs) m.wires

/-- True when an expression still contains a dimension requiring SV elaboration. -/
partial def exprHasSymbolicDimension : Expr → Bool
  | .const _ width => !width.isConcrete
  | .paramConst value width => !value.isConcrete || !width.isConcrete
  | .ref _ => false
  | .op _ args | .concat args => args.any exprHasSymbolicDimension
  | .resize width value => !width.isConcrete || exprHasSymbolicDimension value
  | .slice expr hi lo =>
      exprHasSymbolicDimension expr || !hi.isConcrete || !lo.isConcrete
  | .index array index =>
      exprHasSymbolicDimension array || exprHasSymbolicDimension index

/-- True when a statement cannot be represented by the concrete C++ backend. -/
def stmtHasSymbolicDimension : Stmt → Bool
  | .assign _ rhs => exprHasSymbolicDimension rhs
  | .signedDot _ lhs rhs laneCount lhsWidth rhsWidth resultWidth =>
      exprHasSymbolicDimension lhs || exprHasSymbolicDimension rhs ||
        !laneCount.isConcrete || !lhsWidth.isConcrete ||
        !rhsWidth.isConcrete || !resultWidth.isConcrete
  | .register _ _ _ input _ => exprHasSymbolicDimension input
  | .memory _ addrWidth dataWidth depth _ writeAddr writeData writeEnable readAddr _ _ =>
      !addrWidth.isConcrete || !dataWidth.isConcrete || !depth.isConcrete ||
        [writeAddr, writeData, writeEnable, readAddr].any exprHasSymbolicDimension
  | .inst _ _ connections parameterOverrides =>
      !parameterOverrides.isEmpty ||
        connections.any (fun (_, expr) => exprHasSymbolicDimension expr)

/-- CppSim is deliberately a concrete backend; native SV parameters must first
    be specialized to one concrete environment. -/
def moduleRequiresSpecialization (m : Module) : Bool :=
  !m.nativeItems.isEmpty || !m.parameters.isEmpty ||
    (m.inputs ++ m.outputs ++ m.wires).any (fun port => port.ty.bitWidth?.isNone) ||
    m.body.any stmtHasSymbolicDimension ||
    m.assertions.any (fun (_, expr) => exprHasSymbolicDimension expr)

/-- CppSim has no representation for zero-width packed values or zero-length
    arrays.  Reuse the IR contract checker so hand-written IR and parser output
    fail closed just like elaborated Sparkle modules. -/
def moduleDimensionError? (m : Module) : Option String :=
  match m.validateDimensions with
  | .ok _ => none
  | .error message => some message

def specializationError (m : Module) : String :=
  if !m.nativeItems.isEmpty then
    s!"Sparkle CppSim cannot execute SystemVerilog-native generate/procedural items in module '{m.name}'; emit SystemVerilog or lower the native items to normalized IR first"
  else
    s!"Sparkle CppSim cannot emit parameterized module '{m.name}'; specialize all module parameters and symbolic dimensions before C++ generation"

def emitSpecializationError (m : Module) : String :=
  s!"#error \"{specializationError m}\"\n"

def dimensionError (m : Module) (message : String) : String :=
  s!"Sparkle CppSim cannot emit module '{m.name}': {message}"

/-- Look up bit-width for a name in the type map -/
def lookupWidth (typeMap : TypeMap) (name : String) : Nat :=
  match typeMap.get? name with
  | some ty => ty.bitWidth?.getD 0
  | none => 0

/-- Sanitize a name to be a valid C++ identifier -/
def sanitizeName (name : String) : String :=
  name.replace "." "_"
    |>.replace "-" "_"
    |>.replace " " "_"
    |>.replace "'" "_prime"
    |>.replace "#" ""

/-- CppSim places ports and wires in one C++ identifier namespace.  Distinct
    IR names that sanitize to the same spelling would otherwise alias or let a
    local wire shadow a public member, invalidating checked simulation. -/
def validateSanitizedCppValueNames (m : Module) : Except String Unit := do
  let declarations := (m.inputs ++ m.outputs ++ m.wires).map fun port =>
    (port.name, port.ty)
  let mut seen : Std.HashMap String (String × HWType) := {}
  for (name, ty) in declarations do
    let sanitized := sanitizeName name
    if sanitized.isEmpty then
      throw s!"Sparkle CppSim module '{m.name}' value '{name}' sanitizes to an empty C++ identifier"
    match seen.get? sanitized with
    | some (previous, previousType) =>
        -- An exact port/wire spelling denotes the same IR net and is filtered
        -- from the local declarations by `emitModule`.  Distinct spellings
        -- that sanitize alike would instead alias two different values.
        if previous != name then
          throw s!"Sparkle CppSim module '{m.name}' values '{previous}' and '{name}' collide as C++ identifier '{sanitized}'"
        if previousType != ty then
          throw s!"Sparkle CppSim module '{m.name}' value '{name}' has conflicting duplicate types"
    | none =>
        seen := seen.insert sanitized (name, ty)

/-- Width inference used by the deliberately small wide-container subset.
    A packed value wider than 64 bits is not a C++ scalar, but a concat whose
    immediate leaves are 32/64-bit scalars can still be copied into the
    backend's little-endian `std::array<uint32_t, _>` representation without
    evaluating a wide arithmetic expression. -/
partial def passiveExprWidth (typeMap : TypeMap) : Expr → Nat
  | .const _ width | .paramConst _ width | .resize width _ =>
      width.toNat?.getD 0
  | .ref name => lookupWidth typeMap name
  | .slice _ hi lo => (hi - lo + 1).toNat?.getD 0
  | .concat args => args.foldl (fun total arg => total + passiveExprWidth typeMap arg) 0
  | .index array _ =>
      match array with
      | .ref name =>
          match typeMap.get? name with
          | some (.array _ elementType) => elementType.bitWidth?.getD 0
          | _ => 0
      | _ => 0
  | .op .eq _ | .op .lt_u _ | .op .lt_s _ | .op .le_u _
  | .op .le_s _ | .op .gt_u _ | .op .gt_s _ | .op .ge_u _
  | .op .ge_s _ => 1
  | .op .udiv args | .op .sdiv args =>
    match args with
    | [arg, _] => passiveExprWidth typeMap arg
    | _ => 0
  | .op .mux args =>
      match args with
      | [_, thenValue, elseValue] =>
          max (passiveExprWidth typeMap thenValue) (passiveExprWidth typeMap elseValue)
      | _ => 0
  | .op .shl args | .op .shr args | .op .asr args =>
      match args with
      | [value, _] => passiveExprWidth typeMap value
      | _ => 0
  | .op _ args =>
      match args with
      | [lhs, rhs] => max (passiveExprWidth typeMap lhs) (passiveExprWidth typeMap rhs)
      | [value] => passiveExprWidth typeMap value
      | _ => 0

def isPackedBitVectorOfWidth (typeMap : TypeMap)
    (name : String) (width : Nat) : Bool :=
  typeMap.get? name |>.any fun ty =>
    match ty with
    | .bitVector packedWidth => packedWidth.toNat? == some width
    | _ => false

/-- The one supported operation that consumes a wide packed container is a
    concrete slice whose result fits the scalar backend.  Bounds must select
    actual source bits; this keeps the C++ implementation aligned with the IR
    rather than assigning semantics to malformed/out-of-range slices. -/
def isSupportedPassiveWideSlice (typeMap : TypeMap)
    (value : Expr) (hi lo : DimExpr) : Bool :=
  match value, hi.toNat?, lo.toNat? with
  | .ref source, some concreteHi, some concreteLo =>
      let sourceWidth := lookupWidth typeMap source
      sourceWidth > 64 && isPackedBitVectorOfWidth typeMap source sourceWidth &&
        concreteLo ≤ concreteHi && concreteHi < sourceWidth &&
        concreteHi - concreteLo + 1 ≤ 64
  | _, _, _ => false

/-- The optimizer represents a removed <=64-bit wire assignment as an explicit
    `.resize lhsWidth rhs`.  That resize is the materialization boundary that
    the original scalar C++ member assignment supplied.  Only structural
    scalar expressions are admitted behind that marker: arbitrary `.op` trees
    still need per-node width normalization that the scalar emitter does not
    provide, and narrow multiplication can trigger C++ signed-overflow UB. -/
partial def passiveMaterializedScalarExprSupported
    (typeMap : TypeMap) : Expr → Bool
  | .const _ width | .paramConst _ width =>
      width.toNat?.any (fun concrete => concrete > 0 && concrete ≤ 64)
  | .ref name =>
      typeMap.get? name |>.any fun ty =>
        match ty with
        | .bit => true
        | .bitVector width => width.toNat?.any (· ≤ 64)
        | .array _ _ => false
  | .op _ _ => false
  | .concat args =>
      !args.isEmpty && args.all (passiveMaterializedScalarExprSupported typeMap)
  | .resize width value =>
      width.toNat?.any (fun concrete => concrete > 0 && concrete ≤ 64) &&
        passiveMaterializedScalarExprSupported typeMap value
  | .slice value hi lo =>
      if isSupportedPassiveWideSlice typeMap value hi lo then true
      else
        match hi.toNat?, lo.toNat? with
        | some concreteHi, some concreteLo =>
            let sourceWidth := passiveExprWidth typeMap value
            concreteLo ≤ concreteHi && concreteLo < 64 &&
              sourceWidth > 0 && sourceWidth ≤ 64 &&
              passiveMaterializedScalarExprSupported typeMap value
        | _, _ => false
  | .index (.ref name) index =>
      let indexWidth := passiveExprWidth typeMap index
      indexWidth > 0 && indexWidth ≤ 64 &&
        (typeMap.get? name |>.any fun ty =>
          match ty with
          | .array _ .bit => passiveMaterializedScalarExprSupported typeMap index
          | .array _ (.bitVector width) =>
              width.toNat?.any (fun concrete => concrete > 0 && concrete ≤ 64) &&
                passiveMaterializedScalarExprSupported typeMap index
          | _ => false)
  | .index _ _ => false

/-- Safe grammar for a scalar leaf of a passive wide concat.  Inline `.op`
    trees are rejected, including beneath the concrete <=64-bit `.resize`
    assignment markers inserted by `Optimize.inlineSingleUseWires`.
    Structural nodes may nest such markers without losing the boundary.  Array
    indexing is allowed only when it produces a scalar packed element, never an
    unpacked aggregate. -/
partial def passiveWideScalarLeafSupported
    (typeMap : TypeMap) : Expr → Bool
  | .const _ width | .paramConst _ width =>
      width.toNat?.any (fun concrete => concrete > 0 && concrete ≤ 64)
  | .ref name =>
      typeMap.get? name |>.any fun ty =>
        match ty with
        | .bit => true
        | .bitVector width => width.toNat?.any (· ≤ 64)
        | .array _ _ => false
  | .op _ _ => false
  | .concat args =>
      !args.isEmpty && args.all (passiveWideScalarLeafSupported typeMap)
  | .resize targetWidth value =>
      targetWidth.toNat?.any (fun concrete => concrete > 0 && concrete ≤ 64) &&
        passiveExprWidth typeMap value ≤ 64 &&
        passiveMaterializedScalarExprSupported typeMap value
  | .slice value hi lo =>
      if isSupportedPassiveWideSlice typeMap value hi lo then true
      else
        match hi.toNat?, lo.toNat? with
        | some concreteHi, some concreteLo =>
            let sourceWidth := passiveExprWidth typeMap value
            concreteLo ≤ concreteHi && concreteLo < 64 &&
              sourceWidth > 0 && sourceWidth ≤ 64 &&
              passiveWideScalarLeafSupported typeMap value
        | _, _ => false
  | .index (.ref name) index =>
      let indexWidth := passiveExprWidth typeMap index
      indexWidth > 0 && indexWidth ≤ 64 &&
        (typeMap.get? name |>.any fun ty =>
          match ty with
          | .array _ .bit => passiveWideScalarLeafSupported typeMap index
          | .array _ (.bitVector width) =>
              width.toNat?.any (fun concrete => concrete > 0 && concrete ≤ 64) &&
                passiveWideScalarLeafSupported typeMap index
          | _ => false)
  | .index _ _ => false

/-- A concat argument for a passive wide assignment is either a safe structural
    scalar expression, an explicit <=64-bit scalar materialization boundary,
    or a reference to an already-materialized packed word container.  The wide
    case remains restricted to a plain reference: CppSim still performs no
    arithmetic, resize, slice, or mux on wide values. -/
def isSupportedPassiveConcatArg (typeMap : TypeMap)
    (arg : Expr) : Bool :=
  let width := passiveExprWidth typeMap arg
  width > 0 &&
    if width ≤ 64 then
      passiveWideScalarLeafSupported typeMap arg
    else
      match arg with
      | .ref source => isPackedBitVectorOfWidth typeMap source width
      | _ => false

/-- Wide concat emission clears the destination before packing.  Detect a
    target reference at any depth so a slice/resize/index wrapper cannot hide
    an alias whose value would be destroyed before it is read. -/
partial def passiveExprContainsRef (target : String) : Expr → Bool
  | .ref name => name == target
  | .op _ args | .concat args => args.any (passiveExprContainsRef target)
  | .resize _ value | .slice value _ _ => passiveExprContainsRef target value
  | .index array index =>
      passiveExprContainsRef target array || passiveExprContainsRef target index
  | .const _ _ | .paramConst _ _ => false

/-- The only packed values above 64 bits that CppSim currently executes are
    passive word containers.  They may be copied from another same-width packed
    container, or assembled by concatenating scalar expressions and existing
    passive containers.  Packing is bit-exact even when a leaf crosses a
    32-bit storage-word boundary.  In particular, this does not admit wide
    arithmetic, slices, resizes, muxes, inputs, or registers. -/
def isSupportedPassiveWideAssignment (typeMap : TypeMap)
    (target : String) (targetWidth : Nat) (rhs : Expr) : Bool :=
  targetWidth > 64 && isPackedBitVectorOfWidth typeMap target targetWidth &&
    match rhs with
    | .ref source =>
        isPackedBitVectorOfWidth typeMap source targetWidth
    | .concat args =>
        passiveExprWidth typeMap rhs == targetWidth && !args.isEmpty &&
          args.all (isSupportedPassiveConcatArg typeMap) &&
          -- Emission clears the destination words before packing.  Reject an
          -- in-place concat at any expression depth so a source can never be
          -- clobbered before it is read.
          !args.any (passiveExprContainsRef target)
    | _ => false

/-- Strict validation used when native parameters have just been specialized.
    The legacy CppSim emitter represents packed values above 64 bits as word
    arrays but does not yet implement general assignments or arithmetic over
    those arrays.  Reject such operations instead of accepting a specialization
    whose generated C++ would contain a `// skipped` assignment. -/
def validateSpecializedDesign (d : Design)
    (maxMemoryDepth : Nat := DimExpr.maxNatWorkWidth) : Except String Unit := do
  for module_ in d.modules do
    if d.modules.countP (fun other => other.name == module_.name) > 1 then
      throw s!"Sparkle CppSim design contains duplicate module name '{module_.name}'"
    if let some conflict := d.modules.find? fun other =>
        other.name != module_.name && sanitizeName other.name == sanitizeName module_.name then
      throw s!"Sparkle CppSim module names '{module_.name}' and '{conflict.name}' both emit as C++ class '{sanitizeName module_.name}'"
    validateSanitizedCppValueNames module_
    if module_.isPrimitive then
      throw s!"Sparkle CppSim cannot execute primitive/blackbox module '{module_.name}'"
    unless module_.nativeItems.isEmpty do
      throw s!"Sparkle CppSim cannot execute SystemVerilog-native generate/procedural items in module '{module_.name}'"
    let rec validateType (portName : String) (allowWidePacked : Bool) : HWType → Except String Unit
        | .bit => pure ()
        | .bitVector width =>
            match width.toNat? with
            | some concreteWidth =>
                if concreteWidth > 64 && !allowWidePacked then
                  throw s!"Sparkle CppSim cannot execute {concreteWidth}-bit value '{module_.name}.{portName}'; native packed values above 64 bits are not implemented"
                else
                  pure ()
            | none =>
                throw s!"Sparkle CppSim value '{module_.name}.{portName}' still has a symbolic width"
        | .array _ elementType => validateType portName false elementType
    -- Wide input values cannot cross the uint64_t JIT ABI.  Wide outputs and
    -- internal wires may exist only as passive word containers; statement
    -- validation below checks every producer.
    for port in module_.inputs do
      validateType port.name false port.ty
    for port in module_.outputs ++ module_.wires do
      validateType port.name true port.ty
    let typeMap := buildTypeMap module_
    for statement in module_.body do
      match statement with
      | .signedDot output lhs rhs laneCount lhsWidth rhsWidth resultWidth =>
          unless laneCount.isConcrete && lhsWidth.isConcrete &&
            rhsWidth.isConcrete && resultWidth.isConcrete do
            throw s!"Sparkle CppSim signed dot '{module_.name}.{output}' retains symbolic dimensions"
          unless passiveExprWidth typeMap lhs ≤ 64 && passiveExprWidth typeMap rhs ≤ 64 do
            throw s!"Sparkle CppSim signed dot '{module_.name}.{output}' exceeds the 64-bit concrete backend limit"
      | .assign lhs rhs =>
          let width := lookupWidth typeMap lhs
          if width > 64 && !isSupportedPassiveWideAssignment typeMap lhs width rhs then
            throw s!"Sparkle CppSim cannot execute {width}-bit assignment '{module_.name}.{lhs}'; only same-width packed copies and passive scalar/container concats are supported above 64 bits"
      | .register output _ _ _ _ =>
          let width := lookupWidth typeMap output
          if width > 64 then
            throw s!"Sparkle CppSim cannot execute {width}-bit register '{module_.name}.{output}'; native wide-value operations above 64 bits are not implemented"
      | .memory name addrWidth dataWidth depth .. =>
          match addrWidth.toNat?, dataWidth.toNat?, depth.toNat? with
          | some concreteAddrWidth, some concreteDataWidth, some concreteDepth =>
              if concreteDepth == 0 then
                throw s!"Sparkle CppSim cannot execute zero-depth memory '{module_.name}.{name}'"
              if concreteDepth > maxMemoryDepth then
                throw s!"Sparkle CppSim cannot allocate memory '{module_.name}.{name}' with depth {concreteDepth}; the configured concrete-memory limit is {maxMemoryDepth} entries"
              if concreteAddrWidth >= 64 || concreteDataWidth > 64 then
                throw s!"Sparkle CppSim cannot execute memory '{module_.name}.{name}' with AW={concreteAddrWidth}, DW={concreteDataWidth}; native values above 64 bits are not implemented"
          | _, _, _ =>
              throw s!"Sparkle CppSim memory '{module_.name}.{name}' still has symbolic dimensions"
      | .inst childName instanceName _ overrides =>
          unless overrides.isEmpty do
            throw s!"Sparkle CppSim instance '{module_.name}.{instanceName}' still has parameter overrides"
          unless d.modules.any (fun child => child.name == childName) do
            throw s!"Sparkle CppSim instance '{module_.name}.{instanceName}' refers to missing module '{childName}'"

/-- The public JIT memory ABI still transports one word through `uint32_t`.
    The class backend itself supports scalar memory elements through 64 bits,
    but exposing those memories through JIT would silently truncate the upper
    half.  Keep the checked JIT API fail-closed until its ABI is widened. -/
def validateJITMemoryABI (d : Design) : Except String Unit := do
  for module_ in d.modules do
    for statement in module_.body do
      match statement with
      | .memory name _ dataWidth _ .. =>
          match dataWidth.toNat? with
          | some concreteDataWidth =>
              if concreteDataWidth > 32 then
                throw s!"Sparkle CppSim JIT memory '{module_.name}.{name}' has DW={concreteDataWidth}, but the current jit_set_mem/jit_get_mem ABI supports at most 32 bits"
          | none =>
              throw s!"Sparkle CppSim JIT memory '{module_.name}.{name}' still has a symbolic data width"
      | _ => pure ()

/-- Convert HWType to C++ type string -/
def emitCppType : HWType → String
  | .bit => "uint8_t"
  | .bitVector w =>
    match w.toNat? with
    | none => "SparkleCppSim_requires_concrete_width"
    | some concreteWidth =>
      if concreteWidth ≤ 8 then "uint8_t"
      else if concreteWidth ≤ 16 then "uint16_t"
      else if concreteWidth ≤ 32 then "uint32_t"
      else if concreteWidth ≤ 64 then "uint64_t"
      else  -- Wide type: use array of uint32_t words
        let nWords := (concreteWidth + 31) / 32
        "std::array<uint32_t, " ++ toString nWords ++ ">"
  | .array size elemType =>
    match size.toNat? with
    | none => "SparkleCppSim_requires_concrete_array_size"
    | some concreteSize =>
      "std::array<" ++ emitCppType elemType ++ ", " ++ toString concreteSize ++ ">"

/-- Check if a width needs masking (not a native C++ integer width) -/
def needsMask (w : Nat) : Bool :=
  w != 8 && w != 16 && w != 32 && w != 64

/-- Emit a bit mask expression for the given width -/
def emitMask (w : Nat) : String :=
  if !needsMask w then ""
  else if w == 1 then "1"
  else s!"((1ULL << {w}) - 1)"

/-- Wrap an expression with a mask if the width requires it -/
def applyMask (expr : String) (w : Nat) : String :=
  let mask := emitMask w
  if mask.isEmpty then expr
  else s!"(({expr}) & {mask})"

/-- Check if an IR expression produces a result that is already correctly masked.
    Invariant: every assignment applies a mask, so .ref reads yield masked values. -/
partial def exprIsMasked (w : Nat) : Expr → Bool
  | .const _ _ => true  -- constants are always exact
  | .paramConst _ width => width.toNat? == some w
  | .ref _ => true  -- all wires are masked at their assignment site
  | .resize width _ => width.toNat? == some w
  | .op .eq _ | .op .lt_u _ | .op .lt_s _ | .op .le_u _
  | .op .le_s _ | .op .gt_u _ | .op .gt_s _ | .op .ge_u _
  | .op .ge_s _ => w == 1  -- comparisons produce 0 or 1
  | .slice _ hi lo => (hi - lo + 1).toNat? == some w  -- slice is already exact width
  | .op .mux [_, t, e] => exprIsMasked w t && exprIsMasked w e
  | .op .and [a, b] => exprIsMasked w a || exprIsMasked w b  -- AND is masked if either operand is
  | .op .or [a, b] => exprIsMasked w a && exprIsMasked w b  -- OR of masked stays in width
  | .op .xor [a, b] => exprIsMasked w a && exprIsMasked w b  -- XOR of masked stays in width
  | .op .shr _ => true  -- right-shift moves bits toward LSB, no new upper bits
  | .op .asr _ => true  -- cast to unsigned in emitExpr handles width
  | _ => !needsMask w  -- native widths don't need masking

/-- Convert Operator to C++ operator symbol -/
def emitCppOperator (op : Operator) : String :=
  match op with
  | .and => "&"
  | .or  => "|"
  | .xor => "^"
  | .not => "~"
  | .add => "+"
  | .sub => "-"
  | .mul => "*"
  | .udiv => "/"
  | .sdiv => "/"
  | .eq  => "=="
  | .lt_u => "<"
  | .lt_s => "<"
  | .le_u => "<="
  | .le_s => "<="
  | .gt_u => ">"
  | .gt_s => ">"
  | .ge_u => ">="
  | .ge_s => ">="
  | .shl => "<<"
  | .shr => ">>"
  | .asr => ">>"
  | .sext => "$signed"
  | .neg => "-"
  | .mux => "?"

/-- Get signed cast type for a given width -/
def signedCastType (w : Nat) : String :=
  if w ≤ 8 then "int8_t"
  else if w ≤ 16 then "int16_t"
  else if w ≤ 32 then "int32_t"
  else "int64_t"

/-- Interpret a packed C++ value as a two's-complement integer with exactly
    `width` meaningful bits. -/
def emitSignedValue (value : String) (width : Nat) : String :=
  if width == 0 then "0"
  else if width >= 64 then
    s!"((int64_t)(uint64_t)({value}))"
  else
    s!"((int64_t)(((uint64_t)({value}) ^ (1ULL << {width - 1})) - (1ULL << {width - 1})))"

/-- Best-effort width inference for an expression -/
partial def inferExprWidth (typeMap : TypeMap) : Expr → Nat
  | .const _ w => w.toNat?.getD 0
  | .paramConst _ w => w.toNat?.getD 0
  | .ref name => lookupWidth typeMap name
  | .resize width _ => width.toNat?.getD 0
  | .slice _ hi lo => (hi - lo + 1).toNat?.getD 0
  | .concat args =>
    args.foldl (fun acc arg => acc + inferExprWidth typeMap arg) 0
  | .index arr _ =>
    match arr with
    | .ref name =>
      match typeMap.get? name with
      | some (.array _ elemType) => elemType.bitWidth?.getD 0
      | _ => 0
    | _ => 0
  | .op .eq _ | .op .lt_u _ | .op .lt_s _ | .op .le_u _
  | .op .le_s _ | .op .gt_u _ | .op .gt_s _ | .op .ge_u _
  | .op .ge_s _ => 1
  | .op .mux args =>
    match args with
    | [_, thenVal, elseVal] =>
      max (inferExprWidth typeMap thenVal) (inferExprWidth typeMap elseVal)
    | _ => 0
  | .op .shl args | .op .shr args | .op .asr args =>
    match args with
    | [arg1, _] => inferExprWidth typeMap arg1
    | _ => 0
  | .op _ args =>
    match args with
    | [arg1, arg2] =>
      max (inferExprWidth typeMap arg1) (inferExprWidth typeMap arg2)
    | [arg1] => inferExprWidth typeMap arg1
    | _ => 0

/-- Validate the concrete C++ expression language recursively.  The backend can
    execute packed expression nodes and casts up to 64 bits; wider packed values
    use an array representation for which inline operations are not implemented.
    Array references themselves are exempt because indexing a wide aggregate can
    still produce a supported scalar element. -/
partial def validateResizeExpr (typeMap : TypeMap)
    (moduleName role : String) (expression : Expr) : Except String Unit := do
  let packedWidth? := match expression with
    | .ref name =>
        match typeMap.get? name with
        | some (.array _ _) => none
        | _ => some (inferExprWidth typeMap expression)
    | _ => some (inferExprWidth typeMap expression)
  if let some concreteWidth := packedWidth? then
    if concreteWidth > 64 then
      throw s!"Sparkle CppSim cannot execute {concreteWidth}-bit expression in {role} of module '{moduleName}'; packed expression nodes above 64 bits are not implemented"
  match expression with
  | .resize width value => do
      let targetWidth ← match width.toNat? with
        | some concrete => pure concrete
        | none => throw s!"Sparkle CppSim {role} in module '{moduleName}' retains symbolic resize width '{width}'"
      let sourceWidth := inferExprWidth typeMap value
      if targetWidth == 0 then
        throw s!"Sparkle CppSim {role} in module '{moduleName}' has a zero resize width"
      if sourceWidth == 0 then
        throw s!"Sparkle CppSim cannot determine the resize operand width in {role} of module '{moduleName}'"
      if targetWidth > 64 || sourceWidth > 64 then
        throw s!"Sparkle CppSim cannot execute {sourceWidth}-to-{targetWidth}-bit resize in {role} of module '{moduleName}'; packed resize values above 64 bits are not implemented"
      validateResizeExpr typeMap moduleName role value
  | .op _ args | .concat args =>
      args.forM (validateResizeExpr typeMap moduleName role)
  | .slice value hi lo =>
      if isSupportedPassiveWideSlice typeMap value hi lo then
        pure ()
      else
        validateResizeExpr typeMap moduleName role value
  | .index array index =>
      validateResizeExpr typeMap moduleName role array *>
        validateResizeExpr typeMap moduleName role index
  | .const _ _ | .paramConst _ _ | .ref _ => pure ()

def validateModuleResizeExprs (m : Module) : Except String Unit := do
  let typeMap := buildTypeMap m
  for statement in m.body do
      match statement with
    | .signedDot _ lhs rhs _ _ _ _ =>
        validateResizeExpr typeMap m.name "signed dot lhs" lhs *>
          validateResizeExpr typeMap m.name "signed dot rhs" rhs
    | .assign lhs rhs =>
        let width := lookupWidth typeMap lhs
        if width > 64 then
          unless isSupportedPassiveWideAssignment typeMap lhs width rhs do
            throw s!"Sparkle CppSim cannot execute {width}-bit assignment '{m.name}.{lhs}'; only same-width packed copies and passive scalar/container concats are supported above 64 bits"
          -- Do not feed the wide concat/copy root to the scalar validator.
          -- Its concat leaves still are ordinary scalar expressions and must
          -- independently satisfy every existing <=64-bit safety check.
          match rhs with
          | .concat args =>
              for arg in args do
                -- Wide concat arguments are already restricted to plain
                -- packed-container references by
                -- `isSupportedPassiveWideAssignment`; only scalar leaves
                -- belong in the scalar expression validator.
                if passiveExprWidth typeMap arg ≤ 64 then
                  validateResizeExpr typeMap m.name s!"assignment '{lhs}' concat leaf" arg
          | .ref _ => pure ()
          | _ =>
              throw s!"Sparkle CppSim internal error: unsupported passive wide assignment '{m.name}.{lhs}'"
        else
          validateResizeExpr typeMap m.name s!"assignment '{lhs}'" rhs
    | .register output _ _ input _ =>
        validateResizeExpr typeMap m.name s!"register '{output}'" input
    | .memory name _ _ _ _ writeAddr writeData writeEnable readAddr _ _ =>
        for (portRole, expression) in
            [("write address", writeAddr), ("write data", writeData),
             ("write enable", writeEnable), ("read address", readAddr)] do
          validateResizeExpr typeMap m.name s!"memory '{name}' {portRole}" expression
    | .inst _ instanceName connections _ =>
        for (portName, expression) in connections do
          validateResizeExpr typeMap m.name
            s!"instance '{instanceName}' connection '{portName}'" expression
  for (assertionName, expression) in m.assertions do
    validateResizeExpr typeMap m.name s!"assertion '{assertionName}'" expression

def moduleResizeError? (m : Module) : Option String :=
  match validateModuleResizeExprs m with
  | .ok _ => none
  | .error message => some message

/-- Read an at-most-64-bit concrete slice from the little-endian word-array
    representation used for passive wide packed values.  At most three source
    words can overlap such a slice. -/
private def emitPassiveWideSlice (source : String) (concreteHi concreteLo : Nat) : String :=
  let sliceWidth := concreteHi - concreteLo + 1
  let firstWord := concreteLo / 32
  let touchedWords := (concreteLo % 32 + sliceWidth + 31) / 32
  let sliceEnd := concreteLo + sliceWidth
  let terms := (List.range touchedWords).map fun relativeWord =>
    let wordIndex := firstWord + relativeWord
    let wordStart := wordIndex * 32
    let overlapStart := max concreteLo wordStart
    let overlapEnd := min sliceEnd (wordStart + 32)
    let overlapWidth := overlapEnd - overlapStart
    let sourceShift := overlapStart - wordStart
    let resultShift := overlapStart - concreteLo
    let mask := if overlapWidth == 32 then "0xffffffffULL"
      else s!"((1ULL << {overlapWidth}) - 1ULL)"
    let selected :=
      s!"(((uint64_t){sanitizeName source}[{wordIndex}] >> {sourceShift}) & {mask})"
    if resultShift == 0 then selected else s!"({selected} << {resultShift})"
  "(" ++ String.intercalate " | " terms ++ ")"

/-- Convert IR expression to C++ expression -/
partial def emitExpr (typeMap : TypeMap) (e : Expr) : String :=
  match e with
  | .const value width =>
    match width.toNat? with
    | none => "SparkleCppSim_symbolic_constant_requires_specialization"
    | some concreteWidth =>
      let cppType := emitCppType (.bitVector width)
      -- Normalize every constant at its IR width before forming a C++
      -- literal.  Positive parameter constants may be much larger than
      -- `ULL` before the final BitVec truncation (for example `(1<<80)-1`
      -- at width 8); emitting that mathematical Nat directly is rejected by
      -- C++ compilers or relies on implementation extensions.
      let modulus : Int := (2 : Int) ^ concreteWidth
      let unsigned := ((value % modulus) + modulus) % modulus
      s!"({cppType})0x{Nat.toDigits 16 unsigned.toNat |> String.ofList}ULL"

  | .paramConst value width =>
    match value.toNat?, width.toNat? with
    | some concreteValue, some _ =>
        emitExpr typeMap (.const (Int.ofNat concreteValue) width)
    | _, _ => "SparkleCppSim_parameter_constant_requires_specialization"

  | .ref name =>
    sanitizeName name

  | .resize width value =>
    match width.toNat? with
    | none => "SparkleCppSim_symbolic_resize_requires_specialization"
    | some concreteWidth =>
      let sourceWidth := inferExprWidth typeMap value
      if sourceWidth == 0 then
        "SparkleCppSim_resize_source_width_is_unknown"
      else
        -- Normalize the operand at its own IR width before changing widths.
        -- This is essential for promoted C++ arithmetic: an 8-bit IR add
        -- 255+1 wraps to zero before a 16-bit resize, while raw C++ would
        -- otherwise carry the value 256 into the outer cast.
        let sourceType := emitCppType (.bitVector sourceWidth)
        let sourceCasted := s!"(({sourceType})({emitExpr typeMap value}))"
        let sourceValue := applyMask sourceCasted sourceWidth
        let targetType := emitCppType (.bitVector width)
        let targetCasted := s!"(({targetType})({sourceValue}))"
        applyMask targetCasted concreteWidth

  | .concat args =>
    -- Concat: shift+OR chain
    match args with
    | [] => "(uint8_t)0ULL"
    | [single] => emitExpr typeMap single
    | _ =>
      let widths := args.map (inferExprWidth typeMap ·)
      let totalWidth : Nat := widths.foldl Nat.add 0
      let resultType := emitCppType (.bitVector (.literal totalWidth))
      let pairs := args.zip widths
      -- foldr: process right-to-left, accumulating shift from LSB
      let (terms, _) := pairs.foldr (fun (arg, w) (acc, shift) =>
        let expr := emitExpr typeMap arg
        let term := if shift > 0 then
          "((" ++ resultType ++ ")" ++ expr ++ " << " ++ toString shift ++ ")"
        else
          "(" ++ resultType ++ ")" ++ expr
        (term :: acc, shift + w)
      ) ([], 0)
      "(" ++ String.intercalate " | " terms ++ ")"

  | .slice e hi lo =>
    match hi.toNat?, lo.toNat? with
    | some concreteHi, some concreteLo =>
      if isSupportedPassiveWideSlice typeMap e hi lo then
        match e with
        | .ref source => emitPassiveWideSlice source concreteHi concreteLo
        | _ => "SparkleCppSim_internal_wide_slice_source_error"
      else
        let sliceWidth := concreteHi - concreteLo + 1
        let source := s!"((uint64_t)({emitExpr typeMap e}))"
        -- Always mask slice results for widths < 64.  The inner expression may be
        -- wider than sliceWidth (e.g., .slice(.op .shr [32-bit, 12]) 7 0 produces
        -- 20 bits, not 8).  We cannot rely on emitMask/needsMask which skip native
        -- widths (8,16,32) assuming C++ variable-type truncation — that doesn't
        -- hold for inline expressions within concats.
        -- A scalar source is at most 64 packed bits in checked CppSim.  Selecting
        -- above bit 63 is therefore zero; never emit a C++ shift count >= 64 (UB).
        if concreteLo >= 64 then "0ULL"
        else if sliceWidth >= 64 then
          if concreteLo == 0 then source
          else s!"({source} >> {concreteLo})"
        else if concreteLo == 0 then
          s!"({source} & ((1ULL << {sliceWidth}) - 1))"
        else
          s!"(({source} >> {concreteLo}) & ((1ULL << {sliceWidth}) - 1))"
    | _, _ => "SparkleCppSim_symbolic_slice_requires_specialization"

  | .index arr idx =>
    s!"{emitExpr typeMap arr}[{emitExpr typeMap idx}]"

  | .op .mux args =>
    match args with
    | [cond, thenVal, elseVal] =>
      s!"({emitExpr typeMap cond} ? {emitExpr typeMap thenVal} : {emitExpr typeMap elseVal})"
    | _ => "/* ERROR: mux requires 3 arguments */"

  | .op .not args =>
    match args with
    | [arg] =>
      -- IR `.not` is a width-preserving bitwise complement.  Cast back to
      -- the operand's packed type before returning so C++ integer promotion
      -- cannot sign-extend an 8/16-bit complement inside a concat or resize.
      let width := inferExprWidth typeMap arg
      if width == 0 then "SparkleCppSim_not_operand_width_is_unknown"
      else
        let cppType := emitCppType (.bitVector width)
        applyMask s!"(({cppType})(~({emitExpr typeMap arg})))" width
    | _ => "/* ERROR: not requires 1 argument */"

  | .op .neg args =>
    match args with
    | [arg] => s!"(-{emitExpr typeMap arg})"
    | _ => "/* ERROR: neg requires 1 argument */"

  | .op .sext args =>
    match args with
    | [arg] =>
      let width := inferExprWidth typeMap arg
      emitSignedValue (emitExpr typeMap arg) width
    | _ => "/* ERROR: sext requires 1 argument */"

  | .op operator args =>
    match args with
    | [arg1, arg2] =>
      match operator with
      | .shl | .shr =>
        let width := inferExprWidth typeMap arg1
        if width == 0 then
          "SparkleCppSim_shift_operand_width_is_unknown"
        else
          -- Evaluate logical shifts in an unsigned 64-bit carrier.  Narrow IR
          -- values are masked at their assignment/resize boundary, while this
          -- wider carrier preserves intentional byte-lane placement such as
          -- `(byte << 24)` before it is ORed into a 32-bit word.  Guarding at
          -- the carrier width avoids C++'s undefined oversized shifts without
          -- incorrectly treating the narrow source width as the expression's
          -- surrounding evaluation context.
          let lhs := s!"((uint64_t)({emitExpr typeMap arg1}))"
          let rhs := s!"((uint64_t)({emitExpr typeMap arg2}))"
          let shifted := if operator == .shl then
              s!"(({lhs}) << ({rhs}))"
            else s!"(({lhs}) >> ({rhs}))"
          -- C++ shifts by the promoted operand width or more are undefined;
          -- the backend's packed carrier is 64 bits, and later width masks
          -- recover the declared BitVec/SystemVerilog result.
          s!"(({rhs} >= 64ULL) ? 0ULL : {shifted})"
      | .asr =>
        let width := inferExprWidth typeMap arg1
        if width == 0 then
          "SparkleCppSim_shift_operand_width_is_unknown"
        else
          let lhs := s!"((uint64_t)({emitExpr typeMap arg1}))"
          let rhs := s!"((uint64_t)({emitExpr typeMap arg2}))"
          let fullMask := if width >= 64 then "~0ULL"
            else if width == 1 then "1ULL"
            else s!"((1ULL << {width}) - 1ULL)"
          let signMask := if width == 64 then "0x8000000000000000ULL"
            else s!"(1ULL << {width - 1})"
          let signSet := s!"(({lhs} & {signMask}) != 0ULL)"
          -- Avoid signed right-shift and oversized-shift corner cases.  For a
          -- nonzero in-range amount, logical shift the payload and explicitly
          -- OR the high sign-fill mask.  Every shift below is then < 64.
          let inRange :=
            s!"(({rhs} == 0ULL) ? ({lhs} & {fullMask}) : " ++
            s!"((({lhs} >> {rhs}) | ({signSet} ? ({fullMask} << ({width} - {rhs})) : 0ULL)) & {fullMask}))"
          s!"(({rhs} >= {width}) ? ({signSet} ? {fullMask} : 0ULL) : {inRange})"
      | .lt_s | .le_s | .gt_s | .ge_s =>
        let w := inferExprWidth typeMap arg1
        let stype := signedCastType w
        s!"(({stype}){emitExpr typeMap arg1} {emitCppOperator operator} ({stype}){emitExpr typeMap arg2} ? 1 : 0)"
      | .udiv =>
        let lhs := emitExpr typeMap arg1
        let rhs := emitExpr typeMap arg2
        s!"(({rhs} == 0) ? 0 : ({lhs} / {rhs}))"
      | .sdiv =>
        let w := inferExprWidth typeMap arg1
        let stype := signedCastType w
        let lhs := s!"({stype}){emitExpr typeMap arg1}"
        let rhs := s!"({stype}){emitExpr typeMap arg2}"
        s!"(({rhs} == 0) ? 0 : ({lhs} / {rhs}))"
      | .eq | .lt_u | .le_u | .gt_u | .ge_u =>
        s!"({emitExpr typeMap arg1} {emitCppOperator operator} {emitExpr typeMap arg2} ? 1 : 0)"
      | _ =>
        s!"({emitExpr typeMap arg1} {emitCppOperator operator} {emitExpr typeMap arg2})"
    | _ => s!"/* ERROR: operator with wrong arity */"

/-- Emit one at-most-32-bit chunk at an arbitrary bit offset in the wide packed
    representation.  A chunk can touch at most two storage words. -/
private def emitPassiveWideChunkAssign (lhs value : String)
    (bitOffset chunkWidth : Nat) : List String :=
  let wordIndex := bitOffset / 32
  let wordOffset := bitOffset % 32
  let lowWidth := min chunkWidth (32 - wordOffset)
  let lowMask := if lowWidth == 32 then "0xffffffffULL"
    else s!"((1ULL << {lowWidth}) - 1ULL)"
  let lowLine :=
    s!"        {sanitizeName lhs}[{wordIndex}] |= (uint32_t)((({value}) & {lowMask}) << {wordOffset});"
  if chunkWidth ≤ lowWidth then
    [lowLine]
  else
    let highWidth := chunkWidth - lowWidth
    let highMask := if highWidth == 32 then "0xffffffffULL"
      else s!"((1ULL << {highWidth}) - 1ULL)"
    [lowLine,
     s!"        {sanitizeName lhs}[{wordIndex + 1}] |= (uint32_t)((({value}) >> {lowWidth}) & {highMask});"]

/-- Split one concat argument into little-endian at-most-32-bit chunks.  Scalar
    arguments are evaluated in a masked uint64 carrier.  A wide argument is
    necessarily a validated reference to another passive word container. -/
private def passiveWideArgChunks (typeMap : TypeMap)
    (arg : Expr) : List (Nat × Nat × String) :=
  let width := passiveExprWidth typeMap arg
  let chunkCount := (width + 31) / 32
  if width ≤ 64 then
    -- Cast through the IR leaf's scalar carrier before widening to uint64_t.
    -- Native C++ widths (8/16/32) rely on that cast for modular truncation;
    -- non-native widths additionally need their explicit mask.
    let scalarType := emitCppType (.bitVector (.literal width))
    let casted := s!"((uint64_t)(({scalarType})({emitExpr typeMap arg})))"
    let value := applyMask casted width
    (List.range chunkCount).map fun chunkIndex =>
      let sourceOffset := chunkIndex * 32
      let chunkWidth := min 32 (width - sourceOffset)
      let chunkValue := if sourceOffset == 0 then value
        else s!"(({value}) >> {sourceOffset})"
      (sourceOffset, chunkWidth, chunkValue)
  else
    match arg with
    | .ref source =>
        (List.range chunkCount).map fun chunkIndex =>
          let sourceOffset := chunkIndex * 32
          let chunkWidth := min 32 (width - sourceOffset)
          (sourceOffset, chunkWidth,
            s!"((uint64_t){sanitizeName source}[{chunkIndex}])")
    | _ => []

/-- Emit a passive concat into the wide packed representation.  Concat
    arguments are ordered MSB-to-LSB in the IR, while the C++ array and JIT
    output ABI expose word zero first, so emission walks the arguments in
    reverse order.  This is only bit packing: validation admits scalar leaves
    and already-materialized wide references, never wide computation. -/
def emitPassiveWideConcatAssign (typeMap : TypeMap)
    (lhs : String) (targetWidth : Nat) (args : List Expr) : List String :=
  let wordCount := (targetWidth + 31) / 32
  let initial := (List.range wordCount).map fun wordIndex =>
    s!"        {sanitizeName lhs}[{wordIndex}] = 0;"
  let (_, assignments) := args.reverse.foldl (fun (bitOffset, lines) arg =>
    let width := passiveExprWidth typeMap arg
    let chunkLines := (passiveWideArgChunks typeMap arg).flatMap fun
      (sourceOffset, chunkWidth, value) =>
        emitPassiveWideChunkAssign lhs value (bitOffset + sourceOffset) chunkWidth
    (bitOffset + width, lines ++ chunkLines)
  ) (0, initial)
  assignments

/-- Parts of a C++ class generated from a single statement -/
structure StmtParts where
  declarations    : List String
  evalBody        : List String
  tickBody        : List String
  resetBody       : List String
  evalTickLocals  : List String   -- _next local decls for evalTick()

instance : Append StmtParts where
  append a b :=
    { declarations := a.declarations ++ b.declarations
    , evalBody := a.evalBody ++ b.evalBody
    , tickBody := a.tickBody ++ b.tickBody
    , resetBody := a.resetBody ++ b.resetBody
    , evalTickLocals := a.evalTickLocals ++ b.evalTickLocals }

def StmtParts.empty : StmtParts :=
  { declarations := [], evalBody := [], tickBody := [], resetBody := [], evalTickLocals := [] }

/-- Emit a C++ constant expression for an init value with given width -/
def emitInitValue (initValue : Int) (width : Nat) : String :=
  let cppType := emitCppType (.bitVector width)
  if initValue < 0 then
    let modulus : Int := (2 : Int) ^ width
    let unsigned := ((initValue % modulus) + modulus) % modulus
    s!"({cppType})0x{Nat.toDigits 16 unsigned.toNat |> String.ofList}ULL"
  else
    s!"({cppType}){initValue}ULL"

/-- Split a statement into declaration/eval/tick/reset parts -/
def emitStmt (stmt : Stmt) (typeMap : TypeMap)
    (design : Option Design := none) : StmtParts :=
  match stmt with
  | .assign lhs rhs =>
    let width := lookupWidth typeMap lhs
    if width > 64 then
      let evalBody :=
        if isSupportedPassiveWideAssignment typeMap lhs width rhs then
          match rhs with
          | .ref source => [s!"        {sanitizeName lhs} = {sanitizeName source};"]
          | .concat args => emitPassiveWideConcatAssign typeMap lhs width args
          | _ => [s!"#error \"Sparkle CppSim internal passive-wide assignment mismatch\""]
        else
          [s!"#error \"Sparkle CppSim cannot execute {width}-bit assignment '{sanitizeName lhs}'\""]
      { declarations := []
      , evalBody
      , tickBody := []
      , resetBody := []
      , evalTickLocals := [] }
    else
      let expr := emitExpr typeMap rhs
      let masked := if exprIsMasked width rhs then expr else applyMask expr width
      { declarations := []
      , evalBody := [s!"        {sanitizeName lhs} = {masked};"]
      , tickBody := []
      , resetBody := []
      , evalTickLocals := [] }

  | .signedDot output lhs rhs laneCount lhsWidth rhsWidth resultWidth =>
    let lanes := laneCount.toNat?.getD 0
    let lhsW := lhsWidth.toNat?.getD 0
    let rhsW := rhsWidth.toNat?.getD 0
    let accW := resultWidth.toNat?.getD 0
    let outputName := sanitizeName output
    let indexName := sanitizeName (output ++ "_dot_index")
    let laneExpr (packed : Expr) (width : Nat) : String :=
      let shifted := s!"((uint64_t)({emitExpr typeMap packed}) >> ({indexName} * {width}))"
      if width == 64 then shifted else s!"({shifted} & ((1ULL << {width}) - 1))"
    let lhsSigned := emitSignedValue (laneExpr lhs lhsW) lhsW
    let rhsSigned := emitSignedValue (laneExpr rhs rhsW) rhsW
    let rawUpdate := s!"((int64_t){emitSignedValue outputName accW} + ((int64_t){lhsSigned} * (int64_t){rhsSigned}))"
    let updated := applyMask rawUpdate accW
    let body :=
      s!"        {outputName} = 0;\n" ++
      s!"        for (size_t {indexName} = 0; {indexName} < {lanes}; ++{indexName}) " ++
      ob ++ "\n" ++
      s!"            {outputName} = {updated};\n" ++
      cb
    { declarations := []
    , evalBody := [body]
    , tickBody := []
    , resetBody := []
    , evalTickLocals := [] }

  | .register output _clock _reset input initValue =>
    let width := lookupWidth typeMap output
    let cppType := emitCppType (.bitVector width)
    let outName := sanitizeName output
    let nextName := s!"{outName}_next"
    let rawExpr := emitExpr typeMap input
    let inputExpr := if exprIsMasked width input then rawExpr else applyMask rawExpr width
    let initExpr := emitInitValue initValue width
    { declarations := [s!"    {cppType} {outName};", s!"    {cppType} {nextName};"]
    , evalBody := [s!"        {nextName} = {inputExpr};"]
    , tickBody := [s!"        {outName} = {nextName};"]
    , resetBody := [s!"        {outName} = {initExpr};"]
    , evalTickLocals := [s!"        {cppType} {nextName};"] }

  | .memory name addrWidth dataWidth depth _clock writeAddr writeData writeEnable readAddr readData comboRead =>
    match addrWidth.toNat?, dataWidth.toNat?, depth.toNat? with
    | some _concreteAddrWidth, some _concreteDataWidth, some concreteDepth =>
      let memSize := concreteDepth
      let elemType := emitCppType (.bitVector dataWidth)
      let memName := sanitizeName name
      let rdName := sanitizeName readData
      let memDecl := "    std::array<" ++ elemType ++ ", " ++ toString memSize ++ "> " ++ memName ++ ";"
      -- Declare rdName if not already in typeMap (e.g. unused memory read port)
      let rdType := emitCppType (.bitVector dataWidth)
      let rdInTypeMap := typeMap.contains readData
      let rdDecl := if rdInTypeMap then [] else [s!"    {rdType} {rdName};"]
      let readIndex := emitExpr typeMap readAddr
      let writeIndex := emitExpr typeMap writeAddr
      let writeCondition := emitExpr typeMap writeEnable
      let writeValue := emitExpr typeMap writeData
      if comboRead then
        { declarations := [memDecl] ++ rdDecl
        , evalBody := [s!"        {rdName} = ((uint64_t)({readIndex}) < {memSize}) ? {memName}[{readIndex}] : ({rdType})0;"]
        , tickBody := [s!"        if (({writeCondition}) && (uint64_t)({writeIndex}) < {memSize}) {memName}[{writeIndex}] = {writeValue};"]
        , resetBody := [s!"        {memName}.fill(0);", s!"        {rdName} = ({rdType})0;"]
        , evalTickLocals := [] }
      else
        let addrLatch := s!"{memName}_raddr"
        let addrType := emitCppType (.bitVector addrWidth)
        { declarations := [memDecl, s!"    {addrType} {addrLatch};"] ++ rdDecl
        , evalBody := [s!"        {addrLatch} = {readIndex};"]
        , tickBody :=
            [ s!"        {rdName} = ((uint64_t)({addrLatch}) < {memSize}) ? {memName}[{addrLatch}] : ({rdType})0;"
            , s!"        if (({writeCondition}) && (uint64_t)({writeIndex}) < {memSize}) {memName}[{writeIndex}] = {writeValue};" ]
        , resetBody := [s!"        {memName}.fill(0);", s!"        {addrLatch} = ({addrType})0;", s!"        {rdName} = ({rdType})0;"]
        , evalTickLocals := [] }
    | _, _, _ =>
      { declarations := ["#error \"Sparkle CppSim symbolic memory dimensions require specialization\""]
      , evalBody := []
      , tickBody := []
      , resetBody := []
      , evalTickLocals := [] }

  | .inst moduleName instName connections parameterOverrides =>
    let className := sanitizeName moduleName
    let iName := sanitizeName instName
    -- Look up sub-module in design to determine input/output ports
    let subModule := design.bind fun (d : Design) => d.findModule moduleName
    let outputPortNames : List String := match subModule with
      | some sm => sm.outputs.map fun (p : Port) => p.name
      | none => []
    let inputConns := connections.filterMap fun (portName, expr) =>
      if !outputPortNames.contains portName then
        some s!"        {iName}.{sanitizeName portName} = {emitExpr typeMap expr};"
      else none
    let outputConns := connections.filterMap fun (portName, expr) =>
      if outputPortNames.contains portName then
        match expr with
        | .ref wireName => some s!"        {sanitizeName wireName} = {iName}.{sanitizeName portName};"
        | _ => none
      else none
    let unsupportedOverride := if parameterOverrides.isEmpty then [] else
      ["#error \"Sparkle CppSim instance parameter overrides require specialization\""]
    { declarations := unsupportedOverride ++ [s!"    {className} {iName};"]
    , evalBody := inputConns ++ [s!"        {iName}.eval();"] ++ outputConns
    , tickBody := [s!"        {iName}.tick();"]
    , resetBody := [s!"        {iName}.reset();"]
    , evalTickLocals := [] }

/-- Collect all wire name references from an IR expression -/
partial def collectExprRefs : Expr → List String
  | .ref name => [name]
  | .const _ _ => []
  | .paramConst _ _ => []
  | .resize _ value => collectExprRefs value
  | .slice inner _ _ => collectExprRefs inner
  | .concat args => args.flatMap collectExprRefs
  | .op _ args => args.flatMap collectExprRefs
  | .index arr idx => collectExprRefs arr ++ collectExprRefs idx

/-- Collect all wire names referenced outside `eval()` by memory state updates.
    Memory write operands and synchronous read addresses are consumed by `tick()`.
    Every memory read-data value is also assigned by `reset()`, including a
    combinational read-data wire, so it must remain a class member even when it
    is omitted from `observableWires`. -/
def collectTickRefWires (body : List Stmt) : List String :=
  body.flatMap fun stmt =>
    match stmt with
    | .memory _ _ _ _ _ wa wd we ra rd cr =>
      let exprs := if !cr then [wa, wd, we, ra] else [wa, wd, we]
      -- `reset()` initializes `rd` for both read modes; a local declaration
      -- inside `eval()` would therefore leave the reset assignment undeclared.
      let refs := exprs.flatMap collectExprRefs ++ [rd]
      refs.map sanitizeName
    | _ => []

/-- Emit a complete C++ class for a module -/
def emitModule (m : Module) (design : Option Design := none)
    (observableWires : Option (List String) := none) : String :=
  if let some message := moduleDimensionError? m then
    s!"#error \"{dimensionError m message}\"\n"
  else if moduleRequiresSpecialization m then
    emitSpecializationError m
  else if let some message := moduleResizeError? m then
    s!"#error \"{message}\"\n"
  else if m.isPrimitive then
    s!"// Primitive module: {m.name}\n// (blackbox - not generated)\n\n"
  else
    let typeMap := buildTypeMap m
    let className := sanitizeName m.name

    -- Collect all StmtParts
    let allParts := m.body.map (emitStmt · typeMap design)

    -- Input port declarations
    let inputDecls := m.inputs.map fun (p : Port) =>
      s!"    {emitCppType p.ty} {sanitizeName p.name};"

    -- Output port declarations
    let outputDecls := m.outputs.map fun (p : Port) =>
      s!"    {emitCppType p.ty} {sanitizeName p.name};"

    -- Internal wire declarations (excluding ports and register outputs)
    let portNames : Std.HashSet String := Std.HashSet.ofList <|
      (m.inputs ++ m.outputs).map fun (p : Port) => p.name
    let registerNames : Std.HashSet String := Std.HashSet.ofList <|
      m.body.filterMap fun s => match s with
        | .register output .. => some output
        | _ => none
    let internalWires := m.wires.filter fun (w : Port) =>
      !portNames.contains w.name && !registerNames.contains w.name

    -- Partition into member wires (observable/JIT) and local wires
    -- Wires referenced in tick()/reset bodies must always be class members.
    let tickRefs : Std.HashSet String := Std.HashSet.ofList (collectTickRefWires m.body)
    let observableNames := observableWires.map Std.HashSet.ofList
    let memberWires := match observableNames with
      | some ws => internalWires.filter fun (w : Port) =>
          let sn := sanitizeName w.name
          ws.contains sn || tickRefs.contains sn
      | none => internalWires.filter fun (w : Port) =>
          let sn := sanitizeName w.name
          sn.startsWith "_gen_" || tickRefs.contains sn
    -- Collect memory names to avoid declaring them as local scalars
    let memoryNames : Std.HashSet String := Std.HashSet.ofList <|
      m.body.filterMap fun s => match s with
        | .memory name _ _ _ _ _ _ _ _ _ _ => some (sanitizeName name)
        | _ => none
    let localWires := match observableNames with
      | some ws => internalWires.filter fun (w : Port) =>
          let sn := sanitizeName w.name
          !ws.contains sn && !tickRefs.contains sn && !memoryNames.contains sn
      | none => internalWires.filter fun (w : Port) =>
          let sn := sanitizeName w.name
          !sn.startsWith "_gen_" && !tickRefs.contains sn && !memoryNames.contains sn

    let wireDecls := memberWires.map fun (p : Port) =>
      s!"    {emitCppType p.ty} {sanitizeName p.name};"

    -- Local variable declarations (emitted inside eval())
    let localDecls := localWires.map fun (p : Port) =>
      s!"        {emitCppType p.ty} {sanitizeName p.name};"

    -- Extra declarations from statements (registers, memories, sub-instances)
    let stmtDecls := allParts.flatMap fun p => p.declarations

    -- Eval/tick/reset bodies
    let evalBody := allParts.flatMap fun p => p.evalBody
    let tickBody := allParts.flatMap fun p => p.tickBody
    let resetBody := allParts.flatMap fun p => p.resetBody
    let evalTickLocals := allParts.flatMap fun p => p.evalTickLocals

    -- Assemble the class
    let header := s!"// Generated by Sparkle HDL - C++ Simulation Model\n// Module: {m.name}\n\n"
    let classOpen := "class " ++ className ++ " {\npublic:\n"

    let inputSection := if inputDecls.isEmpty then "" else
      "    // Input ports\n" ++ String.intercalate "\n" inputDecls ++ "\n\n"

    let outputSection := if outputDecls.isEmpty then "" else
      "    // Output ports\n" ++ String.intercalate "\n" outputDecls ++ "\n\n"

    let wireSection := if wireDecls.isEmpty then "" else
      "    // Internal wires\n" ++ String.intercalate "\n" wireDecls ++ "\n\n"

    let stmtDeclSection := if stmtDecls.isEmpty then "" else
      "    // Registers and memories\n" ++ String.intercalate "\n" stmtDecls ++ "\n\n"

    let constructor := "    " ++ className ++ "() { reset(); }\n\n"

    let resetMethod :=
      "    void reset() {\n" ++
      (if resetBody.isEmpty then "" else String.intercalate "\n" resetBody ++ "\n") ++
      "    }\n\n"

    let evalMethod :=
      "    void eval() {\n" ++
      (if localDecls.isEmpty then "" else String.intercalate "\n" localDecls ++ "\n") ++
      (if evalBody.isEmpty then "" else String.intercalate "\n" evalBody ++ "\n") ++
      "    }\n\n"

    let tickMethod :=
      "    void tick() {\n" ++
      (if tickBody.isEmpty then "" else String.intercalate "\n" tickBody ++ "\n") ++
      "    }\n\n"

    let evalTickMethod :=
      "    void evalTick() {\n" ++
      (if evalTickLocals.isEmpty then "" else
        "        // Register next-state (local for register promotion)\n" ++
        String.intercalate "\n" evalTickLocals ++ "\n") ++
      (if localDecls.isEmpty then "" else String.intercalate "\n" localDecls ++ "\n") ++
      (if evalBody.isEmpty then "" else String.intercalate "\n" evalBody ++ "\n") ++
      (if tickBody.isEmpty then "" else String.intercalate "\n" tickBody ++ "\n") ++
      "    }\n"

    let classClose := "};\n"

    header ++ classOpen ++ inputSection ++ outputSection ++ wireSection ++
    stmtDeclSection ++ constructor ++ resetMethod ++ evalMethod ++ tickMethod ++
    evalTickMethod ++ classClose

/-- Convert a single module to C++ simulation code with includes -/
def toCppSim (m : Module) : String :=
  let includes := "#include <cstdint>\n#include <array>\n#include <cstring>\n\n"
  includes ++ emitModule m

/-- Checked C++ entry point for callers that want an error before writing or
    compiling generated source.  `toCppSim` remains source-compatible and emits
    a C++ `#error` directive for the same condition. -/
def toCppSimChecked (m : Module)
    (maxMemoryDepth : Nat := DimExpr.maxNatWorkWidth) : Except String String := do
  if let some message := moduleDimensionError? m then
    throw (dimensionError m message)
  else if moduleRequiresSpecialization m then
    throw (specializationError m)
  else if let some message := moduleResizeError? m then
    throw message
  else
    validateSpecializedDesign { topModule := m.name, modules := [m] } maxMemoryDepth
    pure (toCppSim m)

/-- Convert a full design to C++ simulation code -/
def toCppSimDesign (d : Design)
    (observableWires : Option (List String) := none) : String :=
  let header := "#include <cstdint>\n#include <array>\n#include <cstring>\n\n"
  -- Emit sub-modules before top module (dependency order)
  let topName := d.topModule
  let subModules := d.modules.filter fun (m : Module) => m.name != topName
  let topModule := d.modules.find? fun (m : Module) => m.name == topName
  let subCode := subModules.map (emitModule · (some d))
  let topCode := match topModule with
    | some m => [emitModule m (some d) observableWires]
    | none => []
  header ++ String.intercalate "\n" (subCode ++ topCode)

def toCppSimDesignChecked (d : Design)
    (observableWires : Option (List String) := none)
    (maxMemoryDepth : Nat := DimExpr.maxNatWorkWidth) : Except String String := do
  match d.modules.findSome? (fun module =>
      moduleDimensionError? module |>.map fun message => (module, message)) with
  | some (module, message) => throw (dimensionError module message)
  | none =>
    match d.modules.find? moduleRequiresSpecialization with
    | some module => throw (specializationError module)
    | none =>
      match d.modules.findSome? moduleResizeError? with
      | some message => throw message
      | none =>
        validateSpecializedDesign d maxMemoryDepth
        pure (toCppSimDesign d observableWires)

/-- Collect memory entries from a module's body (name, addrWidth, dataWidth, exact depth). -/
private def collectMemories (body : List Stmt) : List (String × Nat × Nat × Nat) :=
  body.filterMap fun stmt =>
    match stmt with
    | .memory name addrWidth dataWidth depth .. =>
      match addrWidth.toNat?, dataWidth.toNat?, depth.toNat? with
      | some concreteAddrWidth, some concreteDataWidth, some concreteDepth =>
        some (name, concreteAddrWidth, concreteDataWidth, concreteDepth)
      | _, _, _ => none
    | _ => none

/-- Collect (sanitizedName, width) for all registers ≤64 bits -/
private def collectRegisters (body : List Stmt) (typeMap : TypeMap)
    : List (String × Nat) :=
  body.filterMap fun stmt =>
    match stmt with
    | .register output .. =>
      let width := lookupWidth typeMap output
      if width ≤ 64 then some (sanitizeName output, width) else none
    | _ => none

/-- Generate jit_set_reg switch cases -/
private def emitSetRegSwitch (regs : List (String × Nat)) : String :=
  let indexed := (List.range regs.length).zip regs
  let cases := indexed.map fun (i, sName, width) =>
    let cppType := emitCppType (.bitVector width)
    s!"            case {i}: s->{sName} = ({cppType})val; break;"
  String.intercalate "\n" cases

/-- Generate jit_get_reg switch cases -/
private def emitGetRegSwitch (regs : List (String × Nat)) : String :=
  let indexed := (List.range regs.length).zip regs
  let cases := indexed.map fun (i, sName, _width) =>
    s!"            case {i}: return (uint64_t)s->{sName};"
  String.intercalate "\n" cases

/-- Generate jit_reg_name switch cases -/
private def emitRegNameSwitch (regs : List (String × Nat)) : String :=
  let indexed := (List.range regs.length).zip regs
  let cases := indexed.map fun (i, sName, _width) =>
    s!"            case {i}: return \"{sName}\";"
  String.intercalate "\n" cases

/-- Generate set_input switch cases from Module.inputs (skip clk only) -/
private def emitSetInputSwitch (inputs : List Port) : String :=
  let userInputs := inputs.filter fun (p : Port) =>
    p.name != "clk"
  let indexed := (List.range userInputs.length).zip userInputs
  let cases := indexed.map fun (i, p) =>
    let sName := sanitizeName p.name
    let cppType := emitCppType p.ty
    s!"            case {i}: s->{sName} = ({cppType})val; break;"
  String.intercalate "\n" cases

/-- Generate get_output switch cases from Module.outputs -/
private def emitGetOutputSwitch (outputs : List Port) : String :=
  -- For wide packed outputs (array), expose each 32-bit element
  -- For scalar outputs, return directly
  let cases := outputs.foldl (fun (acc : List String × Nat) (p : Port) =>
    let sName := sanitizeName p.name
    let w := p.ty.bitWidth?.getD 0
    if w > 64 then
      -- Wide array output: expose each 32-bit element
      let nWords := (w + 31) / 32
      let wordCases := List.range nWords |>.map fun j =>
        s!"            case {acc.2 + j}: return (uint64_t)s->{sName}[{j}];"
      (acc.1 ++ wordCases, acc.2 + nWords)
    else
      let cast := s!"(uint64_t)s->{sName}"
      (acc.1 ++ [s!"            case {acc.2}: return {cast};"], acc.2 + 1)
  ) ([], 0)
  String.intercalate "\n" cases.1

/-- Count total output slots (wide outputs expand to multiple slots) -/
private def countOutputSlots (outputs : List Port) : Nat :=
  outputs.foldl (fun acc p =>
    let w := p.ty.bitWidth?.getD 0
    if w > 64 then acc + (w + 31) / 32 else acc + 1
  ) 0

/-- Get the filtered list of named wires (observable or _gen_ prefix, ≤64 bits) -/
private def getNamedWires (wires : List Port)
    (observableWires : Option (List String) := none) : List Port :=
  match observableWires with
  | some ws => wires.filter fun (w : Port) =>
      ws.contains (sanitizeName w.name) && w.ty.bitWidth?.getD 0 ≤ 64
  | none => wires.filter fun (w : Port) =>
      (sanitizeName w.name).startsWith "_gen_" && w.ty.bitWidth?.getD 0 ≤ 64

/-- The public JIT value ABI transports one scalar through `uint64_t`.
    CppSim classes may contain unpacked arrays, but the generated setters and
    getters cannot cast an unpacked `std::array` to or from that scalar.  Check
    exactly the top-level values exposed by the wrapper and fail before
    emitting C++ that cannot compile. -/
def validateJITValueABI (d : Design)
    (observableWires : Option (List String) := none) : Except String Unit := do
  match d.modules.find? fun module_ => module_.name == d.topModule with
  | none => pure ()
  | some module_ =>
      for input in module_.inputs do
        match input.ty with
        | .array _ _ =>
            throw s!"Sparkle CppSim JIT input '{module_.name}.{input.name}' is an unpacked array, but jit_set_input supports only scalar packed values"
        | _ => pure ()
      for output in module_.outputs do
        match output.ty with
        | .array _ _ =>
            throw s!"Sparkle CppSim JIT output '{module_.name}.{output.name}' is an unpacked array, but jit_get_output supports only scalar packed values"
        | _ => pure ()
      for wire in getNamedWires module_.wires observableWires do
        match wire.ty with
        | .array _ _ =>
            throw s!"Sparkle CppSim JIT observable wire '{module_.name}.{wire.name}' is an unpacked array, but jit_get_wire supports only scalar packed values"
        | _ => pure ()

/-- Generate get_wire switch for named internal wires (observable or _gen_ prefix, ≤64 bits) -/
private def emitGetWireSwitch (wires : List Port)
    (observableWires : Option (List String) := none) : String × Nat :=
  let namedWires := getNamedWires wires observableWires
  let indexed := (List.range namedWires.length).zip namedWires
  let cases := indexed.map fun (i, p) =>
    let sName := sanitizeName p.name
    s!"            case {i}: return (uint64_t)s->{sName};"
  (String.intercalate "\n" cases, namedWires.length)

/-- Generate wire_name switch (returns wire name by index for discovery) -/
private def emitWireNameSwitch (wires : List Port)
    (observableWires : Option (List String) := none) : String :=
  let namedWires := getNamedWires wires observableWires
  let indexed := (List.range namedWires.length).zip namedWires
  let cases := indexed.map fun (i, p) =>
    let sName := sanitizeName p.name
    s!"            case {i}: return \"{sName}\";"
  String.intercalate "\n" cases

/-- Generate memory access switch cases from Module.body -/
private def emitMemoryAccessSwitches (body : List Stmt) :
    String × String × Nat :=
  let mems := collectMemories body
  let indexed := (List.range mems.length).zip mems
  let setCases := indexed.map fun (i, name, _addrWidth, _dataWidth, depth) =>
    let sName := sanitizeName name
    s!"            case {i}: if (addr < {depth}) s->{sName}[addr] = data; break;"
  let getCases := indexed.map fun (i, name, _addrWidth, _dataWidth, depth) =>
    let sName := sanitizeName name
    s!"            case {i}: return addr < {depth} ? (uint32_t)s->{sName}[addr] : 0;"
  ( String.intercalate "\n" setCases
  , String.intercalate "\n" getCases
  , mems.length )

/-- Generate jit_memset_word switch cases from Module.body -/
private def emitMemsetWordSwitch (body : List Stmt) : String :=
  let mems := collectMemories body
  let indexed := (List.range mems.length).zip mems
  let cases := indexed.map fun (i, name, _addrWidth, _dataWidth, depth) =>
    let sName := sanitizeName name
    s!"            case {i}: for (uint32_t k = 0; k < count && (uint64_t)addr + k < {depth}; k++) s->{sName}[addr + k] = val; break;"
  String.intercalate "\n" cases

/-- Generate self-contained JIT wrapper .cpp from a Design -/
def toCppSimJIT (d : Design)
    (observableWires : Option (List String) := none) : String :=
  -- Generate the CppSim class code (reuse existing, with observableWires for member/local partitioning)
  let classCode := toCppSimDesign d observableWires
  -- Find top module for port/wire introspection
  let topModule := d.modules.find? fun (m : Module) => m.name == d.topModule
  match topModule with
  | none => classCode ++ "\n// ERROR: top module not found\n"
  | some m =>
    let className := sanitizeName m.name
    let userInputs := m.inputs.filter fun (p : Port) =>
      p.name != "clk"
    let numInputs := userInputs.length
    let numOutputs := countOutputSlots m.outputs
    let setInputCases := emitSetInputSwitch m.inputs
    let getOutputCases := emitGetOutputSwitch m.outputs
    let (wireSwitch, numWires) := emitGetWireSwitch m.wires observableWires
    let wireNameSwitch := emitWireNameSwitch m.wires observableWires
    let (memSetCases, memGetCases, numMems) :=
      emitMemoryAccessSwitches m.body
    let memsetWordCases := emitMemsetWordSwitch m.body
    let typeMap := buildTypeMap m
    let regs := collectRegisters m.body typeMap
    let numRegs := regs.length
    let setRegCases := emitSetRegSwitch regs
    let getRegCases := emitGetRegSwitch regs
    let regNameCases := emitRegNameSwitch regs
    -- Assemble extern "C" wrapper
    classCode ++
    "\n// ============================================================\n" ++
    "// Auto-generated JIT FFI wrapper\n" ++
    "// ============================================================\n\n" ++
    s!"extern \"C\" {ob}\n\n" ++
    s!"void* jit_create() {ob} return new {className}(); {cb}\n" ++
    s!"void  jit_destroy(void* ctx) {ob} delete static_cast<{className}*>(ctx); {cb}\n" ++
    s!"void  jit_reset(void* ctx) {ob} static_cast<{className}*>(ctx)->reset(); {cb}\n" ++
    s!"void  jit_eval(void* ctx)  {ob} static_cast<{className}*>(ctx)->eval(); {cb}\n" ++
    s!"void  jit_tick(void* ctx)  {ob} static_cast<{className}*>(ctx)->tick(); {cb}\n" ++
    s!"void  jit_eval_tick(void* ctx) {ob} static_cast<{className}*>(ctx)->evalTick(); {cb}\n\n" ++
    s!"void jit_set_input(void* ctx, uint32_t idx, uint64_t val) {ob}\n" ++
    s!"    auto* s = static_cast<{className}*>(ctx);\n" ++
    s!"    switch (idx) {ob}\n" ++
    setInputCases ++ "\n" ++
    s!"    {cb}\n" ++
    s!"{cb}\n\n" ++
    s!"uint64_t jit_get_output(void* ctx, uint32_t idx) {ob}\n" ++
    s!"    auto* s = static_cast<{className}*>(ctx);\n" ++
    s!"    switch (idx) {ob}\n" ++
    getOutputCases ++ "\n" ++
    s!"    {cb}\n" ++
    s!"    return 0;\n" ++
    s!"{cb}\n\n" ++
    s!"uint64_t jit_get_wire(void* ctx, uint32_t idx) {ob}\n" ++
    s!"    auto* s = static_cast<{className}*>(ctx);\n" ++
    s!"    switch (idx) {ob}\n" ++
    wireSwitch ++ "\n" ++
    s!"    {cb}\n" ++
    s!"    return 0;\n" ++
    s!"{cb}\n\n" ++
    s!"void jit_set_mem(void* ctx, uint32_t mem_idx, uint32_t addr, uint32_t data) {ob}\n" ++
    s!"    auto* s = static_cast<{className}*>(ctx);\n" ++
    s!"    switch (mem_idx) {ob}\n" ++
    memSetCases ++ "\n" ++
    s!"    {cb}\n" ++
    s!"{cb}\n\n" ++
    s!"uint32_t jit_get_mem(void* ctx, uint32_t mem_idx, uint32_t addr) {ob}\n" ++
    s!"    auto* s = static_cast<{className}*>(ctx);\n" ++
    s!"    switch (mem_idx) {ob}\n" ++
    memGetCases ++ "\n" ++
    s!"    {cb}\n" ++
    s!"    return 0;\n" ++
    s!"{cb}\n\n" ++
    s!"void jit_memset_word(void* ctx, uint32_t mem_idx, uint32_t addr, uint32_t val, uint32_t count) {ob}\n" ++
    s!"    auto* s = static_cast<{className}*>(ctx);\n" ++
    s!"    switch (mem_idx) {ob}\n" ++
    memsetWordCases ++ "\n" ++
    s!"    {cb}\n" ++
    s!"{cb}\n\n" ++
    s!"const char* jit_wire_name(uint32_t idx) {ob}\n" ++
    s!"    switch (idx) {ob}\n" ++
    wireNameSwitch ++ "\n" ++
    s!"    {cb}\n" ++
    s!"    return \"\";\n" ++
    s!"{cb}\n\n" ++
    s!"uint32_t jit_num_inputs()   {ob} return {numInputs}; {cb}\n" ++
    s!"uint32_t jit_num_outputs()  {ob} return {numOutputs}; {cb}\n" ++
    s!"uint32_t jit_num_wires()    {ob} return {numWires}; {cb}\n" ++
    s!"uint32_t jit_num_memories() {ob} return {numMems}; {cb}\n\n" ++
    s!"void jit_set_reg(void* ctx, uint32_t reg_idx, uint64_t val) {ob}\n" ++
    s!"    auto* s = static_cast<{className}*>(ctx);\n" ++
    s!"    switch (reg_idx) {ob}\n" ++
    setRegCases ++ "\n" ++
    s!"    {cb}\n" ++
    s!"{cb}\n\n" ++
    s!"uint64_t jit_get_reg(void* ctx, uint32_t reg_idx) {ob}\n" ++
    s!"    auto* s = static_cast<{className}*>(ctx);\n" ++
    s!"    switch (reg_idx) {ob}\n" ++
    getRegCases ++ "\n" ++
    s!"    {cb}\n" ++
    s!"    return 0;\n" ++
    s!"{cb}\n\n" ++
    s!"const char* jit_reg_name(uint32_t idx) {ob}\n" ++
    s!"    switch (idx) {ob}\n" ++
    regNameCases ++ "\n" ++
    s!"    {cb}\n" ++
    s!"    return \"\";\n" ++
    s!"{cb}\n\n" ++
    s!"uint32_t jit_num_regs() {ob} return {numRegs}; {cb}\n\n" ++
    s!"void* jit_snapshot(void* ctx) {ob}\n" ++
    s!"    return new {className}(*static_cast<{className}*>(ctx));\n" ++
    s!"{cb}\n\n" ++
    s!"void jit_restore(void* ctx, void* snap) {ob}\n" ++
    s!"    *static_cast<{className}*>(ctx) = *static_cast<{className}*>(snap);\n" ++
    s!"{cb}\n\n" ++
    s!"void jit_free_snapshot(void* snap) {ob}\n" ++
    s!"    delete static_cast<{className}*>(snap);\n" ++
    s!"{cb}\n\n" ++
    s!"{cb} // extern \"C\"\n"

/-- Checked JIT entry point.  Parameterized designs cannot be reflected through
    the fixed-width uint64_t JIT ABI until they have been specialized. -/
def toCppSimJITChecked (d : Design)
    (observableWires : Option (List String) := none)
    (maxMemoryDepth : Nat := DimExpr.maxNatWorkWidth) : Except String String := do
  match d.modules.findSome? (fun module =>
      moduleDimensionError? module |>.map fun message => (module, message)) with
  | some (module, message) => throw (dimensionError module message)
  | none =>
    match d.modules.find? moduleRequiresSpecialization with
    | some module => throw (specializationError module)
    | none =>
      match d.modules.findSome? moduleResizeError? with
      | some message => throw message
      | none =>
        validateSpecializedDesign d maxMemoryDepth
        validateJITMemoryABI d
        validateJITValueABI d observableWires
        pure (toCppSimJIT d observableWires)

end Sparkle.Backend.CppSim
