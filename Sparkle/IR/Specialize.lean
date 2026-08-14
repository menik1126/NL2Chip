/-
  Concrete specialization for parameterized hardware designs.

  A SystemVerilog parameter belongs to a module instance, not globally to a
  module name.  Consequently, specialization is a hierarchy-aware
  monomorphization pass: the same source child is cloned when two instances
  bind it to different concrete environments.
-/

import Sparkle.IR.AST

namespace Sparkle.IR.Specialize

open Sparkle.IR.AST Sparkle.IR.Type

/-- Concrete bindings supplied for the top module.  Values omitted from this
    list use the corresponding module declaration's default. -/
abbrev ParameterBindings := List (String × Nat)

private structure CacheEntry where
  sourceName : String
  bindings : ParameterBindings
  specializedName : String

private structure SpecializationState where
  modules : List Module := []
  cache : List CacheEntry := []
  usedNames : List String := []
  nextId : Nat := 0

/-- Keep clone names collision-free for both current text backends.  Their
    identifier normalizers intentionally agree on these substitutions. -/
private def emittedIdentifier (name : String) : String :=
  name.replace "." "_"
    |>.replace "-" "_"
    |>.replace " " "_"
    |>.replace "'" "_prime"
    |>.replace "#" ""

private def findModuleChecked (design : Design) (name : String) : Except String Module :=
  match design.modules.filter (·.name == name) with
  | [] => .error s!"specialization refers to missing module '{name}'"
  | [module_] => .ok module_
  | _ => .error s!"design contains more than one module named '{name}'"

private def rejectDuplicateNames (role : String)
    (bindings : List (String × α)) : Except String Unit := do
  for (name, _) in bindings do
    if bindings.countP (fun entry => entry.1 == name) > 1 then
      throw s!"{role} contains duplicate binding for parameter '{name}'"

/-- Resolve a partial binding list in module-declaration order. -/
private def resolveBindings (module_ : Module) (role : String)
    (supplied : ParameterBindings) : Except String ParameterBindings := do
  rejectDuplicateNames role supplied
  for (name, _) in supplied do
    unless module_.parameters.any (fun parameter => parameter.name == name) do
      throw s!"{role} supplies unknown parameter '{name}' for module '{module_.name}'"
  for parameter in module_.parameters do
    if module_.parameters.countP (fun other => other.name == parameter.name) > 1 then
      throw s!"module '{module_.name}' declares duplicate parameter '{parameter.name}'"
  return module_.parameters.map fun parameter =>
    (parameter.name, (supplied.lookup parameter.name).getD parameter.defaultValue)

private def lookupNat (bindings : ParameterBindings) (name : String) : Option Nat :=
  bindings.lookup name

private def lookupDim (bindings : ParameterBindings) (name : String) : Option DimExpr :=
  (lookupNat bindings name).map .literal

private def cacheLookup (state : SpecializationState) (sourceName : String)
    (bindings : ParameterBindings) : Option String :=
  (state.cache.find? fun entry =>
    entry.sourceName == sourceName && entry.bindings == bindings).map (·.specializedName)

private partial def findUnusedName (sourceName : String) (usedNames : List String)
    (nextId : Nat) : String × Nat :=
  let candidate := s!"{sourceName}__specialized_{nextId}"
  if usedNames.any (fun used =>
      used == candidate || emittedIdentifier used == emittedIdentifier candidate) then
    findUnusedName sourceName usedNames (nextId + 1)
  else
    (candidate, nextId + 1)

private def freshCloneName (sourceName : String)
    (state : SpecializationState) : String × SpecializationState :=
  let (name, nextId) := findUnusedName sourceName state.usedNames state.nextId
  (name, { state with usedNames := name :: state.usedNames, nextId })

private partial def validateSliceOrder (role : String) : Expr → Except String Unit
  | .slice inner hi lo => do
      match hi.toNat?, lo.toNat? with
      | some concreteHi, some concreteLo =>
          if concreteHi < concreteLo then
            throw s!"{role} contains reversed slice [{concreteHi}:{concreteLo}]"
      | _, _ => pure ()
      validateSliceOrder role inner
  | .op _ args | .concat args => args.forM (validateSliceOrder role)
  | .resize _ value => validateSliceOrder role value
  | .index array index =>
      validateSliceOrder role array *> validateSliceOrder role index
  | .const _ _ | .ref _ => pure ()

private def validateConcreteModule (module_ : Module) : Except String Unit := do
  unless module_.parameters.isEmpty do
    throw s!"specialized module '{module_.name}' still declares parameters"
  for dimension in module_.dimensionExpressions do
    unless dimension.isConcrete do
      throw s!"specialized module '{module_.name}' retains symbolic dimension '{dimension}'"
  for statement in module_.body do
    match statement with
    | .inst _ instanceName connections overrides =>
        unless overrides.isEmpty do
          throw s!"specialized instance '{module_.name}.{instanceName}' retains parameter overrides"
        for (portName, connection) in connections do
          validateSliceOrder
            s!"specialized instance '{module_.name}.{instanceName}' connection '{portName}'"
            connection
    | .assign lhs rhs =>
        validateSliceOrder s!"specialized assignment '{module_.name}.{lhs}'" rhs
    | .register output _ _ input _ =>
        validateSliceOrder s!"specialized register '{module_.name}.{output}'" input
    | .memory name _ _ _ writeAddr writeData writeEnable readAddr _ _ =>
        for (portRole, expr) in
            [("write address", writeAddr), ("write data", writeData),
             ("write enable", writeEnable), ("read address", readAddr)] do
          validateSliceOrder
            s!"specialized memory '{module_.name}.{name}' {portRole}" expr
  for (assertionName, assertion) in module_.assertions do
    validateSliceOrder
      s!"specialized assertion '{module_.name}.{assertionName}'" assertion
  module_.validateDimensions

private def validateConcreteDesign (design : Design) : Except String Unit := do
  for module_ in design.modules do
    if design.modules.countP (fun other => other.name == module_.name) > 1 then
      throw s!"specialization generated duplicate module name '{module_.name}'"
    if let some conflict := design.modules.find? fun other =>
        other.name != module_.name &&
          emittedIdentifier other.name == emittedIdentifier module_.name then
      throw s!"specialized module names '{module_.name}' and '{conflict.name}' both emit as '{emittedIdentifier module_.name}'"
    validateConcreteModule module_
    for statement in module_.body do
      match statement with
      | .inst childName instanceName _ _ =>
          unless design.modules.any (fun child => child.name == childName) do
            throw s!"specialized instance '{module_.name}.{instanceName}' refers to missing module '{childName}'"
      | _ => pure ()
  unless design.modules.any (fun module_ => module_.name == design.topModule) do
    throw s!"specialized design is missing top module '{design.topModule}'"

/-- Specialize one source module and every child reachable from it.  Modules
    are accumulated in dependency order so concrete backends can emit child
    classes/modules before their parents. -/
private partial def specializeModule (design : Design) (sourceName : String)
    (supplied : ParameterBindings) (isTop : Bool) (ancestors : List String)
    (state : SpecializationState) : Except String (String × SpecializationState) := do
  if ancestors.contains sourceName then
    throw s!"recursive module hierarchy encountered while specializing '{sourceName}'"

  let source ← findModuleChecked design sourceName
  let bindings ← resolveBindings source s!"specialization of module '{sourceName}'" supplied

  if let some specializedName := cacheLookup state sourceName bindings then
    return (specializedName, state)

  if source.isPrimitive && !source.parameters.isEmpty then
    throw s!"parameterized primitive module '{sourceName}' cannot be concretely cloned; provide a concrete primitive wrapper"

  let (specializedName, state) :=
    if isTop || source.parameters.isEmpty then (source.name, state)
    else freshCloneName source.name state
  let base := source.substituteDimensions (lookupDim bindings)

  let rec specializeBody (remaining : List Stmt) (reversed : List Stmt)
      (state : SpecializationState) : Except String (List Stmt × SpecializationState) := do
    match remaining with
    | [] => return (reversed.reverse, state)
    | statement :: rest =>
      match statement with
      | .inst childName instanceName connections overrides =>
          let child ← findModuleChecked design childName
          rejectDuplicateNames
            s!"parameter overrides on instance '{sourceName}.{instanceName}'" overrides
          for (name, _) in overrides do
            unless child.parameters.any (fun parameter => parameter.name == name) do
              throw s!"instance '{sourceName}.{instanceName}' overrides unknown parameter '{name}' of module '{childName}'"
          let concreteOverrides ← overrides.mapM fun (name, value) =>
            match value.toNat? with
            | some concrete => pure (name, concrete)
            | none =>
              throw s!"instance '{sourceName}.{instanceName}' retains unresolved override '{name} = {value}'"
          let childBindings ← resolveBindings child
            s!"parameter overrides on instance '{sourceName}.{instanceName}'" concreteOverrides
          let (specializedChild, state) ← specializeModule design childName childBindings
            false (sourceName :: ancestors) state
          let rewritten := .inst specializedChild instanceName connections []
          specializeBody rest (rewritten :: reversed) state
      | other => specializeBody rest (other :: reversed) state

  let (body, state) ← specializeBody base.body [] state
  let specialized : Module :=
    { base with name := specializedName, parameters := [], body }
  validateConcreteModule specialized
  let state :=
    { state with
      modules := state.modules ++ [specialized]
      cache := state.cache ++ [{ sourceName, bindings, specializedName }] }
  return (specializedName, state)

/--
  Monomorphize the reachable hierarchy at one concrete top-level parameter
  environment.

  Supplied bindings are matched only against the top module; omitted bindings
  use its declared defaults.  Each instance override is then evaluated in its
  parent's concrete environment.  The result contains only reachable modules,
  has no parameter declarations or instance overrides, and contains no
  symbolic or zero-valued hardware dimensions.
-/
def specializeDesign (design : Design)
    (parameters : ParameterBindings := []) : Except String Design := do
  for module_ in design.modules do
    if design.modules.countP (fun other => other.name == module_.name) > 1 then
      throw s!"design contains more than one module named '{module_.name}'"
  let top ← findModuleChecked design design.topModule
  let topBindings ← resolveBindings top
    s!"top-level specialization of '{design.topModule}'" parameters
  let initial : SpecializationState :=
    { usedNames := design.modules.map (·.name) }
  let (specializedTop, state) ← specializeModule design design.topModule topBindings
    true [] initial
  let result : Design := { topModule := specializedTop, modules := state.modules }
  validateConcreteDesign result
  return result

/-- Use every declared top-level default. -/
def specializeDesignWithDefaults (design : Design) : Except String Design :=
  specializeDesign design []

end Sparkle.IR.Specialize
