import Fluxpcb.Board

open Lean

namespace Fluxpcb

/-- A conjecture: what the requirement asks to be proved.  `connected` and
    `netlist` are Lean's; every other predicate belongs to a peer domain
    kernel (fab/power/thermal/signal) and is reported as `foreign` here. -/
structure Claim where
  id : String
  pred : String
  net : String
  pads : Array String
  scope : Array String
  deps : Array String
  expect : List (String × Array String)
deriving Repr, Inhabited

structure Cert where
  claim : String
  kind : String
  edges : Array (String × String)
deriving Repr, Inhabited

def optStr (j : Json) (k : String) : Except String String :=
  match j.getObjVal? k with
  | .ok v => Json.getStr? v
  | .error _ => pure ""

def optIdArr (j : Json) (k : String) : Except String (Array String) :=
  match j.getObjVal? k with
  | .ok v => Json.getArr? v >>= fun a => a.mapM Json.getStr?
  | .error _ => pure #[]

def optNetPads (j : Json) (k : String) : Except String (List (String × Array String)) :=
  match j.getObjVal? k with
  | .ok v => netPadsOf v
  | .error _ => pure []

def claimOf (j : Json) : Except String Claim := do
  let id ← jStr j "id"
  let pred ← jStr j "pred"
  let net ← optStr j "net"
  let pads ← optIdArr j "pads"
  let scope ← optIdArr j "scope"
  let deps ← optIdArr j "deps"
  let expect ← optNetPads j "expect"
  pure { id, pred, net, pads, scope, deps, expect }

def certOf (j : Json) : Except String Cert := do
  let claim ← jStr j "claim"
  let kind ← optStr j "kind"
  let es ← jArr j "edges"
  let edges ← es.mapM (fun e => do
    let a ← Json.getArr? e
    if a.size != 2 then throw "edge needs two ids"
    let x ← Json.getStr? (a[0]!)
    let y ← Json.getStr? (a[1]!)
    pure (x, y))
  pure { claim, kind, edges }

structure Carried where
  id : String
  scope : Array String
deriving Repr, Inhabited

def carriedOf (j : Json) : Except String Carried := do
  let id ← jStr j "id"
  let scope ← optIdArr j "scope"
  pure { id, scope }

def claimsOf (j : Json) : Except String (Array Claim) := do
  let a ← Json.getArr? j
  a.mapM claimOf

def certsOf (j : Json) : Except String (Array Cert) := do
  let a ← Json.getArr? j
  a.mapM certOf

end Fluxpcb
