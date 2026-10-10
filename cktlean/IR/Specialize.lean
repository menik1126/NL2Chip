/-
  Concrete specialization for symbolic-parameter IR.

  Backends such as CppSim operate on fixed C++ ABI types. This pass evaluates
  every retained dimension for one explicit parameter configuration, unrolls
  symbolic generate loops, and removes parameter bindings before those
  backends run.
-/

import cktlean.IR.AST

namespace cktlean.IR.Specialize

open cktlean.IR.AST
open cktlean.IR.Type

abbrev Bindings := List (String × Nat)


def bindValue (bindings : Bindings) (name : String) (value : Nat) : Except String Bindings :=
  match bindings.lookup name with
  | none => .ok ((name, value) :: bindings)
  | some existing =>
    if existing == value then .ok bindings
    else .error s!"conflicting specializations for parameter '{name}': {existing} and {value}"


def requireDimension (bindings : Bindings) (context : String) (dimension : DimExpr) : Except String Nat := do
  match dimension.evaluate bindings with
  | some value => return value
  | none => throw s!"could not specialize {context} dimension '{dimension}'"


partial def specializeType (bindings : Bindings) (context : String) : HWType → Except String HWType
  | .bit => return .bit
  | .bitVector width =>
    if width == 0 then throw s!"{context} has zero width" else return .bitVector width
  | .bitVectorDim dimension => do
    let width ← requireDimension bindings context dimension
    if width == 0 then throw s!"{context} specializes to zero width"
    return .bitVector width
  | .array size elementType => do
    if size == 0 then throw s!"{context} has zero array size"
    return .array size (← specializeType bindings s!"{context} element" elementType)
  | .arrayDim dimension elementType => do
    let size ← requireDimension bindings s!"{context} array size" dimension
    if size == 0 then throw s!"{context} specializes to zero array size"
    return .array size (← specializeType bindings s!"{context} element" elementType)


def specializePort (bindings : Bindings) (moduleName : String) (port : Port) : Except String Port := do
  return {
    port with
    ty := ← specializeType bindings s!"module '{moduleName}' port '{port.name}'" port.ty
  }


partial def specializeExpr (bindings : Bindings) (indices : Bindings) : Expr → Except String Expr
  | .const value width => return .const value width
  | .constDim value width => do
    let concreteWidth ← requireDimension bindings "constant width" width
    return .const value concreteWidth
  | .dimension value => do
    let concreteValue ← requireDimension (indices ++ bindings)
      "generate-time value" value
    return .const (Int.ofNat concreteValue) 32
  | .ref name =>
    match indices.lookup name with
    | some value => return .const (Int.ofNat value) 32
    | none =>
      match bindings.lookup name with
      | some value => return .const (Int.ofNat value) 32
      | none => return .ref name
  | .op operator args =>
    return .op operator (← args.mapM (specializeExpr bindings indices))
  | .concat args =>
    return .concat (← args.mapM (specializeExpr bindings indices))
  | .slice expr hi lo =>
    return .slice (← specializeExpr bindings indices expr) hi lo
  | .sliceDim expr hi lo => do
    let dimensionBindings := indices ++ bindings
    let concreteHi ← requireDimension dimensionBindings "slice high bound" hi
    let concreteLo ← requireDimension dimensionBindings "slice low bound" lo
    if concreteHi < concreteLo then
      throw s!"specialized slice has descending width [{concreteHi}:{concreteLo}]"
    return .slice (← specializeExpr bindings indices expr) concreteHi concreteLo
  | .index array index =>
    return .index
      (← specializeExpr bindings indices array)
      (← specializeExpr bindings indices index)


partial def specializeStmt
    (bindings : Bindings) (indices : Bindings) : Stmt → Except String (List Stmt)
  | .assign lhs rhs =>
    return [.assign lhs (← specializeExpr bindings indices rhs)]
  | .assignExpr lhs rhs =>
    return [.assignExpr
      (← specializeExpr bindings indices lhs)
      (← specializeExpr bindings indices rhs)]
  | .generateFor label index start stop body => do
    let concreteStart ← requireDimension bindings s!"generate loop '{label}' start" start
    let concreteStop ← requireDimension bindings s!"generate loop '{label}' stop" stop
    if concreteStop < concreteStart then
      throw s!"generate loop '{label}' specializes to invalid range [{concreteStart}, {concreteStop})"
    let mut result : List Stmt := []
    for offset in List.range (concreteStop - concreteStart) do
      let iteration := concreteStart + offset
      for statement in body do
        result := result ++ (← specializeStmt bindings ((index, iteration) :: indices) statement)
    return result
  | .signedDot output lhs rhs laneCount lhsWidth rhsWidth resultWidth => do
    let concreteLanes ← requireDimension bindings "signed dot-product lane count" laneCount
    let concreteLhsWidth ← requireDimension bindings "signed dot-product lhs lane width" lhsWidth
    let concreteRhsWidth ← requireDimension bindings "signed dot-product rhs lane width" rhsWidth
    let concreteResultWidth ← requireDimension bindings "signed dot-product result width" resultWidth
    if concreteLanes == 0 || concreteLhsWidth == 0 || concreteRhsWidth == 0 ||
        concreteResultWidth == 0 then
      throw "signed dot product specializes to a zero dimension"
    return [.signedDot output
      (← specializeExpr bindings indices lhs)
      (← specializeExpr bindings indices rhs)
      (.literal concreteLanes) (.literal concreteLhsWidth)
      (.literal concreteRhsWidth) (.literal concreteResultWidth)]
  | .cdc output sourceDomain destDomain input kind =>
    return [.cdc output sourceDomain destDomain
      (← specializeExpr bindings indices input) kind]
  | .register output clock reset input initValue =>
    return [.register output clock reset
      (← specializeExpr bindings indices input)
      (← specializeExpr bindings indices initValue)]
  | .memory name addrWidth dataWidth clock writeAddr writeData writeEnable
      readAddr readData comboRead => do
    let concreteAddr ← requireDimension bindings s!"memory '{name}' address width" addrWidth
    let concreteData ← requireDimension bindings s!"memory '{name}' data width" dataWidth
    if concreteAddr == 0 || concreteData == 0 then
      throw s!"memory '{name}' specializes to a zero dimension"
    return [.memory name (.literal concreteAddr) (.literal concreteData) clock
      (← specializeExpr bindings indices writeAddr)
      (← specializeExpr bindings indices writeData)
      (← specializeExpr bindings indices writeEnable)
      (← specializeExpr bindings indices readAddr)
      readData comboRead]
  | .asyncMemory name addrWidth dataWidth writeDomain writeAddr writeData
      writeEnable readDomain readAddr readData => do
    let concreteAddr ← requireDimension bindings
      s!"async memory '{name}' address width" addrWidth
    let concreteData ← requireDimension bindings
      s!"async memory '{name}' data width" dataWidth
    if concreteAddr == 0 || concreteData == 0 then
      throw s!"async memory '{name}' specializes to a zero dimension"
    return [.asyncMemory name (.literal concreteAddr) (.literal concreteData)
      writeDomain
      (← specializeExpr bindings indices writeAddr)
      (← specializeExpr bindings indices writeData)
      (← specializeExpr bindings indices writeEnable)
      readDomain (← specializeExpr bindings indices readAddr) readData]
  | .inst moduleName instName connections parameterBindings domainMap => do
    for (name, dimension) in parameterBindings do
      let _ ← requireDimension bindings s!"instance '{instName}' parameter '{name}'" dimension
    let concreteConnections ← connections.mapM fun (name, expression) => do
      return (name, ← specializeExpr bindings indices expression)
    let suffix := indices.foldl (fun acc (index, value) =>
      acc ++ "_" ++ index ++ "_" ++ toString value
    ) ""
    return [.inst moduleName (instName ++ suffix) concreteConnections [] domainMap]


def collectInstanceBindings (design : Design) (initial : Bindings) : Except String Bindings := do
  let mut bindings := initial
  -- Parameter propagation is acyclic for a legal module hierarchy. Repeating
  -- once per module is sufficient while keeping diagnostics deterministic.
  for _ in List.range (design.modules.length + 1) do
    for module in design.modules do
      for statement in module.body do
        match statement with
        | .inst _ _instName _ parameterBindings _domainMap =>
          for (name, dimension) in parameterBindings do
            match dimension.evaluate bindings with
            | some value => bindings ← bindValue bindings name value
            | none => pure ()
        | _ => pure ()
  return bindings


def completeModuleDefaults (design : Design) (initial : Bindings) : Except String Bindings := do
  let mut bindings := initial
  for module in design.modules do
    for parameter in module.parameters do
      if bindings.lookup parameter.name |>.isNone then
        bindings ← bindValue bindings parameter.name parameter.defaultValue
  return bindings


def assignedBitTargets (body : List Stmt) : List String :=
  body.foldl (fun targets statement =>
    match statement with
    | .assignExpr (.index (.ref name) _) _ =>
      if targets.contains name then targets else targets ++ [name]
    | .assignExpr (.slice (.ref name) hi lo) _ =>
      if hi == lo && !targets.contains name then targets ++ [name] else targets
    | _ => targets
  ) []


def initializeAssignedBits (wires : List Port) (body : List Stmt) : List Stmt :=
  let targets := assignedBitTargets body
  let initializers := targets.filterMap fun name =>
    match wires.find? (·.name == name) with
    | some wire => some (.assign name (.const 0 wire.ty.bitWidth))
    | none => none
  initializers ++ body


def specializeModule (bindings : Bindings) (module : Module) : Except String Module := do
  let inputs ← module.inputs.mapM (specializePort bindings module.name)
  let outputs ← module.outputs.mapM (specializePort bindings module.name)
  let wires ← module.wires.mapM (specializePort bindings module.name)
  let mut body : List Stmt := []
  for statement in module.body do
    body := body ++ (← specializeStmt bindings [] statement)
  let assertions ← module.assertions.mapM fun (name, expression) => do
    return (name, ← specializeExpr bindings [] expression)
  return {
    module with
    parameters := []
    inputs := inputs
    outputs := outputs
    wires := wires
    body := initializeAssignedBits wires body
    assertions := assertions
  }


def specializeDesign (design : Design) (requested : Bindings) : Except String Design := do
  let top ← match design.findModule design.topModule with
    | some module => pure module
    | none => throw s!"top module '{design.topModule}' is missing from design"
  for parameter in top.parameters do
    if requested.lookup parameter.name |>.isNone then
      throw s!"missing required top-level CppSim specialization '{parameter.name}'"
  let propagated ← collectInstanceBindings design requested
  let bindings ← completeModuleDefaults design propagated
  let modules ← design.modules.mapM (specializeModule bindings)
  return { design with modules := modules }

end cktlean.IR.Specialize
