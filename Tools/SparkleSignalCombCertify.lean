import Sparkle.Compiler.SignalCombCertificate

namespace Sparkle.SignalCombCertify

open Lean

structure Config where
  nonce : String

private def usage : String :=
  "sparkle-signal-comb-certify --nonce <32-lowercase-hex-digits>"

private def validNonce (nonce : String) : Bool :=
  nonce.length == 32 && nonce.toList.all fun char =>
    ('0' <= char && char <= '9') || ('a' <= char && char <= 'f')

private def parseArgs (args : List String) : Except String Config := do
  let nonce ← match args with
    | ["--nonce", nonce] => pure nonce
    | ["--nonce"] => throw "missing value after option: '--nonce'"
    | [] => throw "missing required option: '--nonce'"
    | flag :: _ => throw s!"unknown or duplicate option: '{flag}'"
  unless validNonce nonce do
    throw "invalid nonce: expected exactly 32 lowercase hexadecimal digits"
  return { nonce }

private def errorJson
    (kind message : String) (nonce : Option String := none) : Json :=
  Json.mkObj [
    ("error_kind", .str kind),
    ("message", .str message),
    ("schema_version", (2 : Json)),
    ("status", .str "error"),
    ("verification_nonce", nonce.map Json.str |>.getD .null)
  ]

private def emitJson (json : Json) : IO Unit :=
  IO.println json.compress

private def runCertificate (config : Config) : IO Json := do
  -- Import only the fixed original-indexed unary/binary checker and its proof
  -- dependencies. No caller-selected module, declaration, theorem, syntax
  -- extension, or Meta reifier is loaded into this kernel environment.
  let env ← Lean.importModules
    #[{ module :=
      Sparkle.Compiler.SignalCombCertificate.certificateModule }]
    {}
    (trustLevel := 0)
    (loadExts := false)
  let coreContext : Lean.Core.Context := {
    fileName := "<sparkle-signal-comb-certify>"
    fileMap := default
    options := ({} : Lean.Options).set `pp.raw true
  }
  let coreState : Lean.Core.State := { env }
  let (certificate, _) ← Lean.Meta.MetaM.toIO
    (Sparkle.Compiler.SignalCombCertificate.certifySignalCombCompilerCorrectness
      (some config.nonce))
    coreContext
    coreState
  return certificate

def run (args : List String) : IO UInt32 := do
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

end Sparkle.SignalCombCertify

def main (args : List String) : IO UInt32 :=
  Sparkle.SignalCombCertify.run args
