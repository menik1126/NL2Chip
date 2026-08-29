/-
  DRC (Design Rule Check) — Registered Output Check

  Warns when output ports are driven by combinational logic rather than registers.
  For backend-friendly RTL (synthesis + STA), outputs should be driven by registers.
-/

import Sparkle.IR.AST

namespace Sparkle.Compiler.DRC

open Sparkle.IR.AST

/-- Find the statement that defines a given wire name. -/
def findDriver (body : List Stmt) (wireName : String) : Option Stmt :=
  body.find? fun
    | .assign lhs _ => lhs == wireName
    | .assignExpr (.ref lhs) _ => lhs == wireName
    | .assignExpr _ _ => false
    | .generateFor .. => false
    | .cdc output .. => output == wireName
    | .register output .. => output == wireName
    | .memory (readData := rd) .. => rd == wireName
    | .asyncMemory (readData := rd) .. => rd == wireName
    | .inst _ instName _ _ _ => instName == wireName

private def declaredDomain? (m : Module) (name : String) : Option DomainId :=
  (m.inputs ++ m.outputs ++ m.wires).find? (fun port => port.name == name)
    |>.bind (·.domain)

/-- Infer all domain owners contributing to an expression. Constants and
    explicitly untyped compatibility ports contribute no owner. -/
partial def inferExprDomains (m : Module) (expr : Expr)
    (visited : List String := []) : List DomainId :=
  match expr with
  | .const .. | .constDim .. | .dimension .. => []
  | .ref name =>
      if visited.contains name then []
      else
        let declared := (declaredDomain? m name).toList
        let driven := match findDriver m.body name with
          | some (.assign _ rhs) => inferExprDomains m rhs (name :: visited)
          | some (.cdc _ _ destDomain _ _) => [destDomain]
          | some (.register _ domain _ _ _) => [domain]
          | some (.memory (domain := domain) ..) => [domain]
          | some (.asyncMemory (readDomain := domain) ..) => [domain]
          | _ => []
        (declared ++ driven).eraseDups
  | .op _ args | .concat args =>
      (args.flatMap (inferExprDomains m · visited)).eraseDups
  | .slice inner _ _ => inferExprDomains m inner visited
  | .sliceDim inner _ _ => inferExprDomains m inner visited
  | .index array index =>
      (inferExprDomains m array visited ++ inferExprDomains m index visited).eraseDups

private def expectedDomainIssues (m : Module) (owner : String)
    (expected : DomainId) (expr : Expr) : List String :=
  let actual := inferExprDomains m expr
  let foreign := actual.filter (· != expected)
  if foreign.isEmpty then []
  else
    [s!"[DRC] Module '{m.name}': {owner} belongs to domain '{expected}' but reads domain(s) '{String.intercalate ", " foreign}' without an explicit CDC"]

/-- Check domain provenance through all typed combinational and sequential data
    paths. An explicit `.cdc` changes ownership to its destination domain. -/
def checkDomainFlows (m : Module) : List String :=
  let bodyIssues := m.body.flatMap fun stmt =>
    match stmt with
    | .assign lhs rhs =>
        let domains := inferExprDomains m rhs
        let mixedIssue :=
          if domains.length > 1 then
            [s!"[DRC] Module '{m.name}': assignment '{lhs}' combines domains '{String.intercalate ", " domains}' without an explicit CDC"]
          else []
        let ownerIssues := match declaredDomain? m lhs with
          | some expected => expectedDomainIssues m s!"assignment '{lhs}'" expected rhs
          | none => []
        mixedIssue ++ ownerIssues
    | .assignExpr lhs rhs =>
        let domains := (inferExprDomains m lhs ++ inferExprDomains m rhs).eraseDups
        if domains.length > 1 then
          [s!"[DRC] Module '{m.name}': indexed assignment combines domains '{String.intercalate ", " domains}' without an explicit CDC"]
        else []
    | .generateFor .. => []
    | .cdc output sourceDomain destDomain input _ =>
        expectedDomainIssues m s!"CDC '{output}' input" sourceDomain input ++
        (match declaredDomain? m output with
         | some declared =>
             if declared == destDomain then []
             else [s!"[DRC] Module '{m.name}': CDC '{output}' is declared in domain '{declared}', expected destination '{destDomain}'"]
         | none => [])
    | .register output domain _ input _ =>
        expectedDomainIssues m s!"register '{output}' input" domain input
    | .memory name _ _ domain writeAddr writeData writeEnable readAddr _ _ =>
        expectedDomainIssues m s!"memory '{name}' write address" domain writeAddr ++
        expectedDomainIssues m s!"memory '{name}' write data" domain writeData ++
        expectedDomainIssues m s!"memory '{name}' write enable" domain writeEnable ++
        expectedDomainIssues m s!"memory '{name}' read address" domain readAddr
    | .asyncMemory name _ _ writeDomain writeAddr writeData writeEnable
        readDomain readAddr _ =>
        expectedDomainIssues m s!"async memory '{name}' write address" writeDomain writeAddr ++
        expectedDomainIssues m s!"async memory '{name}' write data" writeDomain writeData ++
        expectedDomainIssues m s!"async memory '{name}' write enable" writeDomain writeEnable ++
        expectedDomainIssues m s!"async memory '{name}' read address" readDomain readAddr
    | .inst .. => []
  bodyIssues.eraseDups

/-- Validate clock-domain declarations and all sequential references. -/
def checkClockDomains (m : Module) : List String :=
  let duplicateIds := m.clockDomains.filterMap fun domain =>
    let definitions := m.clockDomains.filter (fun other => other.id == domain.id)
    if definitions.length > 1 then
      some s!"[DRC] Module '{m.name}': duplicate clock-domain id '{domain.id}'"
    else none
  let missingRefs := m.body.flatMap fun stmt =>
    let domains := match stmt with
      | .cdc _ sourceDomain destDomain _ _ => [sourceDomain, destDomain]
      | .register _ domain _ _ _ => [domain]
      | .memory _ _ _ domain _ _ _ _ _ _ => [domain]
      | .asyncMemory _ _ _ writeDomain _ _ _ readDomain _ _ =>
          [writeDomain, readDomain]
      | _ => []
    domains.filterMap fun domain =>
      if m.findClockDomain? domain |>.isSome then none
      else some s!"[DRC] Module '{m.name}': statement references unknown domain '{domain}'"
  let invalidCrossings := m.body.filterMap fun stmt =>
    match stmt with
    | .cdc _ sourceDomain destDomain _ _ =>
        if sourceDomain == destDomain then
          some s!"[DRC] Module '{m.name}': CDC source and destination are both '{sourceDomain}'"
        else none
    | .asyncMemory name _ _ writeDomain _ _ _ readDomain _ _ =>
        if writeDomain == readDomain then
          some s!"[DRC] Module '{m.name}': async memory '{name}' uses the same write/read domain '{writeDomain}'"
        else none
    | _ => none
  let ports := m.inputs ++ m.outputs
  let missingPorts := m.clockDomains.flatMap fun domain =>
    let clockIssue :=
      if ports.any (fun port => port.name == domain.clock && port.ty == .bit) then []
      else [s!"[DRC] Module '{m.name}': domain '{domain.id}' clock port '{domain.clock}' is missing or not one bit"]
    let resetIssue := match domain.reset with
      | none => []
      | some reset =>
          if ports.any (fun port => port.name == reset && port.ty == .bit) then []
          else [s!"[DRC] Module '{m.name}': domain '{domain.id}' reset port '{reset}' is missing or not one bit"]
    clockIssue ++ resetIssue
  (duplicateIds.eraseDups ++ missingRefs ++ invalidCrossings ++ missingPorts ++
    checkDomainFlows m).eraseDups

private def connectionExpr? (connections : List (String × Expr))
    (portName : String) : Option Expr :=
  (connections.find? (fun (name, _) => name == portName)).map (·.2)

/-- Validate hierarchical domain mappings and their physical clock/reset and
    typed data-port connections. -/
def checkInstanceDomains (design : Design) : List String :=
  design.modules.flatMap fun parent =>
    parent.body.flatMap fun stmt =>
      match stmt with
      | .inst moduleName instName connections _parameterBindings domainMap =>
          match design.findModule moduleName with
          | none =>
              [s!"[DRC] Module '{parent.name}': instance '{instName}' references unknown module '{moduleName}'"]
          | some child =>
              let mappingIssues := child.clockDomains.flatMap fun childDomain =>
                let mappings := domainMap.filter (fun (childId, _) => childId == childDomain.id)
                match mappings with
                | [] =>
                    [s!"[DRC] Module '{parent.name}': instance '{instName}' has no parent mapping for child domain '{childDomain.id}'"]
                | [(_, parentId)] =>
                    match parent.findClockDomain? parentId with
                    | none =>
                        [s!"[DRC] Module '{parent.name}': instance '{instName}' maps child domain '{childDomain.id}' to unknown parent domain '{parentId}'"]
                    | some parentDomain =>
                        let clockIssues :=
                          match connectionExpr? connections childDomain.clock with
                          | some (.ref clock) =>
                              if clock == parentDomain.clock then []
                              else [s!"[DRC] Module '{parent.name}': instance '{instName}' child clock '{childDomain.clock}' is connected to '{clock}', expected '{parentDomain.clock}'"]
                          | _ =>
                              [s!"[DRC] Module '{parent.name}': instance '{instName}' child clock '{childDomain.clock}' is not connected to parent domain '{parentId}'"]
                        let resetIssues := match childDomain.reset with
                          | none => []
                          | some childReset =>
                              match parentDomain.reset, connectionExpr? connections childReset with
                              | some parentReset, some (.ref reset) =>
                                  if reset == parentReset then []
                                  else [s!"[DRC] Module '{parent.name}': instance '{instName}' child reset '{childReset}' is connected to '{reset}', expected '{parentReset}'"]
                              | _, _ =>
                                  [s!"[DRC] Module '{parent.name}': instance '{instName}' child reset '{childReset}' is not connected to parent domain '{parentId}'"]
                        clockIssues ++ resetIssues
                | _ =>
                    [s!"[DRC] Module '{parent.name}': instance '{instName}' maps child domain '{childDomain.id}' more than once"]
              let extraMappingIssues := domainMap.flatMap fun (childId, parentId) =>
                let childIssue :=
                  if child.findClockDomain? childId |>.isSome then []
                  else [s!"[DRC] Module '{parent.name}': instance '{instName}' maps unknown child domain '{childId}'"]
                let parentIssue :=
                  if parent.findClockDomain? parentId |>.isSome then []
                  else [s!"[DRC] Module '{parent.name}': instance '{instName}' maps to unknown parent domain '{parentId}'"]
                childIssue ++ parentIssue
              let inputIssues := child.inputs.flatMap fun port =>
                match port.domain, connectionExpr? connections port.name with
                | some childId, some expr =>
                    match domainMap.find? (fun (mappedChild, _) => mappedChild == childId) with
                    | some (_, parentId) =>
                        expectedDomainIssues parent
                          s!"instance '{instName}' input '{port.name}'" parentId expr
                    | none => []
                | _, _ => []
              let outputIssues := child.outputs.flatMap fun port =>
                match port.domain, connectionExpr? connections port.name with
                | some childId, some (.ref parentValue) =>
                    match domainMap.find? (fun (mappedChild, _) => mappedChild == childId),
                        declaredDomain? parent parentValue with
                    | some (_, parentId), some declared =>
                        if parentId == declared then []
                        else [s!"[DRC] Module '{parent.name}': instance '{instName}' output '{port.name}' maps to domain '{parentId}' but drives '{parentValue}' in domain '{declared}'"]
                    | _, _ => []
                | _, _ => []
              (mappingIssues ++ extraMappingIssues ++ inputIssues ++ outputIssues).eraseDups
      | _ => []

def checkDesignClockDomains (design : Design) : List String :=
  (design.modules.flatMap checkClockDomains ++ checkInstanceDomains design).eraseDups

/-- Check that all output ports are driven by registers or stateful memory primitives.
    Returns a list of warning strings for violations. -/
def checkRegisteredOutputs (m : Module) : List String :=
  m.outputs.filterMap fun port =>
    -- Skip infrastructure ports
    if port.name == "clk" || port.name == "rst" then
      none
    else
      -- Find the assign statement for this output port
      let assignStmt := m.body.find? fun
        | .assign lhs _ => lhs == port.name
        | _ => false
      match assignStmt with
      | none => none  -- No assign found, skip
      | some (.assign _ rhs) =>
        match rhs with
        | .ref wireName =>
          -- Check what defines this wire
          match findDriver m.body wireName with
          | some (.register ..) => none  -- Registered output, pass
          | some (.memory (comboRead := false) ..) => none  -- Synchronous memory read, pass
          | some (.asyncMemory ..) => none  -- Explicit FWFT async-memory read, pass
          | _ => some s!"[DRC] Module '{m.name}': output '{port.name}' is not driven by a register (driven by wire '{wireName}')"
        | _ => some s!"[DRC] Module '{m.name}': output '{port.name}' is driven by combinational logic"
      | _ => none  -- unreachable

end Sparkle.Compiler.DRC
