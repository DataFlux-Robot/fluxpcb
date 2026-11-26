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
  /-- Claim ids the conjecture requires to be discharged.  Coverage is
      checked here so "the theorem covers the conjecture" is not a Python
      claim: an obligation missing from the proven/carried set fails. -/
  obligations : Array String
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
  let obligations ← optIdArr j "obligations"
  let before ← match j.getObjVal? "before" with
    | .ok v => do
        let s ← stateOf v
        pure (some s)
    | .error _ => pure none
  pure { state, claims, certs, carried, touched, before, obligations }

def hasDup (ids : List String) : Bool :=
  match ids with
  | [] => false
  | x :: xs => xs.contains x || hasDup xs

def verify (r : Request) : Json :=
  let drift := r.state.digest != digestOf r.state
  let driftBefore := match r.before with
    | some b => b.digest != digestOf b
    | none => false
  let dupIds := hasDup (r.claims.toList.map (fun c => c.id))
  let carriedIds := r.carried.map (fun c => c.id)
  let carriedOk := r.carried.all (fun c => scopeDisjoint c.scope r.touched)
  let frame := match r.before with
    | some b => frameOk b r.state r.touched (r.carried.map (fun c => (c.id, c.scope)))
    | none => true
  let rec go (claims : List Claim) (proven : List String)
      (acc : List Json) (ok : Bool) : List Json × List String × Bool :=
    match claims with
    | [] => (acc.reverse, proven, ok)
    | c :: cs =>
      let cert? := r.certs.find? (fun x => x.claim == c.id)
      let carried? := r.carried.find? (fun x => x.id == c.id)
      let (st0, w0) :=
        match cert?, carried? with
        | none, some ca =>
            if ca.scope == c.scope then
              ("carried", "reused proof: scope untouched")
            else
              ("unknown", "carried scope differs from the claim's scope")
        | _, _ => statusOf r.state c cert?
      let depsOk := c.deps.all (fun d => proven.contains d || carriedIds.contains d)
      let (st, w) :=
        if (st0 == "proven" || st0 == "carried") && !depsOk then
          ("unknown", "a dependency is not proven")
        else (st0, w0)
      let proven := if st == "proven" || st == "carried" then c.id :: proven else proven
      let ok := ok && (st == "proven" || st == "carried" || st == "foreign")
      let entry := Json.mkObj [
        ("id", Json.str c.id), ("pred", Json.str c.pred),
        ("status", Json.str st), ("witness", Json.str w)]
      go cs proven (entry :: acc) ok
  let (entries, proven, claimsOk) := go r.claims.toList [] [] true
  let uncovered := r.obligations.toList.filter
    (fun o => !proven.contains o && !carriedIds.contains o)
  let coverageOk := uncovered.isEmpty
  let ok := claimsOk && !drift && !driftBefore && frame && carriedOk &&
            !dupIds && coverageOk
  Json.mkObj [
    ("digest", Json.str r.state.digest),
    ("computed_digest", Json.str (digestOf r.state)),
    ("canonical", Json.str (canonical r.state)),
    ("drift", Json.bool drift),
    ("drift_before", Json.bool driftBefore),
    ("duplicate_ids", Json.bool dupIds),
    ("frame_ok", Json.bool frame),
    ("carried_ok", Json.bool carriedOk),
    ("coverage_ok", Json.bool coverageOk),
    ("uncovered", Json.arr (uncovered.toArray.map Json.str)),
    ("claims", Json.arr entries.toArray),
    ("ok", Json.bool ok)]

def run (txt : String) : Except String String := do
  let j ← Json.parse txt
  let r ← requestOf j
  pure (Json.compress (verify r))

end Fluxpcb
