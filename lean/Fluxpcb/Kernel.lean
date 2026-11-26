import Fluxpcb.Check
import Fluxpcb.Frame

open Lean

namespace Fluxpcb

structure Request where
  state : State
  claims : Array Claim
  certs : Array Cert
  carried : Array Carried
  touched : Array String
  before : Option State
deriving Inhabited

def requestOf (j : Json) : Except String Request := do
  let state ← (j.getObjVal? "state") >>= stateOf
  let claims ← (j.getObjVal? "claims") >>= claimsOf
  let certs ← match j.getObjVal? "certs" with
    | .ok v => certsOf v
    | .error _ => pure #[]
  let carried ← match j.getObjVal? "carried" with
    | .ok v => do
        let a ← Json.getArr? v
        a.mapM carriedOf
    | .error _ => pure #[]
  let touched ← optIdArr j "touched"
  let before ← match j.getObjVal? "before" with
    | .ok v => do
        let s ← stateOf v
        pure (some s)
    | .error _ => pure none
  pure { state, claims, certs, carried, touched, before }

def verify (r : Request) : Json :=
  let drift := r.state.digest != digestOf r.state
  let carriedIds := r.carried.map (fun c => c.id)
  let carriedOk := r.carried.all (fun c => scopeDisjoint c.scope r.touched)
  let frame := match r.before with
    | some b => frameOk b r.state r.touched (r.carried.map (fun c => (c.id, c.scope)))
    | none => true
  let rec go (claims : List Claim) (proven : List String)
      (acc : List Json) (ok : Bool) : List Json × Bool :=
    match claims with
    | [] => (acc.reverse, ok)
    | c :: cs =>
      let cert? := r.certs.find? (fun x => x.claim == c.id)
      let (st0, w0) := statusOf r.state c cert?
      let depsOk := c.deps.all (fun d => proven.contains d || carriedIds.contains d)
      let (st, w) :=
        if st0 == "proven" && !depsOk then ("unknown", "a dependency is not proven")
        else (st0, w0)
      let proven := if st == "proven" then c.id :: proven else proven
      let ok := ok && (st == "proven" || st == "foreign")
      let entry := Json.mkObj [
        ("id", Json.str c.id), ("pred", Json.str c.pred),
        ("status", Json.str st), ("witness", Json.str w)]
      go cs proven (entry :: acc) ok
  let (entries, claimsOk) := go r.claims.toList [] [] true
  let ok := claimsOk && !drift && frame && carriedOk
  Json.mkObj [
    ("digest", Json.str r.state.digest),
    ("computed_digest", Json.str (digestOf r.state)),
    ("drift", Json.bool drift),
    ("frame_ok", Json.bool frame),
    ("carried_ok", Json.bool carriedOk),
    ("claims", Json.arr entries.toArray),
    ("ok", Json.bool ok)]

def run (txt : String) : Except String String := do
  let j ← Json.parse txt
  let r ← requestOf j
  pure (Json.compress (verify r))

end Fluxpcb
