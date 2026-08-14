import Sparkle.Compiler.Elab

namespace Sparkle.Certify

open Lean

structure Config where
  moduleName : Name
  theoremName : Name
  parameterNames : Array Name
  nonce : String

private def usage : String :=
  "sparkle-certify --module <Module.Name> --theorem <Theorem.Name> " ++
  "--parameters <W[,D,...]> --nonce <32-lowercase-hex-digits>"

private def validNameSegment (segment : String) : Bool :=
  match segment.toList with
  | [] => false
  | first :: rest =>
      (first.isAlpha || first == '_') &&
        rest.all (fun char => char.isAlphanum || char == '_' || char == '\'')

private def parseQualifiedName (kind value : String) : Except String Name := do
  let segments := value.splitOn "."
  unless !segments.isEmpty && segments.all validNameSegment do
    throw s!"invalid {kind}: '{value}'"
  return value.toName

private def parseParameterNames (value : String) : Except String (Array Name) := do
  let segments := value.splitOn ","
  unless !segments.isEmpty && segments.all validNameSegment do
    throw s!"invalid Nat parameter list: '{value}'"
  let names := segments.toArray.map String.toName
  for name in names do
    if names.count name != 1 then
      throw s!"duplicate Nat parameter name: '{name}'"
  return names

private def validNonce (nonce : String) : Bool :=
  nonce.length == 32 && nonce.toList.all fun char =>
    ('0' <= char && char <= '9') || ('a' <= char && char <= 'f')

private structure RawConfig where
  moduleName? : Option String := none
  theoremName? : Option String := none
  parameters? : Option String := none
  nonce? : Option String := none

private def setOnce
    (flag value : String) (current : Option String) : Except String (Option String) := do
  if current.isSome then
    throw s!"duplicate option: '{flag}'"
  return some value

private partial def parseRawArgs
    (args : List String) (config : RawConfig := {}) : Except String RawConfig := do
  match args with
  | [] => return config
  | flag :: value :: rest =>
      match flag with
      | "--module" =>
          parseRawArgs rest { config with
            moduleName? := ← setOnce flag value config.moduleName? }
      | "--theorem" =>
          parseRawArgs rest { config with
            theoremName? := ← setOnce flag value config.theoremName? }
      | "--parameters" =>
          parseRawArgs rest { config with
            parameters? := ← setOnce flag value config.parameters? }
      | "--nonce" =>
          parseRawArgs rest { config with
            nonce? := ← setOnce flag value config.nonce? }
      | _ => throw s!"unknown option: '{flag}'"
  | [flag] => throw s!"missing value after option: '{flag}'"

private def requireOption (flag : String) : Option String → Except String String
  | some value => return value
  | none => throw s!"missing required option: '{flag}'"

private def parseArgs (args : List String) : Except String Config := do
  let raw ← parseRawArgs args
  let moduleName ← parseQualifiedName "module name" (← requireOption "--module" raw.moduleName?)
  let theoremName ← parseQualifiedName "theorem name" (← requireOption "--theorem" raw.theoremName?)
  let parameterNames ← parseParameterNames (← requireOption "--parameters" raw.parameters?)
  let nonce ← requireOption "--nonce" raw.nonce?
  unless validNonce nonce do
    throw "invalid nonce: expected exactly 32 lowercase hexadecimal digits"
  return { moduleName, theoremName, parameterNames, nonce }

private def errorJson
    (kind message : String) (nonce : Option String := none) : Json :=
  Json.mkObj [
    ("error_kind", .str kind),
    ("message", .str message),
    ("schema_version", (1 : Json)),
    ("status", .str "error"),
    ("verification_nonce", nonce.map Json.str |>.getD .null)
  ]

private def emitJson (json : Json) : IO Unit :=
  IO.println json.compress

private def runCertificate (config : Config) : IO Json := do
  -- Do not load persistent environment extensions.  The certifier needs only
  -- kernel declarations from the candidate's `.olean`; no candidate-supplied
  -- syntax, command elaborator, initializer, or extension callback is run.
  let env ← Lean.importModules
    #[{ module := config.moduleName }]
    {}
    (trustLevel := 0)
    (loadExts := false)
  let moduleIdx ← match env.getModuleIdxFor? config.theoremName with
    | some moduleIdx => pure moduleIdx
    | none => throw <| IO.userError s!"'{config.theoremName}' is not present in \
        Lean's kernel-checked environment."
  let declaringModule := env.header.modules[moduleIdx]!.module
  unless declaringModule == config.moduleName do
    throw <| IO.userError s!"Theorem '{config.theoremName}' is declared by module \
      '{declaringModule}', not candidate module '{config.moduleName}'."
  let coreContext : Lean.Core.Context := {
    fileName := "<sparkle-certify>"
    fileMap := default
    -- The raw printer depends only on the kernel expression and does not run
    -- candidate-provided delaborators or pretty-printer extensions.
    options := ({} : Lean.Options).set `pp.raw true
  }
  let coreState : Lean.Core.State := { env }
  let (certificate, _) ← Lean.Meta.MetaM.toIO
    (Sparkle.Compiler.Elab.certifyUniversalTheorem
      config.theoremName config.parameterNames (some config.nonce))
    coreContext
    coreState
  return certificate

def run (args : List String) : IO UInt32 := do
  -- Keep the native binary directly runnable from `.lake/build/bin` while
  -- still honoring any evaluator-supplied `LEAN_PATH` entries.
  let localLeanLib := (← IO.appDir).parent.get! / "lib" / "lean"
  Lean.initSearchPath (← Lean.findSysroot) [localLeanLib]
  let config ← match parseArgs args with
    | .ok config => pure config
    | .error message =>
        emitJson (errorJson "usage" s!"{message}. Usage: {usage}")
        return 2
  try
    emitJson (← runCertificate config)
    return 0
  catch error =>
    emitJson (errorJson "certificate" (toString error) (some config.nonce))
    return 1

end Sparkle.Certify

def main (args : List String) : IO UInt32 :=
  Sparkle.Certify.run args
